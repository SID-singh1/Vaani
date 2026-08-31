import os
import subprocess
import re
import mlflow

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MODELS_DIR = os.path.join(BASE_DIR, "models")
ML_DIR = os.path.join(BASE_DIR, "ml")
MLRUNS_DIR = os.path.join(ML_DIR, "mlruns")

# Setup MLflow
os.environ["MLFLOW_TRACKING_URI"] = f"sqlite:///{os.path.join(MLRUNS_DIR, 'mlflow.db')}"
mlflow.set_tracking_uri(os.environ["MLFLOW_TRACKING_URI"])
mlflow.set_experiment("Phi-3-mini_LLM_Quantization_Benchmark")

LLAMA_BENCH = os.path.join(ML_DIR, "llama-bench.exe")

MODELS = {
    "Phi3-FP16": os.path.join(MODELS_DIR, "phi-3-mini-fp16.gguf"),
    "Phi3-INT4": os.path.join(MODELS_DIR, "phi-3-mini-q4_k_m.gguf"),
}

def parse_bench_output(output, model_name):
    # Example table row:
    # | phi3 3B Q4_K - Medium          |   2.23 GiB |     3.82 B | CPU        |       4 |           tg128 |          9.66 ± 0.08 |
    size = None
    prompt_ts = None
    gen_ts = None
    
    for line in output.split('\n'):
        if '|' not in line or 'model' in line or '---' in line:
            continue
            
        parts = [p.strip() for p in line.split('|')[1:-1]]
        if len(parts) >= 7:
            size_str = parts[1]
            test_type = parts[5]
            ts_str = parts[6].split('±')[0].strip()
            
            if size is None and 'GiB' in size_str:
                size = float(size_str.replace('GiB', '').strip()) * 1024 # Convert to MB
            
            if 'pp128' in test_type:
                prompt_ts = float(ts_str)
            elif 'tg128' in test_type:
                gen_ts = float(ts_str)
                
    return size, prompt_ts, gen_ts

def benchmark_model(model_name, model_path):
    print(f"\n--- Benchmarking {model_name} ---")
    
    if not os.path.exists(model_path):
        print(f"Skipping {model_name}, model file not found: {model_path}")
        return None
        
    cmd = [LLAMA_BENCH, "-m", model_path, "-p", "128", "-n", "128"]
    print(f"Running: {' '.join(cmd)}")
    
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=ML_DIR, encoding='utf-8')
    
    if result.returncode != 0:
        print(f"Error running benchmark for {model_name}:")
        print(result.stderr)
        return None
        
    size_mb, prompt_ts, gen_ts = parse_bench_output(result.stdout, model_name)
    
    print(f"Results for {model_name}:")
    print(f"  Model Size: {size_mb:.2f} MB" if size_mb else "  Model Size: N/A")
    print(f"  Prompt Processing: {prompt_ts:.2f} tokens/s" if prompt_ts else "  Prompt Processing: N/A")
    print(f"  Text Generation: {gen_ts:.2f} tokens/s" if gen_ts else "  Text Generation: N/A")
    
    # Log to MLflow
    if gen_ts is not None:
        with mlflow.start_run(run_name=f"{model_name}_benchmark"):
            mlflow.log_param("model_name", model_name)
            if size_mb:
                mlflow.log_metric("model_size_mb", size_mb)
            if prompt_ts:
                mlflow.log_metric("prompt_tokens_per_sec", prompt_ts)
            mlflow.log_metric("generation_tokens_per_sec", gen_ts)
            
    return {
        "Model": model_name,
        "Size (MB)": round(size_mb, 2) if size_mb else "N/A",
        "Prompt (t/s)": round(prompt_ts, 2) if prompt_ts else "N/A",
        "Generation (t/s)": round(gen_ts, 2) if gen_ts else "N/A"
    }

def main():
    import pandas as pd
    
    results = []
    for name, path in MODELS.items():
        res = benchmark_model(name, path)
        if res:
            results.append(res)
            
    if results:
        df = pd.DataFrame(results)
        print("\n=== Benchmark Summary ===")
        print(df.to_string(index=False))

if __name__ == "__main__":
    main()
