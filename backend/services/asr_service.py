import os
import subprocess
import httpx
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MODEL_PATH = os.path.join(BASE_DIR, "models", "whisper-small-int8")
USE_LOCAL_MODELS = os.getenv("USE_LOCAL_MODELS", "true").lower() == "true"
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

model = None
processor = None
asr_pipeline = None

def load_asr_model():
    global model, processor, asr_pipeline
    if model is None or processor is None or asr_pipeline is None:
        from transformers import AutoProcessor, pipeline
        from optimum.onnxruntime import ORTModelForSpeechSeq2Seq
        
        print("Loading Whisper INT8 ONNX model...")
        processor = AutoProcessor.from_pretrained(MODEL_PATH)
        model = ORTModelForSpeechSeq2Seq.from_pretrained(MODEL_PATH)
        
        # Initialize pipeline for automatic chunking of long audio
        asr_pipeline = pipeline(
            "automatic-speech-recognition",
            model=model,
            tokenizer=processor.tokenizer,
            feature_extractor=processor.feature_extractor,
            chunk_length_s=30,
        )
        print("Whisper model loaded!")

async def transcribe_audio(file_path: str) -> str:
    """
    Transcribes the given audio file.
    If USE_LOCAL_MODELS is true, uses INT8 quantized Whisper ONNX model locally.
    If false, dynamically switches to Groq's blazing fast whisper-large-v3 API.
    """
    if not USE_LOCAL_MODELS:
        if not GROQ_API_KEY:
            raise Exception("GROQ_API_KEY is required when USE_LOCAL_MODELS is false.")
        
        print("Transcribing via Groq Whisper API...")
        async with httpx.AsyncClient(timeout=60.0) as client:
            with open(file_path, "rb") as f:
                response = await client.post(
                    "https://api.groq.com/openai/v1/audio/translations",
                    headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                    data={"model": "whisper-large-v3"},
                    files={"file": (os.path.basename(file_path), f, "audio/mpeg")}
                )
            response.raise_for_status()
            return response.json().get("text", "")

    # Local Fallback Execution
    import librosa
    load_asr_model()
    
    # Force convert to 16kHz WAV using ffmpeg to guarantee compatibility (webm, ogg, etc)
    wav_path = file_path + ".wav"
    subprocess.run([
        "ffmpeg", "-y", "-i", file_path, 
        "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", wav_path
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    
    # Load and resample audio (it's already 16kHz WAV now)
    speech, sr = librosa.load(wav_path, sr=16000)
    
    # Cleanup intermediate wav
    if os.path.exists(wav_path):
        os.remove(wav_path)
    
    # Use pipeline which automatically handles long audio (chunk_length_s=30)
    # We command Whisper to translate to English, bypassing the LLM's inability to read Devanagari.
    result = asr_pipeline(speech, generate_kwargs={"task": "translate"})
    
    return result["text"]
