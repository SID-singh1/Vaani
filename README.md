# Vaani | On-Device Hindi-Hinglish Voice Intelligence

<div align="center">
  <img src="https://via.placeholder.com/800x400?text=Vaani+Web+Dashboard+Screenshot" alt="Vaani Dashboard UI" width="100%"/>
</div>

<br>

**Vaani** is a highly-optimized Voice AI pipeline designed to instantly transcribe and summarize Hindi-English (Hinglish) voice notes. It features a unique **Dual Engine Architecture**: it can run 100% on-device for absolute data privacy using quantized local models, or it can dynamically switch to cloud APIs (Groq & Gemini) for blazing fast speed and extreme accuracy on low-end hardware.

---

## 📸 See It In Action

### 1. Telegram Bot Integration
Users can seamlessly forward 5-minute Hinglish voice notes from WhatsApp or Telegram directly to the Vaani Bot. It processes the audio locally and instantly replies with a clean, bulleted English summary and action items.

<div align="center">
  <img src="https://via.placeholder.com/400x600?text=Telegram+Bot+Action+Screenshot" alt="Telegram Bot Demo" width="45%"/>
</div>

### 2. Admin Analytics Dashboard
A stunning, glassmorphism-themed Admin Dashboard that queries the local SQLite database to provide live metrics on usage, including a 7-day activity timeline and Sentiment Analysis of processed voice notes.

<div align="center">
  <img src="https://via.placeholder.com/800x400?text=Admin+Dashboard+Screenshot" alt="Admin Analytics Dashboard" width="100%"/>
</div>

---

## 🏗️ Architecture & Engineering: The Dual Engine

Vaani achieves extreme flexibility through a Strategy Pattern architecture, controlled via the `USE_LOCAL_MODELS` environment variable.

### 🛡️ Engine 1: Absolute Privacy (100% On-Device)
Designed for highly sensitive business communications, this engine guarantees no data ever leaves your hardware.
1. **ASR (Speech-to-Text):** Uses a custom INT8 quantized `Whisper-small` model running via `optimum.onnxruntime`. Audio is force-chunked and resampled using FFmpeg to guarantee no hallucinations on long clips, delivering lightning-fast CPU inference.
2. **LLM (Summarization):** Uses Microsoft's `Phi-3-mini` (3.8B parameters) quantized to 4-bit (`Q4_K_M`) GGUF. By bypassing slow Python bindings and acting as a reverse-proxy to the native C++ `llama-server`, it achieves upwards of 7-10 tokens/s on a standard consumer CPU.

### ⚡ Engine 2: Speed & Accuracy (Cloud APIs)
Designed for low-end hardware and high concurrency, this engine leverages state-of-the-art cloud infrastructure.
1. **ASR (Speech-to-Text):** Dynamically switches to **Groq's LPU** hardware to run `whisper-large-v3`, achieving instant, near zero-latency transcription of complex Hinglish audio.
2. **LLM (Summarization):** Streams the transcript to **Google's Gemini 1.5 Flash** to extract structured JSON (Summaries, Action Items, Sentiment) with industry-leading intelligence and context awareness.

## 💻 Tech Stack
* **Backend:** FastAPI, SQLAlchemy (SQLite), python-telegram-bot
* **ML Inference:** `llama.cpp` (LLM), `optimum` & `onnxruntime` (ASR)
* **Frontend:** Vanilla HTML/JS, Modern CSS (Glassmorphism), Chart.js
* **DevOps:** Docker (Containerized for 1-click cloud deployment)

---

## 🚀 Run It Yourself (Locally)

Because this project runs heavy ML models locally, you will need to run two terminal windows to bring the entire pipeline online.

### 1. Start the FastAPI AI Backend
This terminal loads the `Whisper` and `Phi-3` models into memory and starts the web server.

```powershell
# Activate your virtual environment
.\venv\Scripts\Activate.ps1

# Start the FastAPI server
uvicorn main:app --app-dir backend --host 127.0.0.1 --port 8000
```
*You can now open `http://127.0.0.1:8000` to view the Web App, or `http://127.0.0.1:8000/admin` for Analytics.*

### 2. Start the Telegram Bot
Open a **second** terminal window to start the Telegram polling service.

```powershell
# Activate your virtual environment
.\venv\Scripts\Activate.ps1

# Start the bot
python telegram-bot\telegram_bot.py
```
*Your bot is now live and waiting for voice notes!*
