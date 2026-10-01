import os
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
        "You are an intelligent bilingual assistant specialized in Indian speech, business notes, and Hinglish.\n"
        "1. Transliterate any Hindi or Devanagari characters into natural, conversational Romanized Hinglish (Hindi written using the English alphabet, "
        "the exact casual way Indians text on WhatsApp, e.g., 'Kal subah 10 baje team meeting karni hai aur draft share karna hai'). "
        "CRITICAL REQUIREMENT: 'hinglish_transcript' must be a 100% COMPLETE, VERBATIM, SENTENCE-BY-SENTENCE transliteration. "
        "DO NOT summarize, condense, omit, or drop ANY sentences or thoughts. Every single sentence from the original input must be present in sequence.\n"
        "2. Provide an executive summary (1-2 sentences) in clean, professional English.\n"
        "3. Extract concise actionable bullet items in clean, professional English.\n"
        "4. Determine sentiment: 'Positive', 'Neutral', or 'Negative'."
    )
    
    if not USE_LOCAL_MODELS:
        if not GEMINI_API_KEY:
            raise Exception("GEMINI_API_KEY is required when USE_LOCAL_MODELS is false.")
            
        model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        user_prompt = (
            f"Transcript: {transcript}\n\n"
            "Respond strictly with a JSON object containing keys:\n"
            "- 'hinglish_transcript': (string) complete, verbatim transliteration into Romanized Hinglish (English alphabet) without omitting any sentences.\n"
            "- 'summary': (string) executive summary in clean English.\n"
            "- 'action_items': (array of strings) actionable checklist in clean English.\n"
            "- 'sentiment': (string) 'Positive', 'Neutral', or 'Negative'."
        )
        
        # Primary method: Direct async HTTPX REST call (bypasses gRPC quirks & SDK version deprecations)
        try:
            print(f"Summarizing via Gemini API ({model_name})...")
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={GEMINI_API_KEY}"
            payload = {
                "system_instruction": {"parts": [{"text": system_prompt}]},
                "contents": [{"parts": [{"text": user_prompt}]}],
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "temperature": 0.1
                }
            }
            async with httpx.AsyncClient(timeout=30.0) as client:
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
                        action_items = result.get("action_items", [])
                        if not isinstance(action_items, list):
                            action_items = [action_items]
                            
                        # If transcript didn't have Devanagari in the first place, keep original transcript verbatim to avoid any dropped sentences!
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
                    else:
                        print(f"Gemini API returned no candidates: {data}")
                else:
                    print(f"Gemini REST returned HTTP {response.status_code}: {response.text}")
        except Exception as rest_err:
            print(f"Gemini REST call failed ({rest_err}), trying Google GenerativeAI SDK fallback...")

        # Fallback method: Google GenerativeAI SDK
        if genai is not None:
            try:
                genai.configure(api_key=GEMINI_API_KEY)
                sdk_model = genai.GenerativeModel(
                    model_name=model_name,
                    system_instruction=system_prompt,
                    generation_config=genai.GenerationConfig(
                        response_mime_type="application/json",
                        temperature=0.1
                    )
                )
                response = await sdk_model.generate_content_async(user_prompt)
                text = response.text.strip()
                
                if text.startswith("```json"): text = text[7:]
                elif text.startswith("```"): text = text[3:]
                if text.endswith("```"): text = text[:-3]
                
                result = json.loads(text.strip())
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
