import os
import subprocess
import urllib.request
import json
import zipfile

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MODELS_DIR = os.path.join(BASE_DIR, "models")
ML_DIR = os.path.join(BASE_DIR, "ml")

FP16_GGUF = os.path.join(MODELS_DIR, "phi-3-mini-fp16.gguf")
INT4_GGUF = os.path.join(MODELS_DIR, "phi-3-mini-q4_k_m.gguf")

def download_latest_llama_cpp_windows_binary():
    print("Fetching latest llama.cpp Windows binaries...")
    api_url = "https://api.github.com/repos/ggerganov/llama.cpp/releases/latest"
    req = urllib.request.Request(api_url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req) as response:
        data = json.loads(response.read().decode())
    
    download_url = None
    for asset in data.get("assets", []):
        if "win-cpu-x64.zip" in asset["name"]:
            download_url = asset["browser_download_url"]
            break
            
    if not download_url:
        raise Exception("Could not find Windows precompiled binaries for llama.cpp")
        
    print(f"Downloading from {download_url}...")
    zip_path = os.path.join(ML_DIR, "llama_cpp_bin.zip")
    urllib.request.urlretrieve(download_url, zip_path)
    
    print("Extracting llama.cpp binaries...")
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(ML_DIR)
                
    if os.path.exists(zip_path):
        os.remove(zip_path)
        
    return os.path.join(ML_DIR, "llama-quantize.exe")

def main():
    quantize_exe = os.path.join(ML_DIR, "llama-quantize.exe")
    # Force download to ensure we have DLLs
    if os.path.exists(quantize_exe):
        os.remove(quantize_exe)
    
    quantize_exe = download_latest_llama_cpp_windows_binary()
    
    print("\nQuantizing to Q4_K_M...")
    subprocess.run([
        quantize_exe,
        FP16_GGUF,
        INT4_GGUF,
        "q4_k_m"
    ], check=True)
    print("Done!")

if __name__ == "__main__":
    main()
