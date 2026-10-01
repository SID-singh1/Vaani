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
        headers = {"X-Internal-Secret": config.INTERNAL_API_SECRET}
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(f"{config.FASTAPI_BACKEND_URL}/history/{user_id}", headers=headers)
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

async def process_media_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Download the voice/audio/video message and send it to the FastAPI backend."""
    media_obj = (
        update.message.voice 
        or update.message.audio 
        or update.message.video_note
        or update.message.video
        or update.message.document
    )
    if not media_obj:
        return
        
    duration = getattr(media_obj, 'duration', 0)
    duration_str = f"{duration}s " if duration else ""
    status_message = await update.message.reply_text(f"⚡ Transcribing & analyzing your {duration_str}recording... ✨")
    
    try:
        # Get the media file from Telegram
        media_file = await context.bot.get_file(media_obj.file_id)
        
        # Download the file into memory
        audio_buffer = io.BytesIO()
        await media_file.download_to_memory(out=audio_buffer)
        
        file_name = getattr(media_obj, 'file_name', None) or 'recording.oga'
        mime_type = getattr(media_obj, 'mime_type', None) or 'audio/ogg'
        
        # Send to our FastAPI backend
        user_id = f"tg_{update.effective_user.id}"
        headers = {"X-Internal-Secret": config.INTERNAL_API_SECRET}
        
        async with httpx.AsyncClient(timeout=120.0) as client:
            files = {'audio': (file_name, audio_buffer.getvalue(), mime_type)}
            data = {'user_id': user_id}
            
            response = await client.post(
                f"{config.FASTAPI_BACKENDURL if hasattr(config, 'FASTAPI_BACKENDURL') else config.FASTAPI_BACKEND_URL}/process-audio",
                data=data,
                files=files,
                headers=headers
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
        
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error from backend ({e.response.status_code}): {e.response.text}")
        if e.response.status_code == 429:
            await status_message.edit_text("⏳ *Rate limit reached!* You're sending notes faster than the system can process. Please wait 30 seconds before trying again.")
        elif e.response.status_code == 413:
            await status_message.edit_text("📁 *File size limit:* Maximum allowed size is 15MB. Please send a shorter audio clip.")
        elif e.response.status_code == 400:
            await status_message.edit_text("⚠️ *Unsupported format:* Please send a voice note, or an audio/video file like .mp3, .m4a, .wav, .opus, or .mp4.")
        else:
            await status_message.edit_text("⚡ *High Traffic Surge:* Our AI engines are handling heavy demand right now. Please try again in a few moments!")
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        logger.error(f"Connection/Timeout to backend: {e}")
        await status_message.edit_text("⚡ *High Demand:* Our AI engines are currently processing heavy traffic. Please try sending again in 15–20 seconds!")
    except Exception as e:
        logger.error(f"Unexpected error processing media message: {e}", exc_info=True)
        await status_message.edit_text("⚡ *Processing Queue Busy:* Our AI engine encountered high demand. Please try sending again in a few moments!")

async def process_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text messages: guide for short greetings, summarize long text."""
    text = (update.message.text or "").strip()
    if not text:
        return
        
    words = text.split()
    if len(words) < 6:
        # Conversational guidance
        guide = (
            "👋 *Hi! I am Vaani.* Your voice & meeting intelligence assistant.\n\n"
            "Here is what you can send me:\n"
            "🎙️ *Voice Note:* Tap and speak naturally in Hindi, English, or Hinglish.\n"
            "🎵 *Audio / Video:* Upload any `.mp3`, `.m4a`, `.wav`, or `.mp4` file.\n"
            "📝 *Text / Notes:* Paste a long message, transcript, or messy chat to extract bulleted action items!\n\n"
            "Try sending an audio note or paste some text right now! ✨"
        )
        await update.message.reply_text(guide, parse_mode="Markdown")
        return

    # Long text: Summarize directly!
    status_message = await update.message.reply_text("⚡ Analyzing your text notes... ✨")
    user_id = f"tg_{update.effective_user.id}"
    headers = {"X-Internal-Secret": config.INTERNAL_API_SECRET}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{config.FASTAPI_BACKEND_URL}/process-text",
                data={"user_id": user_id, "text": text},
                headers=headers
            )
            response.raise_for_status()
            result = response.json()

        summary = result.get("summary", "")
        action_items = result.get("action_items", [])
        sentiment = result.get("sentiment", "Neutral")

        reply = f"🧠 *AI Summary ({sentiment}):*\n{summary}\n\n"
        if action_items:
            reply += "✅ *Action Items:*\n"
            for item in action_items:
                reply += f"• {item}\n"

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
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error processing text ({e.response.status_code}): {e.response.text}")
        if e.response.status_code == 429:
            await status_message.edit_text("⏳ *Rate limit reached!* You're sending notes faster than the system can process. Please wait 30 seconds before trying again.")
        else:
            await status_message.edit_text("⚡ *High Traffic Surge:* Our AI engines are handling heavy demand right now. Please try again in a moment!")
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        logger.error(f"Connection/Timeout to backend: {e}")
        await status_message.edit_text("⚡ *High Demand:* Our AI engines are currently processing heavy traffic. Please try sending again in 15–20 seconds!")
    except Exception as e:
        logger.error(f"Error processing text message: {e}", exc_info=True)
        await status_message.edit_text("⚡ *Processing Queue Busy:* Our AI engine encountered high demand. Please try again in a moment!")

async def process_photo_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle photos/images politely."""
    msg = (
        "📸 I see an image! Right now, I specialize in **Audio & Text Intelligence**.\n\n"
        "Send me a **voice note**, **audio file (.mp3, .wav, .m4a)**, or paste a **meeting transcript** to get instant summaries!"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

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
            headers = {"X-Internal-Secret": config.INTERNAL_API_SECRET}
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(
                    f"{config.FASTAPI_BACKEND_URL}/feedback",
                    data={"interaction_id": interaction_id, "rating": rating},
                    headers=headers
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

    # Media messages: voice notes, audio files, video notes, video clips, and documents
    media_filter = filters.VOICE | filters.AUDIO | filters.VIDEO_NOTE | filters.VIDEO | filters.Document.ALL
    app.add_handler(MessageHandler(media_filter, process_media_message))
    
    # Text messages: quick onboarding guide or direct text summarization
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, process_text_message))
    
    # Photos
    app.add_handler(MessageHandler(filters.PHOTO, process_photo_message))
    
    from telegram.ext import CallbackQueryHandler
    app.add_handler(CallbackQueryHandler(feedback_callback))

    # Run the bot until the user presses Ctrl-C
    logger.info("Bot is polling for messages...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
