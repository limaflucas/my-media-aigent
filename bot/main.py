import os
import re
import logging

try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

from bot.parser import extract_media_info_from_url
from bot.overseerr import OverseerrClient

# Setup logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

def get_secret(key: str, default: str = None) -> str | None:
    """
    Reads a secret from:
    1. A file path specified in an env variable ending with _FILE (e.g. TELEGRAM_BOT_TOKEN_FILE).
    2. The standard Docker secrets path (/run/secrets/key_lowercase).
    3. The environment variable itself (direct fallback).
    """
    # 1. Check for filename pointer in env (e.g. TELEGRAM_BOT_TOKEN_FILE)
    file_path = os.getenv(f"{key}_FILE")
    if file_path and os.path.exists(file_path):
        try:
            with open(file_path, "r") as f:
                return f.read().strip()
        except Exception as e:
            logger.error(f"Failed to read secret from file path {file_path}: {e}")

    # 2. Check docker secrets directory (/run/secrets/key_lowercase)
    secret_name = key.lower()
    docker_secret_path = f"/run/secrets/{secret_name}"
    if os.path.exists(docker_secret_path):
        try:
            with open(docker_secret_path, "r") as f:
                return f.read().strip()
        except Exception as e:
            logger.error(f"Failed to read Docker secret from {docker_secret_path}: {e}")

    # 3. Fallback to direct environment variable
    return os.getenv(key, default)

# Read configuration using the secret helper
TELEGRAM_BOT_TOKEN = get_secret("TELEGRAM_BOT_TOKEN")
OVERSEERR_URL = os.getenv("OVERSEERR_URL", "http://localhost:5055")
OVERSEERR_API_KEY = get_secret("OVERSEERR_API_KEY")
OVERSEERR_SSL_VERIFY = os.getenv("OVERSEERR_SSL_VERIFY", "true").lower() in ("true", "1", "yes")

if not TELEGRAM_BOT_TOKEN:
    logger.error("TELEGRAM_BOT_TOKEN could not be loaded from environment or secrets!")
if not OVERSEERR_API_KEY:
    logger.error("OVERSEERR_API_KEY could not be loaded from environment or secrets!")

if not OVERSEERR_SSL_VERIFY:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    logger.info("SSL certificate verification is disabled for Overseerr/Seerr API calls.")

# Initialize Overseerr Client
overseerr = OverseerrClient(OVERSEERR_URL, OVERSEERR_API_KEY, ssl_verify=OVERSEERR_SSL_VERIFY)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends a welcoming message explaining the bot's features."""
    welcome_text = (
        "👋 **Welcome to the Movie Request Agent!**\n\n"
        "Send me a link to a movie, TV show, or anime, and I will find it and request it for you on Seerr!\n\n"
        "**Supported links:**\n"
        "• IMDb (e.g., `imdb.com/title/...`)\n"
        "• Letterboxd (e.g., `letterboxd.com/film/...`)\n"
        "• TMDB (e.g., `themoviedb.org/movie/...`)\n"
        "• MyAnimeList (e.g., `myanimelist.net/anime/...`)\n"
        "• AniList (e.g., `anilist.co/anime/...`)\n"
        "• Netflix (e.g., `netflix.com/title/...`)\n\n"
        "Alternatively, you can just type the **title** of the movie/show, and I will search for it directly!"
    )
    await update.message.reply_text(welcome_text, parse_mode="Markdown")

async def present_search_results(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    message_to_edit,
    query: str,
    results: list,
    target_year: int = None,
    target_type: str = None
):
    """Formats and displays the top 5 search results to the user as inline buttons."""
    # Define sorting weight to bubble up the best matching items
    def sort_key(item):
        score = 0
        media_type = item.get("mediaType", "")
        
        # Exact year match bonus
        release_date = item.get("releaseDate") or item.get("firstAirDate") or ""
        if target_year and release_date.startswith(str(target_year)):
            score += 10
            
        # Target media type match bonus
        if target_type and media_type == target_type:
            score += 5
            
        return score

    # Sort results
    sorted_results = sorted(results, key=sort_key, reverse=True)
    top_results = sorted_results[:5]  # Limit to 5 results for clarity

    keyboard = []
    for item in top_results:
        tmdb_id = item.get("id")
        media_type = item.get("mediaType", "movie")
        title = item.get("title") or item.get("name")
        release_date = item.get("releaseDate") or item.get("firstAirDate")
        year = release_date.split("-")[0] if release_date else "N/A"
        
        emoji = "🎬" if media_type == "movie" else "📺"
        button_text = f"{emoji} {title} ({year})"
        
        # Callback data format: action:media_type:tmdb_id
        callback_data = f"sel:{media_type}:{tmdb_id}"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
        
    keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await message_to_edit.edit_text(
        f"🔍 **Search Results for:** _'{query}'_\nChoose the correct item to request:",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )

async def show_item_details_from_dict(message, media_type: str, tmdb_id: int, details: dict):
    """Updates the message with detailed metadata and a request button."""
    title = details.get("title") if media_type == "movie" else details.get("name")
    release_date = details.get("releaseDate") if media_type == "movie" else details.get("firstAirDate")
    year = release_date.split("-")[0] if release_date else "N/A"
    overview = details.get("overview", "No overview available.")
    
    # Truncate overview if too long for Telegram
    if len(overview) > 300:
        overview = overview[:300] + "..."
        
    media_info = details.get("mediaInfo")
    status_str = overseerr.get_media_status_str(media_info)
    status_num = media_info.get("status", 1) if media_info else 1
    
    emoji = "🎬 Movie" if media_type == "movie" else "📺 TV Show"
    
    text = (
        f"**{title} ({year})**\n"
        f"Type: {emoji}\n"
        f"Status: {status_str}\n\n"
        f"_{overview}_\n"
    )
    
    keyboard = []
    # If media is not available (status 5) or partially available (status 4)
    if status_num in [1, 4]:
        if media_type == "movie":
            keyboard.append([InlineKeyboardButton("✅ Request Movie", callback_data=f"req:movie:{tmdb_id}")])
        else:
            keyboard.append([InlineKeyboardButton("✅ Request TV Show (All Seasons)", callback_data=f"req:tv:{tmdb_id}")])
    elif status_num in [2, 3]:
        # Item requested but pending/processing, allow requesting again or show status
        keyboard.append([InlineKeyboardButton("♻️ Request Again", callback_data=f"req:{media_type}:{tmdb_id}")])
        
    keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await message.edit_text(
        text,
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Processes incoming messages. Detects links or performs direct keyword search."""
    message_text = update.message.text
    if not message_text:
        return

    # Look for URLs in the message
    urls = re.findall(r"https?://[^\s]+", message_text)
    
    # If no URL is found, treat the message as a direct search query
    if not urls:
        query = message_text.strip()
        if len(query) < 2:
            return
        
        processing_msg = await update.message.reply_text("🔍 **Searching Overseerr...**", parse_mode="Markdown")
        try:
            results = overseerr.search(query)
            if not results:
                await processing_msg.edit_text(
                    f"❌ No results found on Overseerr for **'{query}'**.",
                    parse_mode="Markdown"
                )
                return
            await present_search_results(update, context, processing_msg, query, results)
        except Exception as e:
            logger.error(f"Search failed: {e}", exc_info=True)
            await processing_msg.edit_text("❌ An error occurred while searching.")
        return

    # If URL is found, parse and request/search
    url = urls[0]
    processing_msg = await update.message.reply_text("🔍 **Parsing link and searching Seerr...**", parse_mode="Markdown")

    try:
        media_info = extract_media_info_from_url(url)
        if not media_info:
            await processing_msg.edit_text(
                "❌ Could not extract media details from that link.\n"
                "Please verify the URL or try searching by title.",
                parse_mode="Markdown"
            )
            return

        # Direct TMDB lookup bypasses search and requests media directly
        if media_info.get("source") == "tmdb_url":
            tmdb_id = media_info["tmdb_id"]
            media_type = media_info["media_type"]
            
            await processing_msg.edit_text(f"⏳ Submitting request for TMDB ID **{tmdb_id}** ({media_type})...", parse_mode="Markdown")
            
            result = overseerr.request_media(media_type, tmdb_id)
            if result:
                await processing_msg.edit_text(
                    f"🎉 **Request Submitted Successfully!**\n\n"
                    f"TMDB ID **{tmdb_id}** ({media_type}) has been requested in Seerr.",
                    parse_mode="Markdown"
                )
            else:
                await processing_msg.edit_text(
                    f"❌ **Failed to request TMDB ID {tmdb_id}.**\n\n"
                    "Please verify Overseerr API connection or logs.",
                    parse_mode="Markdown"
                )
            return

        # Regular Title Search
        title = media_info["title"]
        year = media_info.get("year")
        media_type = media_info.get("media_type")

        results = overseerr.search(title)
        if not results:
            await processing_msg.edit_text(
                f"❌ No results found on Seerr for **'{title}'**.",
                parse_mode="Markdown"
            )
            return

        await present_search_results(
            update=update,
            context=context,
            message_to_edit=processing_msg,
            query=title,
            results=results,
            target_year=year,
            target_type=media_type
        )

    except Exception as e:
        logger.error(f"Error handling URL message: {e}", exc_info=True)
        await processing_msg.edit_text("❌ An error occurred while parsing the link.")

async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Processes button clicks from inline keyboards."""
    query = update.callback_query
    await query.answer()

    data = query.data
    logger.info(f"Callback trigger: {data}")

    if data == "cancel":
        await query.message.delete()
        return

    parts = data.split(":")
    action = parts[0]

    if action == "sel":
        # Select search item (directly submit request to bypass broken movie/tv details endpoints)
        media_type = parts[1]
        tmdb_id = int(parts[2])

        # Extract title from the clicked button text
        title = "Selected Item"
        if query.message.reply_markup and query.message.reply_markup.inline_keyboard:
            for row in query.message.reply_markup.inline_keyboard:
                for button in row:
                    if button.callback_data == data:
                        title = button.text
                        break
        
        # Strip emoji from title if present
        if title.startswith("🎬") or title.startswith("📺"):
            title = title[2:].strip()

        await query.message.edit_text(f"⏳ Submitting request for **{title}**...", parse_mode="Markdown")

        result = overseerr.request_media(media_type, tmdb_id)
        if result:
            await query.message.edit_text(
                f"🎉 **Request Submitted Successfully!**\n\n"
                f"**{title}** has been requested in Seerr.",
                parse_mode="Markdown"
            )
        else:
            await query.message.edit_text(
                f"❌ **Failed to request {title}.**\n\n"
                "Please verify Overseerr API connection or logs.",
                parse_mode="Markdown"
            )

    elif action == "req":
        # Submit the request (kept as fallback)
        media_type = parts[1]
        tmdb_id = int(parts[2])

        # Extract title from the interactive message to confirm it to the user
        first_line = query.message.text.split("\n")[0]
        title = first_line.replace("**", "").strip()

        await query.message.edit_text(f"⏳ Submitting request for **{title}**...", parse_mode="Markdown")

        result = overseerr.request_media(media_type, tmdb_id)
        if result:
            await query.message.edit_text(
                f"🎉 **Request Submitted Successfully!**\n\n"
                f"**{title}** has been requested in Seerr.",
                parse_mode="Markdown"
            )
        else:
            await query.message.edit_text(
                f"❌ **Failed to request {title}.**\n\n"
                "Please verify Overseerr API connection or logs.",
                parse_mode="Markdown"
            )

def main():
    if not TELEGRAM_BOT_TOKEN or not OVERSEERR_API_KEY:
        print("CRITICAL: TELEGRAM_BOT_TOKEN and OVERSEERR_API_KEY must be set in environmental variables.")
        return

    logger.info("Starting Telegram Bot...")
    
    # Build application
    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    # Handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(handle_callback_query))

    # Run bot
    application.run_polling()

if __name__ == "__main__":
    main()
