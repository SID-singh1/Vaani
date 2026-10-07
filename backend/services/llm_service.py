import os
import re
import asyncio
import subprocess
import atexit
import httpx
import json
import time
import platform
try:
    import google.generativeai as genai
except ImportError:
    genai = None
from fastapi import HTTPException
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MODELS_DIR = os.path.join(BASE_DIR, "models")
ML_DIR = os.path.join(BASE_DIR, "ml")

USE_LOCAL_MODELS = os.getenv("USE_LOCAL_MODELS", "true").lower() == "true"
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# Determine binary name based on OS (for Docker/Linux compatibility)
EXE_NAME = "llama-server.exe" if platform.system() == "Windows" else "llama-server"
LLAMA_SERVER_EXE = os.path.join(ML_DIR, EXE_NAME)

LLM_MODEL = os.path.join(MODELS_DIR, "phi-3-mini-q4_k_m.gguf")

llama_process = None
LLAMA_SERVER_PORT = 8081

def start_llama_server():
    if not USE_LOCAL_MODELS:
        return
        
    global llama_process
    if llama_process is not None and llama_process.poll() is None:
        return
        
    print("Starting llama-server.exe backend...")
    cmd = [
        LLAMA_SERVER_EXE,
        "-m", LLM_MODEL,
        "-c", "2048",
        "--port", str(LLAMA_SERVER_PORT)
    ]
    llama_process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    # Wait for server to be ready
    import requests
    for i in range(20):
        try:
            res = requests.get(f"http://127.0.0.1:{LLAMA_SERVER_PORT}/health")
            if res.status_code == 200:
                print("Llama server is ready!")
                return
        except requests.ConnectionError:
            pass
        time.sleep(1)
        
    print("Warning: Llama server did not become ready in time!")
        
def stop_llama_server():
    global llama_process
    if llama_process and llama_process.poll() is None:
        print("Stopping llama-server.exe...")
        llama_process.terminate()
        llama_process.wait()
        
atexit.register(stop_llama_server)

async def summarize_transcript(transcript: str) -> dict:
    """
    Summarizes the given transcript.
    If USE_LOCAL_MODELS is true, uses INT4 Phi-3 model locally.
    If false, dynamically switches to Gemini Pro API.
    """
    # Detect if transcript contains Hindi Devanagari script
    has_devanagari = any('\u0900' <= char <= '\u097F' for char in transcript)
    
    system_prompt = (
        "You are an expert bilingual speech and meeting intelligence assistant specializing in Hindi, Hinglish, and English.\n"
        "Your task is to analyze the user's transcript and generate a structured JSON response.\n\n"
        "CRITICAL LANGUAGE & OUTPUT RULES:\n"
        "1. 'hinglish_transcript': Transliterate any Hindi or Devanagari characters into natural, casual Romanized Hinglish (Hindi written using the English alphabet, "
        "the exact casual way Indians text on WhatsApp, e.g., 'Kal subah 10 baje team meeting karni hai aur deliverables finalize karne hain'). "
        "Every single sentence, thought, and detail from the input must be preserved in full sequence. "
        "NEVER summarize, omit, condense, or truncate sentences in 'hinglish_transcript'. NEVER output Devanagari script here.\n"
        "2. 'summary': Executive summary (1-3 sentences) in 100% CLEAR, PROFESSIONAL ENGLISH. "
        "DO NOT write the summary in Hindi or Devanagari under any circumstances.\n"
        "3. 'action_items': Concise checklist of key decisions/tasks in 100% CLEAR, PROFESSIONAL ENGLISH. "
        "DO NOT write action items in Hindi or Devanagari under any circumstances.\n"
        "4. 'sentiment': Exactly one of 'Positive', 'Neutral', or 'Negative'."
    )
    
    def _format_result(raw_dict: dict) -> dict:
        action_items = raw_dict.get("action_items", [])
        if not isinstance(action_items, list):
            action_items = [action_items] if action_items else []
        action_items = [str(a).strip() for a in action_items if str(a).strip()]

        summary = str(raw_dict.get("summary", "")).strip()
        sentiment = str(raw_dict.get("sentiment", "Neutral")).strip().capitalize()
        if sentiment not in ("Positive", "Neutral", "Negative"):
            sentiment = "Neutral"

        # Transcript protection:
        # If input had no Devanagari, keep original transcript verbatim to prevent any dropped sentences!
        if not has_devanagari:
            final_transcript = transcript
        else:
            cand = str(raw_dict.get("hinglish_transcript", "")).strip()
            # If transliteration is reasonably complete and not truncated, use it; otherwise fallback
            if cand and len(cand) >= int(len(transcript) * 0.7):
                final_transcript = cand
            else:
                final_transcript = transcript

        # Collapsing any autoregressive repetition loops in transcript
        for _ in range(3):
            final_transcript = re.sub(
                r'\b([A-Za-z\u0900-\u097F]+(?:[,\s]+[A-Za-z\u0900-\u097F]+){1,8})(?:[,\s]+\1\b)+',
                r'\1',
                final_transcript,
                flags=re.IGNORECASE
            )
        final_transcript = re.sub(
            r'\b([A-Za-z\u0900-\u097F]+)(?:[,\s]+\1\b){2,}',
            r'\1',
            final_transcript,
            flags=re.IGNORECASE
        )
        final_transcript = re.sub(r'\s+', ' ', final_transcript).strip()

        return {
            "transcript": final_transcript,
            "summary": summary,
            "action_items": action_items,
            "sentiment": sentiment
        }
    
    if not USE_LOCAL_MODELS:
        if not GEMINI_API_KEY:
            raise Exception("GEMINI_API_KEY is required when USE_LOCAL_MODELS is false.")
            
        model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        user_prompt = (
            f"Transcript: {transcript}\n\n"
            "Return a valid JSON object strictly matching these keys:\n"
            "- 'hinglish_transcript': (string) Full verbatim transcript in Romanized Hinglish (English alphabet, no Devanagari, no missing sentences).\n"
            "- 'summary': (string) Executive summary in ENGLISH only.\n"
            "- 'action_items': (array of strings) Actionable checklist in ENGLISH only.\n"
            "- 'sentiment': (string) 'Positive', 'Neutral', or 'Negative'."
        )
        
        # Primary method: Direct async HTTPX REST call with retry & fallback
        model_candidates = list(dict.fromkeys([model_name, "gemini-1.5-flash", "gemini-2.0-flash"]))
        for candidate_model in model_candidates:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{candidate_model}:generateContent?key={GEMINI_API_KEY}"
            payload = {
                "system_instruction": {"parts": [{"text": system_prompt}]},
                "contents": [{"parts": [{"text": user_prompt}]}],
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "temperature": 0.1,
                    "maxOutputTokens": 8192
                }
            }
            for attempt in range(2):
                try:
                    async with httpx.AsyncClient(timeout=60.0) as client:
                        response = await client.post(url, json=payload)
                        if response.status_code == 200:
                            data = response.json()
                            candidates = data.get("candidates", [])
                            if candidates:
                                text = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "").strip()
                                if text.startswith("```json"): text = text[7:]
                                elif text.startswith("```"): text = text[3:]
                                if text.endswith("```"): text = text[:-3]
                                
                                result = json.loads(text.strip())
                                return _format_result(result)
                        elif response.status_code in (429, 500, 502, 503):
                            print(f"[LLM] Gemini ({candidate_model}) returned HTTP {response.status_code}, retrying...")
                            await asyncio.sleep(2)
                            continue
                        else:
                            print(f"[LLM] Gemini ({candidate_model}) returned HTTP {response.status_code}: {response.text[:200]}")
                            break
                except Exception as rest_err:
                    print(f"[LLM] Gemini request error on {candidate_model} ({rest_err}), retrying...")
                    await asyncio.sleep(1)

        # Fallback method: Google GenerativeAI SDK
        if genai is not None:
            try:
                genai.configure(api_key=GEMINI_API_KEY)
                sdk_model = genai.GenerativeModel(
                    model_name=model_name,
                    system_instruction=system_prompt,
                    generation_config=genai.GenerationConfig(
                        response_mime_type="application/json",
                        temperature=0.1,
                        max_output_tokens=8192
                    )
                )
                response = await sdk_model.generate_content_async(user_prompt)
                text = response.text.strip()
                
                if text.startswith("```json"): text = text[7:]
                elif text.startswith("```"): text = text[3:]
                if text.endswith("```"): text = text[:-3]
                
                result = json.loads(text.strip())
                return _format_result(result)
            except Exception as sdk_err:
                raise HTTPException(status_code=500, detail=f"Gemini LLM summarization failed: {str(sdk_err)}")
        else:
            raise HTTPException(status_code=500, detail="Gemini LLM summarization failed: direct REST call failed and google-generativeai SDK is not available.")

    # --- LOCAL LLM EXECUTION ---
    start_llama_server()
    
    user_prompt = f"Transcript: {transcript}"
    
    prompt = f"<|system|>\n{system_prompt} Return your response strictly as a JSON object with keys: 'summary', 'action_items', 'sentiment'. Do not output any other text.<|end|>\n<|user|>\n{user_prompt}<|end|>\n<|assistant|>"
    
    schema = {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "action_items": {
                "type": "array",
                "items": {"type": "string"}
            },
            "sentiment": {"type": "string", "enum": ["Positive", "Neutral", "Negative"]}
        },
        "required": ["summary", "action_items", "sentiment"]
    }
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            response = await client.post(
                f"http://127.0.0.1:{LLAMA_SERVER_PORT}/completion",
                json={
                    "prompt": prompt,
                    "n_predict": 256,
                    "temperature": 0.1,
                    "stop": ["<|end|>"],
                    "json_schema": schema
                }
            )
            response.raise_for_status()
            data = response.json()
            text = data.get("content", "").strip()
            
            # Extract JSON from output
            try:
                # Handle cases where LLM wraps it in markdown ticks
                if text.startswith("```json"):
                    text = text[7:]
                elif text.startswith("```"):
                    text = text[3:]
                if text.endswith("```"):
                    text = text[:-3]
                
                result = json.loads(text.strip())
                
                # Ensure action items is a list
                action_items = result.get("action_items", [])
                if not isinstance(action_items, list):
                    action_items = [action_items]
                    
                if not has_devanagari:
                    hinglish_transcript = transcript
                else:
                    hinglish_transcript = result.get("hinglish_transcript", "").strip() or transcript
                return {
                    "transcript": hinglish_transcript,
                    "summary": result.get("summary", ""),
                    "action_items": action_items,
                    "sentiment": result.get("sentiment", "Neutral")
                }
            except json.JSONDecodeError:
                # Fallback if the LLM failed to output JSON
                return {
                    "summary": text,
                    "action_items": [],
                    "sentiment": "Unknown"
                }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Local LLM summarization failed: {str(e)}")
