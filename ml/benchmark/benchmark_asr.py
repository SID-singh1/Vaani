import os
import time
import psutil
import pandas as pd
import mlflow
import librosa
import numpy as np
import jiwer
from transformers import AutoProcessor
from optimum.onnxruntime import ORTModelForSpeechSeq2Seq

# Paths
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MODELS_DIR = os.path.join(BASE_DIR, "models")
DATA_DIR = os.path.join(BASE_DIR, "ml", "data")
MLRUNS_DIR = os.path.join(BASE_DIR, "ml", "mlruns")

import sys
sys.stdout.reconfigure(encoding='utf-8')

# Create directories if they don't exist
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(MLRUNS_DIR, exist_ok=True)
mlflow.set_tracking_uri(f"sqlite:///{os.path.join(MLRUNS_DIR, 'mlflow.db')}")
mlflow.set_experiment("Vaani_ASR_Quantization")

def generate_dummy_audio():
    """Generates a dummy audio file for benchmarking if no data exists."""
    audio_path = os.path.join(DATA_DIR, "test_audio.wav")
    if not os.path.exists(audio_path):
        print("Generating dummy audio for benchmarking...")
        import soundfile as sf
        # 5 seconds of 16kHz silence/noise
        audio = np.random.randn(16000 * 5) * 0.01 
        sf.write(audio_path, audio, 16000)
    return audio_path

def benchmark_model(model_name: str, model_path: str, audio_path: str, reference_text: str = ""):
    print(f"\n--- Benchmarking {model_name} ---")
    
    # 1. Measure load time & Memory
    process = psutil.Process(os.getpid())
    mem_before = process.memory_info().rss / (1024 * 1024)
    
    start_load = time.time()
    processor = AutoProcessor.from_pretrained(model_path)
    model = ORTModelForSpeechSeq2Seq.from_pretrained(model_path)
    load_time = time.time() - start_load
    
    mem_after = process.memory_info().rss / (1024 * 1024)
    mem_used = mem_after - mem_before
    
    # 2. Prepare inputs
    speech, sr = librosa.load(audio_path, sr=16000)
    inputs = processor(speech, sampling_rate=16000, return_tensors="pt")
    
    # Warmup
    print("Warming up...")
    _ = model.generate(**inputs, max_new_tokens=20)
    
    # 3. Measure Inference Latency
    print("Measuring inference latency...")
    latencies = []
    num_runs = 5
    transcription = ""
    for _ in range(num_runs):
        start_inf = time.time()
        generated_ids = model.generate(**inputs, max_new_tokens=100)
        latencies.append(time.time() - start_inf)
    
    avg_latency = np.mean(latencies)
    transcription = processor.batch_decode(generated_ids, skip_special_tokens=True)[0]
    
    # 4. Calculate WER (if reference text is provided, else skip)
    wer = None
    if reference_text:
        wer = jiwer.wer(reference_text, transcription)
        
    print(f"Results for {model_name}:")
    print(f"  Load Time: {load_time:.2f} s")
    print(f"  Memory Footprint: {mem_used:.2f} MB")
    print(f"  Avg Latency: {avg_latency:.2f} s")
    print(f"  Transcription: '{transcription}'")
    if wer is not None:
        print(f"  WER: {wer:.4f}")
        
    # 5. Log to MLflow
    with mlflow.start_run(run_name=model_name):
        mlflow.log_params({
            "model_type": "whisper-small",
            "format": "ONNX",
            "precision": model_name.split("-")[-1]
        })
        mlflow.log_metrics({
            "load_time_sec": load_time,
            "memory_mb": mem_used,
            "avg_latency_sec": avg_latency
        })
        if wer is not None:
            mlflow.log_metric("wer", wer)
            
    return {
        "Model": model_name,
        "Load Time (s)": round(load_time, 2),
        "Memory (MB)": round(mem_used, 2),
        "Latency (s)": round(avg_latency, 2),
        "WER": round(wer, 4) if wer is not None else "N/A"
    }

if __name__ == "__main__":
    audio_file = generate_dummy_audio()
    # Assuming dummy audio translates to empty/random text, WER won't mean much, 
    # but we can pass a dummy reference to test the pipeline.
    reference = "this is a dummy transcription"
    
    results = []
    
    fp32_path = os.path.join(MODELS_DIR, "whisper-small-fp32")
    int8_path = os.path.join(MODELS_DIR, "whisper-small-int8")
    
    if os.path.exists(fp32_path):
        results.append(benchmark_model("Whisper-FP32", fp32_path, audio_file, reference))
    else:
        print(f"Warning: FP32 model not found at {fp32_path}")
        
    if os.path.exists(int8_path):
        results.append(benchmark_model("Whisper-INT8", int8_path, audio_file, reference))
    else:
        print(f"Warning: INT8 model not found at {int8_path}")
        
    # Output markdown table for README
    if results:
        df = pd.DataFrame(results)
        print("\n=== Benchmark Summary ===")
        print(df.to_markdown(index=False))
        
        # Save to a CSV for easy pasting
        csv_path = os.path.join(DATA_DIR, "asr_benchmark_results.csv")
        df.to_csv(csv_path, index=False)
        print(f"\nResults saved to {csv_path}")
