import os
import subprocess
import argparse

def export_and_quantize_whisper(model_id: str = "openai/whisper-small"):
    """
    Exports the Hugging Face Whisper model to ONNX format (FP32) and then 
    applies dynamic INT8 quantization for efficient CPU inference.
    """
    # 1. Define output directories relative to the project root
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "models"))
    fp32_dir = os.path.join(base_dir, "whisper-small-fp32")
    int8_dir = os.path.join(base_dir, "whisper-small-int8")
    os.makedirs(base_dir, exist_ok=True)

    # 2. Export to FP32 ONNX
    # We use optimum-cli because Whisper is an encoder-decoder model consisting of 
    # multiple sub-models (encoder, decoder, decoder_with_past). The CLI handles 
    # tracing and exporting all these components seamlessly.
    print(f"Starting FP32 ONNX export for {model_id}...")
    subprocess.run([
        "optimum-cli", "export", "onnx",
        "-m", model_id,
        "--task", "automatic-speech-recognition-with-past",
        "--optimize", "O2", # Apply ONNX Runtime graph optimizations (constant folding, etc.)
        fp32_dir
    ], check=True)
    print(f"FP32 model successfully exported to {fp32_dir}")

    # 3. Export & Quantize to INT8
    # ONNX Runtime supports dynamic quantization where weights are quantized to INT8 
    # ahead of time, but activations are quantized dynamically during inference. 
    # We use onnxruntime.quantization directly to pass extra_options and avoid shape inference bugs in complex Whisper graphs.
    print(f"\nStarting INT8 Dynamic Quantization for {model_id}...")
    
    import shutil
    import onnx
    from onnxruntime.quantization import quantize_dynamic, QuantType
    
    # Copy all non-onnx config files first
    os.makedirs(int8_dir, exist_ok=True)
    for filename in os.listdir(fp32_dir):
        if not filename.endswith(".onnx"):
            shutil.copy(os.path.join(fp32_dir, filename), os.path.join(int8_dir, filename))
            
    # Quantize each ONNX file
    for filename in os.listdir(fp32_dir):
        if filename.endswith(".onnx"):
            fp32_model_path = os.path.join(fp32_dir, filename)
            int8_model_path = os.path.join(int8_dir, filename)
            print(f"Quantizing {filename}...")
            # Load model into memory to avoid Windows PermissionError on temp file unlink
            model = onnx.load(fp32_model_path)
            quantize_dynamic(
                model,
                int8_model_path,
                weight_type=QuantType.QUInt8,
                extra_options={'DefaultTensorType': onnx.TensorProto.FLOAT}
            )
            
    print(f"INT8 model successfully quantized to {int8_dir}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export and quantize Whisper model.")
    parser.add_argument("--model", type=str, default="openai/whisper-small", help="Hugging Face model ID")
    args = parser.parse_args()
    
    export_and_quantize_whisper(args.model)
