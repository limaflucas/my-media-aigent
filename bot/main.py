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
OVERSEERR_URL = os.getenv("OVERSEERR_URL", "http://seerr:5055")
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

LANGUAGE_MAP = {
    "en": "English",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "ja": "Japanese",
    "ko": "Korean",
    "zh": "Chinese",
    "pt": "Portuguese",
    "ru": "Russian",
    "hi": "Hindi",
    "sv": "Swedish",
    "nl": "Dutch",
    "no": "Norwegian",
    "da": "Danish",
    "fi": "Finnish",
    "pl": "Polish",
    "tr": "Turkish",
}

# Initialize Overseerr Client
overseerr = OverseerrClient(OVERSEERR_URL, OVERSEERR_API_KEY, ssl_verify=OVERSEERR_SSL_VERIFY)

import time

def set_ttl_item(user_data: dict, key: str, value: any, ttl_seconds: int = 2700):
    """Stores an item in user_data with an expiration timestamp (default 45 mins)."""
    user_data[key] = {
        "value": value,
        "expires_at": time.time() + ttl_seconds
    }

def get_ttl_item(user_data: dict, key: str) -> any:
    """Retrieves an item from user_data if it has not expired yet."""
    item = user_data.get(key)
    if not item:
        return None
    
    if time.time() > item.get("expires_at", 0):
        # Evict expired item
        user_data.pop(key, None)
        return None
        
    return item.get("value")

def cleanup_expired_items(user_data: dict):
    """Removes all expired TTL items from user_data."""
    now = time.time()
    expired_keys = [
        k for k, v in user_data.items()
        if isinstance(v, dict) and "expires_at" in v and now > v["expires_at"]
    ]
    for k in expired_keys:
        user_data.pop(k, None)


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
        "Alternatively, you can just type the **title** of the movie/show, and I will search for it directly!\n\n"
        "ℹ️ **Requests Management:**\n"
        "Use `/seerr [number]` to view and manage recent requests (default is last 3 requests)."
    )
    await update.message.reply_text(welcome_text, parse_mode="Markdown")


async def display_requests_list(message, limit: int, skip: int = 0):
    """Fetches and displays the list of recent requests."""
    try:
        data = overseerr.get_requests(take=limit, skip=skip)
        if data is None:
            await message.edit_text("❌ **Failed to connect to Seerr.** Please check connection details or logs.", parse_mode="Markdown")
            return

        if "results" not in data or not data["results"]:
            await message.edit_text("📭 **No requests found on Seerr.**", parse_mode="Markdown")
            return

        results = data["results"]
        
        # Build the message text and buttons
        text_lines = [f"📋 **Last {len(results)} Requests on Seerr:**\n"]
        keyboard = []
        
        for req in results:
            req_id = req.get("id")
            req_status = req.get("status")
            media_info = req.get("media", {})
            tmdb_id = media_info.get("tmdbId")
            media_type = media_info.get("mediaType", "movie")
            
            # Map request status to human-readable
            # MediaRequestStatus: 1 = PENDING, 2 = APPROVED, 3 = DECLINED, 4 = FAILED, 5 = COMPLETED
            status_map = {
                1: "⏳ Pending Approval",
                2: "✅ Approved",
                3: "❌ Declined",
                4: "⚠️ Failed",
                5: "🎉 Completed"
            }
            status_str = status_map.get(req_status, f"Unknown ({req_status})")
            
            # Fetch details to get the media title/year
            title = None
            year = None
            try:
                if media_type == "movie":
                    details = overseerr.get_movie_details(tmdb_id)
                else:
                    details = overseerr.get_tv_details(tmdb_id)
                
                if details:
                    title = details.get("title") if media_type == "movie" else details.get("name")
                    release_date = details.get("releaseDate") if media_type == "movie" else details.get("firstAirDate")
                    year = release_date.split("-")[0] if release_date else None
            except Exception as e:
                logger.error(f"Failed to fetch details for tmdbId {tmdb_id}: {e}")
                
            if not title:
                title = f"TMDB {tmdb_id}"
            
            media_emoji = "🎬" if media_type == "movie" else "📺"
            display_title = f"{media_emoji} {title}"
            if year:
                display_title += f" ({year})"
                
            text_lines.append(f"**#{req_id}** — {display_title}\n• Status: {status_str}\n")
            
            # Button to select this request
            # Callback data format: req_sel:{request_id}:{limit}
            keyboard.append([
                InlineKeyboardButton(f"🔎 Manage #{req_id}: {title[:20]}...", callback_data=f"req_sel:{req_id}:{limit}")
            ])
            
        keyboard.append([InlineKeyboardButton("❌ Close", callback_data="cancel")])
        
        await message.edit_text(
            "\n".join(text_lines),
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Error displaying requests list: {e}", exc_info=True)
        await message.edit_text("❌ An error occurred while fetching the requests list.")


async def display_request_details(message, request_id: int, limit: int):
    """Fetches and displays the details for a single request with action buttons."""
    try:
        req = overseerr.get_request(request_id)
        if not req:
            await message.edit_text(
                f"❌ Request **#{request_id}** was not found or could not be loaded.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Back to List", callback_data=f"req_list:{limit}")]])
            )
            return
            
        req_status = req.get("status")
        media_info = req.get("media", {})
        tmdb_id = media_info.get("tmdbId")
        media_type = media_info.get("mediaType", "movie")
        
        status_map = {
            1: "⏳ Pending Approval",
            2: "✅ Approved",
            3: "❌ Declined",
            4: "⚠️ Failed",
            5: "🎉 Completed"
        }
        status_str = status_map.get(req_status, f"Unknown ({req_status})")
        
        # Fetch details to get the media title/year
        title = None
        year = None
        overview = None
        try:
            if media_type == "movie":
                details = overseerr.get_movie_details(tmdb_id)
            else:
                details = overseerr.get_tv_details(tmdb_id)
            
            if details:
                title = details.get("title") if media_type == "movie" else details.get("name")
                release_date = details.get("releaseDate") if media_type == "movie" else details.get("firstAirDate")
                year = release_date.split("-")[0] if release_date else None
                overview = details.get("overview")
        except Exception as e:
            logger.error(f"Failed to fetch details for tmdbId {tmdb_id}: {e}")
            
        if not title:
            title = f"TMDB {tmdb_id}"
            
        media_emoji = "🎬" if media_type == "movie" else "📺"
        display_title = f"{media_emoji} {title}"
        if year:
            display_title += f" ({year})"
            
        requested_by = req.get("requestedBy", {})
        username = requested_by.get("username", "Unknown")
        created_at = req.get("createdAt", "N/A")
        # Format createdAt if it is a ISO string
        if created_at != "N/A":
            try:
                created_at = created_at.replace("T", " ")[:16]
            except Exception:
                pass

        text = (
            f"📋 **Manage Request #{request_id}**\n\n"
            f"**Media:** {display_title}\n"
            f"**Type:** {media_type.capitalize()}\n"
            f"**Status:** {status_str}\n"
            f"**Requested By:** {username}\n"
            f"**Date:** {created_at}\n\n"
        )
        
        if overview:
            if len(overview) > 200:
                overview = overview[:200] + "..."
            text += f"_{overview}_\n"

        keyboard = []
        
        # Action buttons based on status:
        # MediaRequestStatus: 1 = PENDING, 2 = APPROVED, 3 = DECLINED, 4 = FAILED, 5 = COMPLETED
        # - Approve: only if PENDING (1)
        # - Deny (Decline): if PENDING (1) or APPROVED (2)
        # - Retry: only if FAILED (4)
        # - Delete: always
        
        action_row = []
        if req_status == 1:
            action_row.append(InlineKeyboardButton("✅ Approve", callback_data=f"req_act:approve:{request_id}:{limit}"))
            action_row.append(InlineKeyboardButton("❌ Deny", callback_data=f"req_act:decline:{request_id}:{limit}"))
        elif req_status == 2:
            action_row.append(InlineKeyboardButton("❌ Deny", callback_data=f"req_act:decline:{request_id}:{limit}"))
        elif req_status == 4:
            action_row.append(InlineKeyboardButton("♻️ Retry", callback_data=f"req_act:retry:{request_id}:{limit}"))
            
        if action_row:
            keyboard.append(action_row)
            
        keyboard.append([InlineKeyboardButton("🗑️ Delete Request", callback_data=f"req_act:delete:{request_id}:{limit}")])
        keyboard.append([InlineKeyboardButton("◀️ Back to List", callback_data=f"req_list:{limit}")])
        
        await message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Error displaying request details: {e}", exc_info=True)
        await message.edit_text(
            "❌ An error occurred while loading request details.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Back to List", callback_data=f"req_list:{limit}")]])
        )


async def show_search_item_details(message, media_type: str, tmdb_id: int, context, is_single_result: bool):
    """Fetches and displays detailed general information for a search result with options."""
    try:
        if media_type == "movie":
            details = overseerr.get_movie_details(tmdb_id)
        else:
            details = overseerr.get_tv_details(tmdb_id)
            
        if not details:
            await message.edit_text(
                "❌ Failed to load media details.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel search", callback_data="cancel")]])
            )
            return

        title = details.get("title") if media_type == "movie" else details.get("name")
        release_date = details.get("releaseDate") if media_type == "movie" else details.get("firstAirDate")
        year = release_date.split("-")[0] if release_date else "Unknown"
        overview = details.get("overview", "No overview available.")
        
        # 1. Duration / Runtime
        if media_type == "movie":
            runtime = details.get("runtime")
            duration = f"{runtime} minutes" if runtime else "Unknown"
        else:
            episode_run_time = details.get("episodeRunTime", [])
            if isinstance(episode_run_time, list) and episode_run_time:
                duration = f"{episode_run_time[0]} minutes per episode"
            else:
                duration = "Unknown"

        # 2. Director / Creator
        directors = []
        if media_type == "movie":
            credits = details.get("credits", {})
            crew = credits.get("crew", []) if isinstance(credits, dict) else []
            directors = [member.get("name") for member in crew if isinstance(member, dict) and member.get("job") == "Director"]
        else:
            created_by = details.get("createdBy", [])
            if isinstance(created_by, list):
                directors = [creator.get("name") for creator in created_by if isinstance(creator, dict) and creator.get("name")]
            if not directors:
                # Fallback to crew
                credits = details.get("credits", {})
                crew = credits.get("crew", []) if isinstance(credits, dict) else []
                directors = [member.get("name") for member in crew if isinstance(member, dict) and member.get("job") in ["Director", "Creator", "Series Director"]]

        director_str = ", ".join(directors) if directors else "Unknown"

        # 3. Original Idiom / Language
        lang_code = details.get("originalLanguage", "unknown")
        language = LANGUAGE_MAP.get(lang_code.lower(), lang_code.upper())

        # Media status
        media_info = details.get("mediaInfo")
        status_str = overseerr.get_media_status_str(media_info)
        status_num = media_info.get("status", 1) if media_info else 1

        emoji = "🎬 Movie" if media_type == "movie" else "📺 TV Show"

        text = (
            f"ℹ️ **{title} ({year})**\n\n"
            f"• **Type:** {emoji}\n"
            f"• **Duration:** {duration}\n"
            f"• **Director/Creator:** {director_str}\n"
            f"• **Original Language:** {language}\n"
            f"• **Status:** {status_str}\n\n"
            f"📖 **Plot:**\n_{overview[:300] + '...' if len(overview) > 300 else overview}_\n"
        )

        keyboard = []
        
        # Option 1: Request movie (or TV show)
        req_btn_text = "✅ Request Movie" if media_type == "movie" else "✅ Request TV Show"
        if status_num in [2, 3]:
            req_btn_text = "♻️ Request Again"
            
        keyboard.append([InlineKeyboardButton(req_btn_text, callback_data=f"search_req:{media_type}:{tmdb_id}")])

        # Option 2: Return to the results list
        if not is_single_result and get_ttl_item(context.user_data, "last_search_results"):
            keyboard.append([InlineKeyboardButton("◀️ Return to results list", callback_data="search_ret")])

        # Option 3: Cancel search
        keyboard.append([InlineKeyboardButton("❌ Cancel search", callback_data="cancel")])

        await message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )

    except Exception as e:
        logger.error(f"Error showing search item details: {e}", exc_info=True)
        await message.edit_text(
            "❌ An error occurred while retrieving media details.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel search", callback_data="cancel")]])
        )


async def seerr_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lists the last N requests from Seerr (default 3)."""
    message = update.effective_message
    if not message:
        return

    args = context.args
    limit = 3
    if args:
        try:
            val = int(args[0])
            if 1 <= val <= 20:
                limit = val
            else:
                await message.reply_text("⚠️ Please specify a number between 1 and 20.")
                return
        except ValueError:
            await message.reply_text("⚠️ Invalid number format. Use `/seerr [number]` (e.g. `/seerr 5`).")
            return

    processing_msg = await message.reply_text("⏳ **Fetching requests from Seerr...**", parse_mode="Markdown")
    await display_requests_list(processing_msg, limit, 0)

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
        callback_data = f"search_sel:{media_type}:{tmdb_id}"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
        
    keyboard.append([InlineKeyboardButton("❌ Cancel search", callback_data="cancel")])
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
    cleanup_expired_items(context.user_data)
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
            
            # Store search in user_data
            set_ttl_item(context.user_data, "last_search_query", query)
            set_ttl_item(context.user_data, "last_search_results", results)

            # If search returns only one result, display general information directly
            if len(results) == 1:
                tmdb_id = results[0]["id"]
                media_type = results[0].get("mediaType", "movie")
                await show_search_item_details(processing_msg, media_type, tmdb_id, context, is_single_result=True)
            else:
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

        # Store search in user_data
        set_ttl_item(context.user_data, "last_search_query", title)
        set_ttl_item(context.user_data, "last_search_results", results)

        # If search returns only one result, display general information directly
        if len(results) == 1:
            tmdb_id = results[0]["id"]
            media_type = results[0].get("mediaType", "movie")
            await show_search_item_details(processing_msg, media_type, tmdb_id, context, is_single_result=True)
        else:
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
    cleanup_expired_items(context.user_data)
    query = update.callback_query
    await query.answer()

    data = query.data
    logger.info(f"Callback trigger: {data}")

    if data == "cancel":
        context.user_data.pop("last_search_query", None)
        context.user_data.pop("last_search_results", None)
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

    elif action == "search_sel":
        media_type = parts[1]
        tmdb_id = int(parts[2])
        await show_search_item_details(query.message, media_type, tmdb_id, context, is_single_result=False)

    elif action == "search_req":
        media_type = parts[1]
        tmdb_id = int(parts[2])
        
        # Get title from message first line
        first_line = query.message.text.split("\n")[0]
        title = first_line.replace("ℹ️", "").replace("**", "").strip()

        await query.message.edit_text(f"⏳ Submitting request for **{title}**...", parse_mode="Markdown")

        result = overseerr.request_media(media_type, tmdb_id)
        if result:
            context.user_data.pop("last_search_query", None)
            context.user_data.pop("last_search_results", None)
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

    elif action == "search_ret":
        query_text = get_ttl_item(context.user_data, "last_search_query")
        results = get_ttl_item(context.user_data, "last_search_results")
        if query_text and results:
            await present_search_results(update, context, query.message, query_text, results)
        else:
            await query.message.edit_text("⚠️ No search history found (or it has expired). Please search again by typing the title.")

    elif action == "req_list":
        limit = int(parts[1])
        await display_requests_list(query.message, limit)

    elif action == "req_sel":
        request_id = int(parts[1])
        limit = int(parts[2])
        await display_request_details(query.message, request_id, limit)

    elif action == "req_act":
        act = parts[1]
        request_id = int(parts[2])
        limit = int(parts[3])

        keyboard = [[InlineKeyboardButton("◀️ Back to List", callback_data=f"req_list:{limit}")]]

        if act == "approve":
            await query.message.edit_text(f"⏳ Approving request #{request_id}...")
            res = overseerr.approve_request(request_id)
            if res:
                keyboard.append([InlineKeyboardButton("🔎 View Details", callback_data=f"req_sel:{request_id}:{limit}")])
                await query.message.edit_text(
                    f"✅ Request #{request_id} has been approved.",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="Markdown"
                )
            else:
                keyboard.append([InlineKeyboardButton("🔎 View Details", callback_data=f"req_sel:{request_id}:{limit}")])
                await query.message.edit_text(
                    f"❌ Failed to approve request #{request_id}.",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="Markdown"
                )

        elif act == "decline":
            await query.message.edit_text(f"⏳ Declining request #{request_id}...")
            res = overseerr.decline_request(request_id)
            if res:
                keyboard.append([InlineKeyboardButton("🔎 View Details", callback_data=f"req_sel:{request_id}:{limit}")])
                await query.message.edit_text(
                    f"✅ Request #{request_id} has been declined.",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="Markdown"
                )
            else:
                keyboard.append([InlineKeyboardButton("🔎 View Details", callback_data=f"req_sel:{request_id}:{limit}")])
                await query.message.edit_text(
                    f"❌ Failed to decline request #{request_id}.",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="Markdown"
                )

        elif act == "retry":
            await query.message.edit_text(f"⏳ Retrying request #{request_id}...")
            res = overseerr.retry_request(request_id)
            if res:
                keyboard.append([InlineKeyboardButton("🔎 View Details", callback_data=f"req_sel:{request_id}:{limit}")])
                await query.message.edit_text(
                    f"✅ Request #{request_id} is being retried.",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="Markdown"
                )
            else:
                keyboard.append([InlineKeyboardButton("🔎 View Details", callback_data=f"req_sel:{request_id}:{limit}")])
                await query.message.edit_text(
                    f"❌ Failed to retry request #{request_id}.",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="Markdown"
                )

        elif act == "delete":
            await query.message.edit_text(f"⏳ Deleting request #{request_id}...")
            res = overseerr.delete_request(request_id)
            if res:
                await query.message.edit_text(
                    f"✅ Request #{request_id} has been deleted.",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="Markdown"
                )
            else:
                keyboard.append([InlineKeyboardButton("🔎 View Details", callback_data=f"req_sel:{request_id}:{limit}")])
                await query.message.edit_text(
                    f"❌ Failed to delete request #{request_id}.",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="Markdown"
                )

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log the error and send a telegram message to notify the user/developer."""
    logger.error("Exception while handling an update:", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "❌ An unexpected error occurred. Please try again later."
            )
        except Exception:
            pass

def main():
    if not TELEGRAM_BOT_TOKEN or not OVERSEERR_API_KEY:
        print("CRITICAL: TELEGRAM_BOT_TOKEN and OVERSEERR_API_KEY must be set in environmental variables.")
        return

    logger.info("Starting Telegram Bot...")
    
    # Build application
    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    # Handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("seerr", seerr_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(handle_callback_query))

    # Error Handler
    application.add_error_handler(error_handler)

    # Run bot
    application.run_polling()

if __name__ == "__main__":
    main()
