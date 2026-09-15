FROM python:3.10-slim

# Install system dependencies including ffmpeg for Whisper and curl/unzip for downloading models
RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    unzip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# We need to manually download the Linux compiled version of llama-server since our repo has the .exe
# We place it directly in the ml/ folder so backend can find it
RUN curl -L https://github.com/ggerganov/llama.cpp/releases/download/b3600/llama-b3600-bin-ubuntu-x64.zip -o llama.zip \
    && unzip llama.zip -d /app/llama_temp \
    && mkdir -p /app/ml \
    && mv /app/llama_temp/build/bin/llama-server /app/ml/llama-server \
    && chmod +x /app/ml/llama-server \
    && rm -rf /app/llama_temp llama.zip

# Copy requirements files first to leverage Docker cache
COPY backend/requirements.txt backend/
COPY telegram-bot/requirements.txt telegram-bot/
COPY ml/requirements.txt ml/

# Install python dependencies
RUN pip install --no-cache-dir -r backend/requirements.txt \
    && pip install --no-cache-dir -r telegram-bot/requirements.txt \
    && pip install --no-cache-dir -r ml/requirements.txt

# Copy all project files (except those in .dockerignore)
COPY . .

# Hugging Face Spaces routes port 7860
EXPOSE 7860

# We set the bot's internal backend URL to point to localhost on 7860 (since they run in the same container)
ENV FASTAPI_BACKEND_URL="http://127.0.0.1:7860"

# Make our startup script executable
RUN chmod +x start.sh

# Run both the FastAPI server and the Telegram bot
CMD ["./start.sh"]
