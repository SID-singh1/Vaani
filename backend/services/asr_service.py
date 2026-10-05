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

def _convert_to_wav(file_path: str) -> str:
    """
    Pre-convert any audio format to 16kHz mono WAV using ffmpeg.
    Applies:
    - -fflags +genpts: repairs presentation timestamps in forwarded/shared audio
    - dynaudnorm: dynamic audio normalization so quiet speech is heard clearly
    - silenceremove: strips dead trailing silence to prevent Whisper hallucinations
    Returns path to the normalized WAV file (caller must clean up).
    """
    wav_path = file_path + ".groq.wav"
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-fflags", "+genpts", "-i", file_path,
             "-af", "dynaudnorm=f=150:g=15,silenceremove=stop_periods=-1:stop_duration=1.5:stop_threshold=-45dB",
             "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", wav_path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True
        )
        return wav_path
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    # Fallback without audio filters if dynaudnorm is unsupported on an edge-case container
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-fflags", "+genpts", "-i", file_path,
             "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", wav_path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True
        )
        return wav_path
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"[ASR] ffmpeg conversion failed ({e}), will send original file to Groq")
        return None

def _split_into_chunks(wav_path: str, segment_seconds: int = 40) -> list:
    """
    Splits long audio into ~40s segments to ensure Whisper never drops
    subsequent speech or hits the 30-second early termination bug.
    """
    chunk_dir = wav_path + "_chunks"
    os.makedirs(chunk_dir, exist_ok=True)
    pattern = os.path.join(chunk_dir, "chunk_%03d.wav")
    try:
        subprocess.run([
            "ffmpeg", "-y", "-i", wav_path,
            "-f", "segment", "-segment_time", str(segment_seconds),
            "-c", "copy", pattern
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        chunks = sorted(glob.glob(os.path.join(chunk_dir, "chunk_*.wav")))
        return chunks
    except Exception as e:
        print(f"[ASR] ffmpeg chunking failed ({e}), will transcribe as single file")
        return []

async def transcribe_audio(file_path: str) -> str:
    """
    Transcribes the given audio file.
    If USE_LOCAL_MODELS is true, uses INT8 quantized Whisper ONNX model locally.
    If false, dynamically switches to Groq's blazing fast whisper-large-v3 API.
    Handles long audio files by intelligently chunking into ~40s parallel pieces.
    """
    if not USE_LOCAL_MODELS:
        if not GROQ_API_KEY:
            raise Exception("GROQ_API_KEY is required when USE_LOCAL_MODELS is false.")
        
        print("Transcribing via Groq Whisper API...")
        
        # Pre-convert and normalize to 16kHz WAV
        wav_path = _convert_to_wav(file_path)
        upload_path = wav_path if wav_path else file_path
        
        try:
            duration = _get_audio_duration(upload_path)
            print(f"[ASR] Detected audio duration: {duration:.1f}s")
            
            # If audio is longer than 45 seconds and we have a WAV file, chunk it
            # This completely eliminates Whisper dropping the second half of recordings!
            if duration > 45.0 and wav_path:
                chunks = _split_into_chunks(wav_path, segment_seconds=40)
                if len(chunks) > 1:
                    print(f"[ASR] Long audio ({duration:.1f}s) split into {len(chunks)} chunks. Processing in parallel...")
                    sem = asyncio.Semaphore(2)  # Process up to 2 chunks concurrently
                    
                    async def transcribe_chunk(chunk_file):
                        async with sem:
                            raw = await _call_groq_whisper(chunk_file, "audio.wav", "audio/wav")
                            return _clean_whisper_hallucinations(raw)
                    
                    chunk_transcripts = await asyncio.gather(*(transcribe_chunk(c) for c in chunks))
                    
                    # Cleanup chunk files
                    for c in chunks:
                        if os.path.exists(c):
                            try: os.remove(c)
                            except: pass
                    try:
                        os.rmdir(wav_path + "_chunks")
                    except: pass
                    
                    stitched = " ".join([t for t in chunk_transcripts if t.strip()]).strip()
                    print(f"[ASR] Successfully stitched {len(chunks)} chunks ({len(stitched)} chars).")
                    return stitched

            # Single chunk execution for short audio (<= 45s) or fallback
            if wav_path:
                upload_name = "audio.wav"
                mime_type = "audio/wav"
            else:
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
            
            raw_text = await _call_groq_whisper(upload_path, upload_name, mime_type)
            return _clean_whisper_hallucinations(raw_text)
            
        finally:
            # Clean up the intermediate WAV file
            if wav_path and os.path.exists(wav_path):
                os.remove(wav_path)


async def _call_groq_whisper(upload_path: str, upload_name: str, mime_type: str, max_retries: int = 2) -> str:
    """
    Calls Groq Whisper API with retry logic for transient errors (429, 500, 503).
    Uses a neutral vocabulary guide prompt (not full sentences) so prompt text never leaks.
    """
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            file_size = os.path.getsize(upload_path)
            timeout = max(60.0, min(180.0, file_size / (1024 * 50)))
            
            async with httpx.AsyncClient(timeout=timeout) as client:
                with open(upload_path, "rb") as f:
                    response = await client.post(
                        "https://api.groq.com/openai/v1/audio/transcriptions",
                        headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                        data={
                            "model": "whisper-large-v3",
                            "temperature": "0.0",
                            "response_format": "verbose_json",
                            # Neutral vocabulary guide — does NOT leak complete sentences into transcripts
                            "prompt": "Hinglish, meeting notes, project updates, tasks, review, WhatsApp Hindi."
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
                segments = res_json.get("segments", [])
                
                # Check segments vs full text
                if segments:
                    segment_texts = [seg.get("text", "").strip() for seg in segments if seg.get("text")]
                    stitched = " ".join(segment_texts).strip()
                    full_text = res_json.get("text", "").strip()
                    result = stitched if len(stitched) >= len(full_text) else full_text
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
