import os
import subprocess
import urllib.request
import json
import zipfile
import shutil
from huggingface_hub import snapshot_download

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MODELS_DIR = os.path.join(BASE_DIR, "models")
ML_DIR = os.path.join(BASE_DIR, "ml")
LLAMA_CPP_DIR = os.path.join(ML_DIR, "llama.cpp")

FP16_DIR = os.path.join(MODELS_DIR, "phi-3-mini-fp16")
FP16_GGUF = os.path.join(MODELS_DIR, "phi-3-mini-fp16.gguf")
INT4_GGUF = os.path.join(MODELS_DIR, "phi-3-mini-q4_k_m.gguf")

MODEL_ID = "microsoft/Phi-3-mini-4k-instruct"

def download_latest_llama_cpp_windows_binary():
    print("Fetching latest llama.cpp Windows binaries...")
    api_url = "https://api.github.com/repos/ggerganov/llama.cpp/releases/latest"
    req = urllib.request.Request(api_url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req) as response:
        data = json.loads(response.read().decode())
    
    download_url = None
    for asset in data.get("assets", []):
        if "win-cpu-x64.zip" in asset["name"] or "win-avx2-x64.zip" in asset["name"]:
            download_url = asset["browser_download_url"]
            break
            
    if not download_url:
        raise Exception("Could not find Windows precompiled binaries for llama.cpp")
        
    print(f"Downloading from {download_url}...")
    zip_path = os.path.join(ML_DIR, "llama_cpp_bin.zip")
    urllib.request.urlretrieve(download_url, zip_path)
    
    print("Extracting quantize.exe...")
    print("Extracting llama.cpp binaries...")
    quantize_exe_path = os.path.join(ML_DIR, "llama-quantize.exe")
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(ML_DIR)
                
    if os.path.exists(zip_path):
        os.remove(zip_path)
        
    return quantize_exe_path

def export_and_quantize_phi3():
    os.makedirs(MODELS_DIR, exist_ok=True)
    
    # 1. Download HF Model
    print(f"\n1. Downloading full-precision model {MODEL_ID} from HuggingFace...")
    snapshot_download(repo_id=MODEL_ID, local_dir=FP16_DIR, ignore_patterns=["*.msgpack", "*.h5", "*.ot"])
    
    # 2. Setup llama.cpp
    print("\n2. Setting up llama.cpp...")
    if not os.path.exists(LLAMA_CPP_DIR):
        subprocess.run(["git", "clone", "https://github.com/ggerganov/llama.cpp", LLAMA_CPP_DIR], check=True)
    
    print("Installing gguf and sentencepiece packages...")
    subprocess.run([sys.executable, "-m", "pip", "install", "gguf", "sentencepiece"], check=True)
    
    # 3. Convert HF to GGUF FP16
    print("\n3. Converting HF model to FP16 GGUF...")
    convert_script = os.path.join(LLAMA_CPP_DIR, "convert_hf_to_gguf.py")
    subprocess.run([
        sys.executable, convert_script,
        FP16_DIR,
        "--outfile", FP16_GGUF,
        "--outtype", "f16"
    ], check=True)
    
    # 4. Quantize to INT4 (Q4_K_M)
    print("\n4. Quantizing to Q4_K_M...")
    quantize_exe = os.path.join(ML_DIR, "llama-quantize.exe")
    if not os.path.exists(quantize_exe):
        quantize_exe = download_latest_llama_cpp_windows_binary()
        
    subprocess.run([
        quantize_exe,
        FP16_GGUF,
        INT4_GGUF,
        "q4_k_m"
    ], check=True)
    
    print("\nLLM Quantization complete!")
    print(f"FP16 GGUF: {FP16_GGUF}")
    print(f"INT4 GGUF: {INT4_GGUF}")

if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    export_and_quantize_phi3()
