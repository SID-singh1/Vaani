import os
import io
import re
import httpx
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from config import config

# Set up logging
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# Validate config before starting
config.validate()

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send welcome guide when /start is issued."""
    welcome_message = (
        "👋 *Welcome to Vaani Voice Assistant!*\n\n"
        "I am your lightning-fast AI meeting & voice intelligence assistant. "
        "Speak naturally in **Hinglish**, **Hindi**, or **English**, and I will instantly transcribe, "
        "summarize, and extract actionable checklists for you.\n\n"
        "🛠️ *Quick Commands:*\n"
        "• `/help` — Full instructions & command guide\n"
        "• `/history [n]` — View your previous summaries (e.g. `/history 5` or `/history all`)\n"
        "• `/delete` — Selectively delete specific notes or wipe all records\n"
        "• `/feedback <text>` — Send feedback or feature requests\n\n"
        "🎙️ *Try it right now:* Send me a quick voice note in Hinglish!"
    )
    await update.message.reply_text(welcome_message, parse_mode="Markdown")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send comprehensive instructions and command list."""
    help_text = (
        "🎙️ *Vaani — AI Voice & Meeting Assistant*\n\n"
        "Transform messy spoken thoughts and recordings into structured, actionable notes instantly!\n\n"
        "✨ *How to Use:*\n"
        "• *Voice Note:* Tap & speak naturally in Hinglish, Hindi, or English.\n"
        "• *Audio / Video:* Upload any `.mp3`, `.m4a`, `.wav`, `.opus`, or `.mp4` file (up to 15MB).\n"
        "• *Text / Transcripts:* Paste raw meeting notes or messy chat to extract bulleted action items.\n\n"
        "🛠️ *Available Commands:*\n"
        "• `/start` — Restart & show welcome guide\n"
        "• `/help` — View this full command menu\n"
        "• `/history [n]` — View past notes (e.g. `/history 3` or `/history all`)\n"
        "• `/delete` — Selectively delete specific notes or wipe your history\n"
        "• `/feedback <text>` — Send feedback or suggestions to the developers\n\n"
        "💡 *Tip:* Speak naturally in Hinglish! Vaani preserves your transcript in Romanized Hinglish while producing clean executive action items in English! 🚀"
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")

async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fetch user's transcription history with optional count limit."""
    user_id = f"tg_{update.effective_user.id}"
    
    limit = 5
    if context.args:
        arg = context.args[0].lower()
        if arg in ["all", "max"]:
            limit = 25
        elif arg.isdigit():
            limit = min(max(1, int(arg)), 25)

    status = await update.message.reply_text("⏳ Fetching your history...")
    
    try:
        headers = {"X-Internal-Secret": config.INTERNAL_API_SECRET}
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{config.FASTAPI_BACKEND_URL}/history/{user_id}?limit={limit}",
                headers=headers
            )
            response.raise_for_status()
            data = response.json()
            
        history = data.get("history", [])
        if not history:
            await status.edit_text("📭 You haven't processed any voice notes yet!")
            return
            
        reply = f"📚 *Your Recent Notes (Showing {len(history)}):*\n\n"
        for idx, item in enumerate(history):
            date = item['timestamp'][:10]
            sentiment = item.get('sentiment', 'Neutral')
            summary = item.get('summary', 'No summary')
            reply += f"*{idx+1}.* 📅 *{date}* `[{sentiment}]`\n_{summary}_\n\n"
            
        reply += "💡 *Tip:* Type `/delete` if you want to remove any of these notes."
        await status.edit_text(reply, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error fetching history: {e}")
        await status.edit_text("⚡ *High Demand:* Could not fetch history right now. Please try again in a moment!")

async def feedback_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle user feedback submissions."""
    user_id = f"tg_{update.effective_user.id}"
    feedback_text = " ".join(context.args).strip() if context.args else ""
    
    if not feedback_text:
        await update.message.reply_text(
            "💬 *Send Feedback:*\n\n"
            "Please include your thoughts after the command, for example:\n"
            "`/feedback Loved the Hinglish transcription, but please add PDF export!`",
            parse_mode="Markdown"
        )
        return
        
    try:
        headers = {"X-Internal-Secret": config.INTERNAL_API_SECRET}
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                f"{config.FASTAPI_BACKEND_URL}/user-feedback",
                data={"user_id": user_id, "message": feedback_text},
                headers=headers
            )
        await update.message.reply_text("🙏 *Thank you!* Your feedback has been received and will help us make Vaani better! ✨", parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error submitting user feedback: {e}")
        await update.message.reply_text("⚡ *High Traffic:* Could not submit feedback right now. Please try again in a few moments!")

async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Initiate selective note deletion flow."""
    user_id = f"tg_{update.effective_user.id}"
    status = await update.message.reply_text("⏳ Loading your notes for deletion...")
    
    try:
        headers = {"X-Internal-Secret": config.INTERNAL_API_SECRET}
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{config.FASTAPI_BACKEND_URL}/history/{user_id}?limit=15",
                headers=headers
            )
            response.raise_for_status()
            data = response.json()
            
        history = data.get("history", [])
        if not history:
            await status.edit_text("📭 You don't have any saved notes to delete.")
            return

        delete_state = {}
        msg = "🗑️ *Select Notes to Delete:*\n\n"
        for idx, item in enumerate(history):
            num = idx + 1
            delete_state[str(num)] = item["id"]
            date = item['timestamp'][:10]
            summary = item.get('summary', 'No summary')
            if len(summary) > 75:
                summary = summary[:72] + "..."
            msg += f"*{num}.* 📅 *{date}* — _{summary}_\n"

        msg += (
            "\n*Reply with the number(s) you wish to delete* (e.g. `1, 3` or `2`), "
            "or type `all` to wipe everything.\n"
            "*(Type `cancel` to exit)*"
        )
        context.user_data["delete_state"] = delete_state
        await status.edit_text(msg, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error initiating delete: {e}")
        await status.edit_text("⚡ *High Demand:* Could not retrieve notes. Please try again in a moment!")

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
        
        async with httpx.AsyncClient(timeout=180.0) as client:
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
        
        # Telegram has a 4096-char limit per message. Split if needed.
        TELEGRAM_MAX_LEN = 4000  # Leave some margin for safety
        
        if len(reply) <= TELEGRAM_MAX_LEN:
            await update.message.reply_text(reply, parse_mode="Markdown", reply_markup=reply_markup)
        else:
            # Split: send transcript first, then summary + action items with feedback buttons
            transcript_msg = f"📝 *Transcription:*\n_{transcription}_"
            summary_msg = f"🧠 *AI Summary ({sentiment}):*\n{summary}\n\n"
            if action_items:
                summary_msg += "✅ *Action Items:*\n"
                for item in action_items:
                    summary_msg += f"• {item}\n"
            
            # Send transcript in chunks if it's very long
            for i in range(0, len(transcript_msg), TELEGRAM_MAX_LEN):
                chunk = transcript_msg[i:i + TELEGRAM_MAX_LEN]
                try:
                    await update.message.reply_text(chunk, parse_mode="Markdown")
                except Exception:
                    # If Markdown parsing fails on a chunk boundary, send as plain text
                    await update.message.reply_text(chunk)
            
            # Send summary with feedback buttons
            try:
                await update.message.reply_text(summary_msg, parse_mode="Markdown", reply_markup=reply_markup)
            except Exception:
                await update.message.reply_text(summary_msg, reply_markup=reply_markup)
        
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
        err_str = str(e).lower()
        logger.error(f"Unexpected error processing media message: {e}", exc_info=True)
        if "file is too big" in err_str:
            await status_message.edit_text("📁 *File size limit:* Telegram prevents bots from downloading files larger than 20MB. Please send a shorter audio clip.")
        else:
            await status_message.edit_text("⚡ *Processing Queue Busy:* Our AI engine encountered high demand. Please try sending again in a few moments!")

async def process_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text messages: deletion inputs, conversational guidance, or text summarization."""
    text = (update.message.text or "").strip()
    if not text:
        return

    # Check if user is in an active deletion flow
    delete_state = context.user_data.get("delete_state")
    if delete_state:
        lowered = text.lower().strip()
        if lowered in ["cancel", "exit", "stop", "back"]:
            context.user_data.pop("delete_state", None)
            context.user_data.pop("pending_delete_ids", None)
            await update.message.reply_text("❌ *Deletion cancelled.* Your notes remain safe.", parse_mode="Markdown")
            return
            
        if lowered == "all":
            keyboard = [
                [
                    InlineKeyboardButton("🗑️ Yes, Delete All", callback_data="delete_confirm_all"),
                    InlineKeyboardButton("❌ Cancel", callback_data="delete_cancel")
                ]
            ]
            await update.message.reply_text(
                "⚠️ *Confirm Wipe All Notes:*\n\n"
                "Are you sure you want to permanently delete **ALL** your notes from the database? "
                "This action cannot be undone.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return

        if re.match(r'^[\d\s,]+$', text):
            raw_nums = re.findall(r'\b\d+\b', text)
            valid_nums = [n for n in raw_nums if n in delete_state]
            if not valid_nums:
                await update.message.reply_text(
                    "⚠️ *Invalid numbers.* Please reply with valid numbers from the list above (e.g. `1, 3`), or type `cancel`.",
                    parse_mode="Markdown"
                )
                return

            valid_nums = list(dict.fromkeys(valid_nums))
            selected_ids = [delete_state[n] for n in valid_nums]
            context.user_data["pending_delete_ids"] = selected_ids
            num_str = ", ".join(f"#{n}" for n in valid_nums)
            
            keyboard = [
                [
                    InlineKeyboardButton("✅ Confirm Delete", callback_data="delete_confirm_selected"),
                    InlineKeyboardButton("❌ Cancel", callback_data="delete_cancel")
                ]
            ]
            await update.message.reply_text(
                f"⚠️ *Confirm Deletion:*\n\n"
                f"Are you sure you want to permanently delete note(s) **{num_str}** from the database? "
                f"This action cannot be undone.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return

        # If user typed something else, clear delete_state and treat as normal text
        context.user_data.pop("delete_state", None)
        context.user_data.pop("pending_delete_ids", None)
        
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

async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle accuracy feedback rating and deletion confirmation callbacks."""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user_id = f"tg_{update.effective_user.id}"
    headers = {"X-Internal-Secret": config.INTERNAL_API_SECRET}

    if data.startswith("rating_"):
        parts = data.split("_")
        rating = parts[1] + "_" + parts[2] # thumbs_up or thumbs_down
        interaction_id = "_".join(parts[3:])
        
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(
                    f"{config.FASTAPI_BACKEND_URL}/feedback",
                    data={"interaction_id": interaction_id, "rating": rating},
                    headers=headers
                )
            original_text = query.message.text
            thank_you = "✅ Thanks for your feedback!" if rating == "thumbs_up" else "❌ Thanks for your feedback. We will improve!"
            await query.edit_message_text(f"{original_text}\n\n_{thank_you}_", parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Error submitting rating: {e}")
            await query.edit_message_text("❌ Failed to submit rating.")

    elif data == "delete_confirm_selected":
        pending_ids = context.user_data.get("pending_delete_ids", [])
        if not pending_ids:
            await query.edit_message_text("⚠️ No notes were selected for deletion.")
            return
            
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                res = await client.post(
                    f"{config.FASTAPI_BACKEND_URL}/history/{user_id}/delete",
                    json={"interaction_ids": pending_ids},
                    headers=headers
                )
                res.raise_for_status()
                res_data = res.json()
                count = res_data.get("deleted_count", len(pending_ids))
            
            context.user_data.pop("delete_state", None)
            context.user_data.pop("pending_delete_ids", None)
            await query.edit_message_text(f"✅ *Successfully deleted {count} note(s) from your database.*", parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Error deleting notes: {e}")
            await query.edit_message_text("⚡ *Error:* Could not delete notes right now. Please try again.")

    elif data == "delete_confirm_all":
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                res = await client.post(
                    f"{config.FASTAPI_BACKEND_URL}/history/{user_id}/delete",
                    json={"delete_all": True},
                    headers=headers
                )
                res.raise_for_status()
                res_data = res.json()
                count = res_data.get("deleted_count", 0)
                
            context.user_data.pop("delete_state", None)
            context.user_data.pop("pending_delete_ids", None)
            await query.edit_message_text(f"✅ *All {count} note(s) have been permanently erased from your database.*", parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Error wiping notes: {e}")
            await query.edit_message_text("⚡ *Error:* Could not wipe history right now. Please try again.")

    elif data == "delete_cancel":
        context.user_data.pop("delete_state", None)
        context.user_data.pop("pending_delete_ids", None)
        await query.edit_message_text("❌ *Deletion cancelled. Your notes remain safe.*", parse_mode="Markdown")

def main():
    """Start the bot."""
    logger.info("Starting Vaani Telegram Bot...")
    
    # Create the Application and pass it your bot's token.
    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("history", history_command))
    app.add_handler(CommandHandler("feedback", feedback_command))
    app.add_handler(CommandHandler("delete", delete_command))

    # Media messages: voice notes, audio files, video notes, video clips, and documents
    media_filter = filters.VOICE | filters.AUDIO | filters.VIDEO_NOTE | filters.VIDEO | filters.Document.ALL
    app.add_handler(MessageHandler(media_filter, process_media_message))
    
    # Text messages: deletion responses, conversational guidance, or text summarization
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, process_text_message))
    
    # Photos
    app.add_handler(MessageHandler(filters.PHOTO, process_photo_message))
    
    # Inline button callbacks
    app.add_handler(CallbackQueryHandler(handle_callback_query))

    # Run the bot until the user presses Ctrl-C
    logger.info("Bot is polling for messages...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
