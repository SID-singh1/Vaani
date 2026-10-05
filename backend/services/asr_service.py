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

import re
import glob
import asyncio

def _clean_whisper_hallucinations(text: str) -> str:
    """
    Strips known Whisper outro/silence hallucinations (e.g. 'Thank you for watching',
    'Please subscribe', etc.) that occur when audio has trailing silence or pauses.
    """
    if not text:
        return ""
    hallucination_patterns = [
        r"(?i)\bthank\s+you\s+for\s+watching\b\.?",
        r"(?i)\bthanks\s+for\s+watching\b\.?",
        r"(?i)\bthank\s+you\s+very\s+much\s+for\s+watching\b\.?",
        r"(?i)\bplease\s+(?:like\s+and\s+)?subscribe\b\.?",
        r"(?i)\bsubscribe\s+to\s+(?:our|my|the)\s+channel\b\.?",
        r"(?i)\bsubtitles\s+by\b.*$",
        r"(?i)\bwatching\b\s*$",
    ]
    cleaned = text
    for pat in hallucination_patterns:
        cleaned = re.sub(pat, "", cleaned)
    return re.sub(r'\s+', ' ', cleaned).strip()

def _get_audio_duration(file_path: str) -> float:
    """
    Determines audio duration in seconds using ffprobe (with ffmpeg fallback).
    """
    try:
        res = subprocess.run([
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", file_path
        ], capture_output=True, text=True, check=True)
        return float(res.stdout.strip())
    except Exception:
        try:
            res = subprocess.run(["ffmpeg", "-i", file_path], capture_output=True, text=True)
            match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", res.stderr)
            if match:
                hours, mins, secs = match.groups()
                return int(hours) * 3600 + int(mins) * 60 + float(secs)
        except Exception:
            pass
    return 0.0

def _convert_to_audio_for_groq(file_path: str) -> tuple[str, str, str]:
    """
    Pre-converts audio for Groq Whisper API without destructive filtering.
    Standardizes to 16kHz mono 16-bit PCM WAV (lossless, zero distortion).
    If the WAV exceeds 24 MB (~12.5 min of speech), automatically encodes to 64kbps MP3,
    which safely fits up to 50 minutes of continuous audio within Groq's 25 MB limit.
    Returns (prepared_file_path, upload_name, mime_type).
    """
    wav_path = file_path + ".clean.wav"
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-fflags", "+genpts", "-i", file_path,
             "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", wav_path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True
        )
        size_bytes = os.path.getsize(wav_path)
        # Groq's maximum file size limit is 25 MB (26,214,400 bytes)
        if size_bytes <= 24 * 1024 * 1024:
            return (wav_path, "audio.wav", "audio/wav")

        # For long audio (>24 MB), encode to 64kbps MP3 (fits up to 50 min in single file)
        mp3_path = file_path + ".clean.mp3"
        subprocess.run(
            ["ffmpeg", "-y", "-fflags", "+genpts", "-i", wav_path,
             "-ar", "16000", "-ac", "1", "-c:a", "libmp3lame", "-b:a", "64k", mp3_path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True
        )
        try: os.remove(wav_path)
        except: pass
        return (mp3_path, "audio.mp3", "audio/mpeg")

    except Exception as e:
        print(f"[ASR] ffmpeg conversion failed ({e}), using original file directly")
        if os.path.exists(wav_path):
            try: os.remove(wav_path)
            except: pass

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
        return (file_path, upload_name, mime_type)

async def transcribe_audio(file_path: str) -> str:
    """
    Transcribes the given audio file.
    If USE_LOCAL_MODELS is false, uses Groq's high-speed whisper-large-v3 API in a single,
    continuous pass without destructive chunking or pause clipping.
    If USE_LOCAL_MODELS is true, uses INT8 quantized Whisper ONNX model locally.
    """
    if not USE_LOCAL_MODELS:
        if not GROQ_API_KEY:
            raise Exception("GROQ_API_KEY is required when USE_LOCAL_MODELS is false.")
        
        # Convert to clean 16kHz mono audio (preserves 100% of speech and acoustic boundaries)
        prep_path, upload_name, mime_type = _convert_to_audio_for_groq(file_path)
        
        try:
            duration = _get_audio_duration(prep_path)
            file_mb = os.path.getsize(prep_path) / (1024 * 1024)
            print(f"[ASR] Processing audio via Groq whisper-large-v3 (duration: {duration:.1f}s, size: {file_mb:.2f} MB)...")
            raw_text = await _call_groq_whisper(prep_path, upload_name, mime_type)
            return _clean_whisper_hallucinations(raw_text)
        finally:
            # Clean up temporary converted file if one was created
            if prep_path != file_path and os.path.exists(prep_path):
                try: os.remove(prep_path)
                except: pass


async def _call_groq_whisper(upload_path: str, upload_name: str, mime_type: str, max_retries: int = 2) -> str:
    """
    Calls Groq Whisper API with retry logic for transient errors (429, 500, 503).
    Transcribes in a continuous single pass using whisper-large-v3 for maximum accuracy.
    """
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            file_size = os.path.getsize(upload_path)
            timeout = max(90.0, min(300.0, file_size / (1024 * 30)))
            
            async with httpx.AsyncClient(timeout=timeout) as client:
                with open(upload_path, "rb") as f:
                    response = await client.post(
                        "https://api.groq.com/openai/v1/audio/transcriptions",
                        headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                        data={
                            "model": "whisper-large-v3",
                            "temperature": "0.0",
                            "response_format": "json"
                        },
                        files={"file": (upload_name, f, mime_type)}
                    )
                
                if response.status_code != 200:
                    error_body = response.text[:500]
                    print(f"[ASR] Groq returned HTTP {response.status_code}: {error_body}")
                    
                    if response.status_code in (429, 500, 502, 503) and attempt < max_retries:
                        wait_time = 2 ** attempt
                        print(f"[ASR] Retrying in {wait_time}s (attempt {attempt + 1}/{max_retries})...")
                        await asyncio.sleep(wait_time)
                        continue
                    
                    response.raise_for_status()
                
                res_json = response.json()
                text = res_json.get("text", "").strip()
                return text
                
        except httpx.TimeoutException as e:
            last_error = e
            if attempt < max_retries:
                wait_time = 2 ** attempt
                print(f"[ASR] Timeout on attempt {attempt + 1}, retrying in {wait_time}s...")
                await asyncio.sleep(wait_time)
                continue
            raise
        except httpx.HTTPStatusError:
            raise
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
