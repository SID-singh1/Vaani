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

def _convert_to_wav(file_path: str) -> str:
    """
    Pre-convert any audio format to 16kHz mono WAV using ffmpeg.
    This normalizes browser webm, Telegram oga/opus, and any other format
    into clean PCM audio that Whisper processes most reliably.
    Returns the path to the WAV file (caller must clean up).
    """
    wav_path = file_path + ".groq.wav"
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", file_path,
             "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", wav_path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True
        )
        return wav_path
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"[ASR] ffmpeg conversion failed ({e}), will send original file to Groq")
        return None


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
        
        # Pre-convert to 16kHz WAV for maximum Whisper compatibility.
        # This fixes: browser webm 400 errors, oga metadata issues,
        # and reduces segment skipping in long audio.
        wav_path = _convert_to_wav(file_path)
        upload_path = wav_path if wav_path else file_path
        
        if wav_path:
            upload_name = "audio.wav"
            mime_type = "audio/wav"
        else:
            # Fallback: send original file with best-guess MIME type
            ext = os.path.splitext(file_path)[1].lower()
            mime_map = {
                ".ogg": ("voice.ogg", "audio/ogg"),
                ".oga": ("voice.ogg", "audio/ogg"),
                ".opus": ("voice.opus", "audio/opus"),
                ".mp3": ("audio.mp3", "audio/mpeg"),
                ".mp4": ("video.mp4", "video/mp4"),
                ".wav": ("audio.wav", "audio/wav"),
                ".m4a": ("audio.m4a", "audio/m4a"),
                ".aac": ("audio.m4a", "audio/m4a"),
                ".webm": ("audio.webm", "audio/webm"),
                ".flac": ("audio.flac", "audio/flac"),
            }
            upload_name, mime_type = mime_map.get(ext, ("voice.ogg", "audio/ogg"))
        
        try:
            return await _call_groq_whisper(upload_path, upload_name, mime_type)
        finally:
            # Clean up the intermediate WAV file
            if wav_path and os.path.exists(wav_path):
                os.remove(wav_path)


async def _call_groq_whisper(upload_path: str, upload_name: str, mime_type: str, max_retries: int = 2) -> str:
    """
    Calls Groq Whisper API with retry logic for transient errors (429, 500, 503).
    Logs the actual error body on failures for debugging.
    """
    import asyncio
    
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            # Scale timeout based on file size (larger files need more time)
            file_size = os.path.getsize(upload_path)
            timeout = max(60.0, min(180.0, file_size / (1024 * 50)))  # ~50KB/s minimum
            
            async with httpx.AsyncClient(timeout=timeout) as client:
                with open(upload_path, "rb") as f:
                    response = await client.post(
                        "https://api.groq.com/openai/v1/audio/transcriptions",
                        headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                        data={
                            "model": "whisper-large-v3",
                            "temperature": "0.0",
                            "response_format": "verbose_json",
                            "prompt": "Haan bhai, kal meeting schedule karni hai. We will discuss project updates aur deliverables."
                        },
                        files={"file": (upload_name, f, mime_type)}
                    )
                
                # Log error body before raising — critical for debugging 400s
                if response.status_code != 200:
                    error_body = response.text[:500]
                    print(f"[ASR] Groq returned HTTP {response.status_code}: {error_body}")
                    
                    # Retry on transient errors only
                    if response.status_code in (429, 500, 502, 503) and attempt < max_retries:
                        wait_time = 2 ** attempt  # 1s, 2s
                        print(f"[ASR] Retrying in {wait_time}s (attempt {attempt + 1}/{max_retries})...")
                        await asyncio.sleep(wait_time)
                        continue
                    
                    response.raise_for_status()
                
                res_json = response.json()
                
                # Log segment metadata for debugging completeness issues
                segments = res_json.get("segments", [])
                duration = res_json.get("duration", 0)
                print(f"[ASR] Groq returned {len(segments)} segments, duration={duration:.1f}s")
                
                # Stitch all segments together to ensure conversational pauses don't cause dropped sentences
                if segments:
                    segment_texts = [seg.get("text", "").strip() for seg in segments if seg.get("text")]
                    stitched = " ".join(segment_texts).strip()
                    full_text = res_json.get("text", "").strip()
                    
                    # Use whichever version is more complete
                    result = stitched if len(stitched) >= len(full_text) else full_text
                    print(f"[ASR] Transcript length: stitched={len(stitched)}, full_text={len(full_text)}, using={'stitched' if len(stitched) >= len(full_text) else 'full_text'}")
                    return result
                
                return res_json.get("text", "")
                
        except httpx.TimeoutException as e:
            last_error = e
            if attempt < max_retries:
                wait_time = 2 ** attempt
                print(f"[ASR] Timeout on attempt {attempt + 1}, retrying in {wait_time}s...")
                await asyncio.sleep(wait_time)
                continue
            raise
        except httpx.HTTPStatusError:
            raise  # Already logged above, don't retry 400s
        except Exception as e:
            last_error = e
            if attempt < max_retries:
                print(f"[ASR] Error on attempt {attempt + 1}: {e}, retrying...")
                await asyncio.sleep(1)
                continue
            raise
    
    raise last_error or Exception("Groq Whisper API failed after all retries")

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
