import os
import subprocess
import atexit
import httpx
import json
import time
from fastapi import HTTPException

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MODELS_DIR = os.path.join(BASE_DIR, "models")
ML_DIR = os.path.join(BASE_DIR, "ml")
LLAMA_SERVER_EXE = os.path.join(ML_DIR, "llama-server.exe")
LLM_MODEL = os.path.join(MODELS_DIR, "phi-3-mini-q4_k_m.gguf")

llama_process = None
LLAMA_SERVER_PORT = 8081

def start_llama_server():
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
    Summarizes the given transcript using the INT4 Phi-3 model via llama-server.exe.
    Returns a dict with 'summary', 'action_items', and 'sentiment'.
    """
    start_llama_server()
    
    system_prompt = "You are an intelligent business assistant that analyzes Hindi-English mixed transcriptions. Extract a concise summary (1-2 sentences), a list of action items, and the overall sentiment (Positive, Neutral, or Negative). Return your response strictly as a JSON object with keys: 'summary', 'action_items', 'sentiment'. Do not output any other text."
    
    user_prompt = f"Transcript: {transcript}"
    
    prompt = f"<|system|>\n{system_prompt}<|end|>\n<|user|>\n{user_prompt}<|end|>\n<|assistant|>"
    
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
                    
                return {
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
            raise HTTPException(status_code=500, detail=f"LLM summarization failed: {str(e)}")
