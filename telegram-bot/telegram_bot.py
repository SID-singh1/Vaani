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
        "I am your lightning-fast AI assistant. "
        "Just send or forward me a voice note in Hinglish, and I will instantly transcribe it, "
        "summarize it, and extract action items for you.\n\n"
        "🛠 *Available Commands:*\n"
        "/start - Show this welcome message\n"
        "/help - How to use the bot\n"
        "/history - View your last 5 transcriptions\n\n"
        "Try sending a voice note right now!"
    )
    await update.message.reply_text(welcome_message, parse_mode="Markdown")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message when the command /help is issued."""
    help_text = (
        "🎤 *How to use Vaani:*\n\n"
        "1. Hold the microphone button to record a voice note.\n"
        "2. Speak naturally in Hindi, English, or a mix of both (Hinglish).\n"
        "3. Send the note.\n\n"
        "I will process it using our blazing fast AI engines and reply with a clean summary."
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")

async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fetch the user's transcription history."""
    user_id = f"tg_{update.effective_user.id}"
    await update.message.reply_text("⏳ Fetching your history...")
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(f"{config.FASTAPI_BACKEND_URL}/history/{user_id}")
            response.raise_for_status()
            data = response.json()
            
        history = data.get("history", [])
        if not history:
            await update.message.reply_text("You haven't processed any voice notes yet!")
            return
            
        reply = "📚 *Your Last 5 Transcriptions:*\n\n"
        for idx, item in enumerate(history):
            date = item['timestamp'][:10]
            summary = item.get('summary', 'No summary')
            reply += f"*{idx+1}. {date}*\n_{summary}_\n\n"
            
        await update.message.reply_text(reply, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error fetching history: {e}")
        await update.message.reply_text("❌ Could not fetch history. Please try again later.")

async def process_voice_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Download the voice message and send it to the FastAPI backend."""
    duration = getattr(update.message.voice, 'duration', 0)
    duration_str = f"{duration}s " if duration else ""
    status_message = await update.message.reply_text(f"⚡ Transcribing & analyzing your {duration_str}voice note... ✨")
    
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
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        
        keyboard = [
            [
                InlineKeyboardButton("👍 Accurate", callback_data=f"rating_thumbs_up_{result.get('interaction_id', 'unknown')}"),
                InlineKeyboardButton("👎 Inaccurate", callback_data=f"rating_thumbs_down_{result.get('interaction_id', 'unknown')}")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(reply, parse_mode="Markdown", reply_markup=reply_markup)
        await status_message.delete()
        
    except Exception as e:
        logger.error(f"Error processing voice message: {e}")
        await status_message.edit_text(f"❌ Sorry, our AI engines are currently experiencing high traffic or an error occurred. Please try again in a few moments!")

async def feedback_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle accuracy rating button clicks."""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    if data.startswith("rating_"):
        parts = data.split("_")
        rating = parts[1] + "_" + parts[2] # thumbs_up or thumbs_down
        interaction_id = "_".join(parts[3:])
        
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(
                    f"{config.FASTAPI_BACKEND_URL}/feedback",
                    data={"interaction_id": interaction_id, "rating": rating}
                )
            # Update the message to remove the buttons and thank the user
            original_text = query.message.text
            thank_you = "✅ Thanks for your feedback!" if rating == "thumbs_up" else "❌ Thanks for your feedback. We will improve!"
            await query.edit_message_text(f"{original_text}\n\n_{thank_you}_", parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Error submitting feedback: {e}")
            await query.edit_message_text("❌ Failed to submit feedback.")

def main():
    """Start the bot."""
    logger.info("Starting Vaani Telegram Bot...")
    
    # Create the Application and pass it your bot's token.
    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

    # on different commands - answer in Telegram
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("history", history_command))

    # on non command i.e message - echo the message on Telegram
    app.add_handler(MessageHandler(filters.VOICE, process_voice_message))
    
    from telegram.ext import CallbackQueryHandler
    app.add_handler(CallbackQueryHandler(feedback_callback))

    # Run the bot until the user presses Ctrl-C
    logger.info("Bot is polling for messages...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
