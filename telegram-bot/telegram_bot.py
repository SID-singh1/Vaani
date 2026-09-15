import os
import io
import httpx
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from config import config

# Set up logging
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# Validate config before starting
config.validate()

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message when the command /start is issued."""
    welcome_message = (
        "👋 Welcome to Vaani Voice Assistant!\n\n"
        "I am your completely private, on-device AI assistant. "
        "Just send or forward me a voice note in Hinglish, and I will instantly transcribe it, "
        "summarize it, and extract action items for you.\n\n"
        "Try sending a voice note right now!"
    )
    await update.message.reply_text(welcome_message)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message when the command /help is issued."""
    help_text = (
        "🎤 *How to use Vaani:*\n\n"
        "1. Hold the microphone button to record a voice note.\n"
        "2. Speak naturally in Hindi, English, or a mix of both (Hinglish).\n"
        "3. Send the note.\n\n"
        "I will process it using our local Whisper and Phi-3 models and reply with a clean summary."
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")

async def process_voice_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Download the voice message and send it to the FastAPI backend."""
    # Let the user know we are working on it
    status_message = await update.message.reply_text("⏳ Processing your voice note... (this may take 30-60 seconds on CPU)")
    
    try:
        # Get the voice file from Telegram
        voice_file = await context.bot.get_file(update.message.voice.file_id)
        
        # Download the file into memory
        audio_buffer = io.BytesIO()
        await voice_file.download_to_memory(out=audio_buffer)
        
        # Send to our FastAPI backend
        user_id = f"tg_{update.effective_user.id}"
        
        async with httpx.AsyncClient(timeout=120.0) as client:
            files = {'audio': ('voice.oga', audio_buffer.getvalue(), 'audio/ogg')}
            data = {'user_id': user_id}
            
            response = await client.post(
                f"{config.FASTAPI_BACKEND_URL}/process-audio",
                data=data,
                files=files
            )
            response.raise_for_status()
            
            result = response.json()
            
        # Format the response
        transcription = result.get("transcript", "")
        summary = result.get("summary", "")
        action_items = result.get("action_items", [])
        sentiment = result.get("sentiment", "Neutral")
        
        # Build pretty message
        reply = f"📝 *Transcription:*\n_{transcription}_\n\n"
        reply += f"🧠 *AI Summary ({sentiment}):*\n{summary}\n\n"
        
        if action_items:
            reply += "✅ *Action Items:*\n"
            for item in action_items:
                reply += f"• {item}\n"
                
        # Send the final response and delete the "processing" message
        await update.message.reply_text(reply, parse_mode="Markdown")
        await status_message.delete()
        
    except Exception as e:
        logger.error(f"Error processing voice message: {e}")
        await status_message.edit_text(f"❌ Sorry, an error occurred while processing your audio.\nEnsure the local FastAPI backend is running!")

def main():
    """Start the bot."""
    logger.info("Starting Vaani Telegram Bot...")
    
    # Create the Application and pass it your bot's token.
    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

    # on different commands - answer in Telegram
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))

    # on non command i.e message - echo the message on Telegram
    app.add_handler(MessageHandler(filters.VOICE, process_voice_message))

    # Run the bot until the user presses Ctrl-C
    logger.info("Bot is polling for messages...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
