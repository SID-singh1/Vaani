#!/bin/bash

# Start FastAPI backend in the background
echo "Starting FastAPI backend..."
uvicorn main:app --app-dir backend --host 0.0.0.0 --port 7860 &
FASTAPI_PID=$!

# Wait for backend to be ready
sleep 5

# Start Telegram bot
echo "Starting Telegram Bot..."
python telegram-bot/telegram_bot.py &
BOT_PID=$!

# Wait for any process to exit
wait -n

# Exit with status of process that exited first
exit $?
