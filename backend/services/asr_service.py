import os
import subprocess
import librosa
from transformers import AutoProcessor, pipeline
from optimum.onnxruntime import ORTModelForSpeechSeq2Seq

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MODEL_PATH = os.path.join(BASE_DIR, "models", "whisper-small-int8")

model = None
processor = None
asr_pipeline = None

def load_asr_model():
    global model, processor, asr_pipeline
    if model is None or processor is None or asr_pipeline is None:
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
    Transcribes the given audio file using the INT8 quantized Whisper ONNX model.
    """
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
    # We force language='hi' to prevent hallucinations on noisy/silent segments.
    result = asr_pipeline(speech, generate_kwargs={"language": "hi"})
    
    return result["text"]
