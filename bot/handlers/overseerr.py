import re
import time
import logging
from io import BytesIO
from typing import Optional, Any, List, Dict

import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from rapidfuzz import fuzz

from bot.parser import extract_media_info_from_url
from services.overseerr import OverseerrClient

logger = logging.getLogger(__name__)

TMDB_IMG_BASE = "https://image.tmdb.org/t/p/w500"
PAGE_SIZE = 7
CONFIDENCE_THRESHOLD = 80

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

PROVIDER_EMOJIS = {
    "netflix": "🔴 Netflix",
    "netflix basic with ads": "🔴 Netflix",
    "amazon prime video": "🔵 Prime Video",
    "prime video": "🔵 Prime Video",
    "disney plus": "💎 Disney+",
    "disney+": "💎 Disney+",
    "hbo max": "🟣 Max",
    "max": "🟣 Max",
    "apple tv": "🍏 Apple TV",
    "apple tv plus": "🍏 Apple TV+",
    "apple tv+": "🍏 Apple TV+",
    "paramount plus": "🔷 Paramount+",
    "paramount+": "🔷 Paramount+",
    "paramount plus apple tv channel": "🔷 Paramount+",
    "crunchyroll": "🟠 Crunchyroll",
    "hulu": "🟢 Hulu",
    "peacock": "🦚 Peacock",
    "peacock premium": "🦚 Peacock",
    "star+": "⭐ Star+",
    "globoplay": "🔴 Globoplay",
    "youtube": "🔺 YouTube",
}

overseerr = OverseerrClient()


def set_ttl_item(user_data: dict, key: str, value: Any, ttl_seconds: int = 2700):
    """Stores an item in user_data with an expiration timestamp (default 45 mins)."""
    user_data[key] = {
        "value": value,
        "expires_at": time.time() + ttl_seconds
    }


def get_ttl_item(user_data: dict, key: str) -> Any:
    """Retrieves an item from user_data if it has not expired yet."""
    item = user_data.get(key)
    if not item:
        return None
    if time.time() > item.get("expires_at", 0):
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


def build_poster_url(details: dict) -> Optional[str]:
    """Builds a full TMDB image URL from posterPath or backdropPath."""
    path = details.get("posterPath") or details.get("backdropPath")
    if path:
        return f"{TMDB_IMG_BASE}{path}"
    return None


def get_streaming_providers(details: dict) -> List[str]:
    """Extracts watch providers from details prioritizing BR/US regions."""
    providers = []
    watch_providers = details.get("watchProviders", [])
    if not watch_providers:
        return providers

    target_region = None
    for region_code in ["BR", "US"]:
        for wp in watch_providers:
            if wp.get("iso_3166_1") == region_code:
                if wp.get("flatrate"):
                    target_region = wp
                    break
        if target_region:
            break

    if not target_region:
        for wp in watch_providers:
            if wp.get("flatrate"):
                target_region = wp
                break

    if target_region and "flatrate" in target_region:
        for item in target_region["flatrate"]:
            name = item.get("name")
            if name and name not in providers:
                providers.append(name)

    return providers


def format_providers(providers: List[str]) -> str:
    """Formats watch provider names mapping them to emojis where applicable."""
    formatted = []
    for p in providers:
        p_lower = p.lower().strip()
        emoji_name = None
        for key, val in PROVIDER_EMOJIS.items():
            if key in p_lower:
                emoji_name = val
                break
        if emoji_name:
            if emoji_name not in formatted:
                formatted.append(emoji_name)
        else:
            formatted.append(f"📺 {p}")
    return ", ".join(formatted)


def get_movie_certification(details: dict) -> Optional[str]:
    """Extracts movie rating certification prioritizing BR then US regions."""
    results = details.get("releases", {}).get("results", [])
    for region in ["BR", "US"]:
        for res in results:
            if res.get("iso_3166_1") == region:
                for rd in res.get("release_dates", []):
                    cert = rd.get("certification")
                    if cert:
                        return cert
    for res in results:
        for rd in res.get("release_dates", []):
            cert = rd.get("certification")
            if cert:
                return cert
    return None


def get_tv_certification(details: dict) -> Optional[str]:
    """Extracts TV show content rating certification prioritizing BR then US regions."""
    results = details.get("contentRatings", {}).get("results", [])
    for region in ["BR", "US"]:
        for res in results:
            if res.get("iso_3166_1") == region:
                rating = res.get("rating")
                if rating:
                    return rating
    for res in results:
        rating = res.get("rating")
        if rating:
            return rating
    return None


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
        "• Netflix (e.g., `netflix.com/title/...`)\n"
        "• YouTube videos/shorts\n"
        "• Instagram posts/reels/videos\n"
        "• Facebook videos/posts\n"
        "• Any web page with movie/TV metadata\n\n"
        "Alternatively, you can just type the **title** of the movie/show, and I will search for it directly!\n\n"
        "ℹ️ **Requests Management:**\n"
        "Use `/seerr list` to view and manage the last 5 requests, `/seerr [number]` for a custom amount, or `/seerr ?` for help."
    )
    await update.message.reply_text(welcome_text, parse_mode="Markdown")


async def display_requests_list(message, limit: int, skip: int = 0):
    """Fetches and displays the list of recent requests."""
    try:
        data = await overseerr.get_requests(take=limit, skip=skip)
        if data is None:
            await message.edit_text("❌ **Failed to connect to Seerr.** Please check connection details or logs.", parse_mode="Markdown")
            return
        if "results" not in data or not data["results"]:
            await message.edit_text("📭 **No requests found on Seerr.**", parse_mode="Markdown")
            return

        results = data["results"]
        text_lines = [f"📋 **Last {len(results)} Requests on Seerr:**\n"]
        keyboard = []

        for req in results:
            req_id = req.get("id")
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

            title = None
            year = None
            try:
                if media_type == "movie":
                    details = await overseerr.get_movie_details(tmdb_id)
                else:
                    details = await overseerr.get_tv_details(tmdb_id)
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
        req = await overseerr.get_request(request_id)
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

        title = None
        year = None
        overview = None
        try:
            if media_type == "movie":
                details = await overseerr.get_movie_details(tmdb_id)
            else:
                details = await overseerr.get_tv_details(tmdb_id)
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


async def seerr_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lists recent requests from Seerr or shows help."""
    message = update.effective_message
    if not message:
        return

    args = context.args
    if not args:
        await message.reply_text(
            "⚠️ The `/seerr` command requires a parameter.\n"
            "Use `/seerr list` to show the last 5 requests, `/seerr [number]` to show a custom amount, or `/seerr ?` for help.",
            parse_mode="Markdown"
        )
        return

    param = args[0].strip().lower()

    if param == "?":
        help_text = (
            "📖 **Available Commands:**\n\n"
            "• `/start` — Start the bot, display welcome message, and list supported links.\n"
            "• `/seerr list` — View and manage the last 5 requests from Seerr.\n"
            "• `/seerr [number]` — View and manage the last `[number]` requests from Seerr (max 20).\n"
            "• `/seerr ?` — Show this help message."
        )
        await message.reply_text(help_text, parse_mode="Markdown")
        return

    if param == "list":
        limit = 5
    else:
        try:
            val = int(param)
            if 1 <= val <= 20:
                limit = val
            else:
                await message.reply_text("⚠️ Please specify a number between 1 and 20.")
                return
        except ValueError:
            await message.reply_text("⚠️ Invalid parameter. Use `/seerr list`, `/seerr [number]`, or `/seerr ?` for help.")
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
    target_type: str = None,
    page: int = 1,
    header_title: str = None
):
    """Formats and displays search results with pagination (7 per page)."""
    def sort_key(item):
        score = 0
        media_type = item.get("mediaType", "")
        release_date = item.get("releaseDate") or item.get("firstAirDate") or ""
        if target_year and release_date.startswith(str(target_year)):
            score += 10
        if target_type and media_type == target_type:
            score += 5
        return score

    sorted_results = sorted(results, key=sort_key, reverse=True)
    total = len(sorted_results)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(1, min(page, total_pages))

    start = (page - 1) * PAGE_SIZE
    page_items = sorted_results[start:start + PAGE_SIZE]

    set_ttl_item(context.user_data, "last_search_query", query)
    set_ttl_item(context.user_data, "last_search_results", sorted_results)
    set_ttl_item(context.user_data, "last_search_page", page)

    keyboard = []
    for item in page_items:
        tmdb_id = item.get("id")
        media_type = item.get("mediaType", "movie")
        title = item.get("title") or item.get("name")
        release_date = item.get("releaseDate") or item.get("firstAirDate")
        year = release_date.split("-")[0] if release_date else "N/A"
        emoji = "🎬" if media_type == "movie" else "📺"
        button_text = f"{emoji} {title} ({year})"
        callback_data = f"search_sel:{media_type}:{tmdb_id}"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])

    nav_row = []
    if page > 1:
        nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"search_page:{page-1}"))
    nav_row.append(InlineKeyboardButton(f"📄 {page}/{total_pages}", callback_data="noop"))
    if page < total_pages:
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"search_page:{page+1}"))
    if nav_row:
        keyboard.append(nav_row)

    keyboard.append([InlineKeyboardButton("❌ Cancel search", callback_data="cancel")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    header = header_title if header_title else f"🔍 **Search Results for:** _'{query}'_"
    await message_to_edit.edit_text(
        f"{header}\nChoose the correct item to request:\n\n_Page {page} of {total_pages}_",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )


async def show_confirmation_card(
    chat_id: int,
    media_type: str,
    tmdb_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    origin: str = "search"
):
    """Displays a confirmation card with poster, Seerr data, and Confirm/Cancel buttons."""
    try:
        if media_type == "movie":
            details = await overseerr.get_movie_details(tmdb_id)
        else:
            details = await overseerr.get_tv_details(tmdb_id)

        if not details:
            await context.bot.send_message(chat_id, "❌ Could not load media details for confirmation.")
            return

        title = details.get("title") if media_type == "movie" else details.get("name")
        release_date = details.get("releaseDate") if media_type == "movie" else details.get("firstAirDate")
        year = release_date.split("-")[0] if release_date else "Unknown"
        tagline = details.get("tagline")
        overview = details.get("overview", "No overview available.")
        overview = overview[:300] + "…" if len(overview) > 300 else overview

        media_info = details.get("mediaInfo")
        status_str = overseerr.get_media_status_str(media_info)

        if media_type == "movie":
            runtime = details.get("runtime")
            duration = f"{runtime}m" if runtime else "Unknown"
        else:
            episode_run_time = details.get("episodeRunTime", [])
            if isinstance(episode_run_time, list) and episode_run_time:
                duration = f"{episode_run_time[0]}m per episode"
            else:
                duration = "Unknown"

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
                credits = details.get("credits", {})
                crew = credits.get("crew", []) if isinstance(credits, dict) else []
                directors = [member.get("name") for member in crew if isinstance(member, dict) and member.get("job") in ["Director", "Creator", "Series Director"]]

        director_str = ", ".join(directors) if directors else "Unknown"

        lang_code = details.get("originalLanguage", "unknown")
        language = LANGUAGE_MAP.get(lang_code.lower(), lang_code.upper())

        genres = [g.get("name") for g in details.get("genres", []) if g.get("name")]
        genres_str = ", ".join(genres) if genres else "Unknown"

        cert = get_movie_certification(details) if media_type == "movie" else get_tv_certification(details)
        cert_str = cert if cert else "Not Rated"

        ratings = await overseerr.get_ratings(media_type, tmdb_id)
        vote_average = details.get("voteAverage")
        if vote_average:
            ratings["tmdb"] = vote_average

        ratings_parts = []
        if ratings["tmdb"] is not None:
            ratings_parts.append(f"⭐️ **TMDb**: {ratings['tmdb']:.1f}/10")
        if ratings["rt_critics"] is not None:
            aud = f" (🍿 {ratings['rt_audience']}%" if ratings['rt_audience'] is not None else ""
            ratings_parts.append(f"🍅 **RT**: {ratings['rt_critics']}%{aud}{')' if aud else ''}")
        if ratings["imdb"] is not None:
            ratings_parts.append(f"🎬 **IMDb**: {ratings['imdb']:.1f}/10")

        ratings_line = "  |  ".join(ratings_parts) if ratings_parts else None

        caption_lines = []
        icon = "🎬" if media_type == "movie" else "📺"
        caption_lines.append(f"{icon} **{title} ({year})**")

        if tagline:
            caption_lines.append(f"_“{tagline}”_\n")
        else:
            caption_lines.append("")

        if ratings_line:
            caption_lines.append(f"{ratings_line}\n")

        caption_lines.append(f"• 🏷️ **Genres**: {genres_str}")
        caption_lines.append(f"• 🔞 **Classification**: {cert_str}")
        caption_lines.append(f"• ⏱️ **Duration**: {duration}")
        caption_lines.append(f"• 📣 **Director/Creator**: {director_str}")
        caption_lines.append(f"• 🗣️ **Original Language**: {language}")
        caption_lines.append(f"• 📡 **Status**: {status_str}")

        providers = get_streaming_providers(details)
        if providers:
            providers_str = format_providers(providers)
            caption_lines.append(f"• 🖥️ **Where to Watch**: {providers_str}")

        caption_lines.append(f"\n📖 **Plot**:\n_{overview}_\n")
        caption_lines.append("Confirm adding this to your library?")

        caption = "\n".join(caption_lines)

        keyboard = [
            [
                InlineKeyboardButton("✅ Confirm", callback_data=f"do_req:{media_type}:{tmdb_id}"),
                InlineKeyboardButton("❌ Cancel", callback_data="cancel"),
            ]
        ]
        if get_ttl_item(context.user_data, "last_search_results"):
            keyboard.append([
                InlineKeyboardButton("◀️ Return to results list", callback_data="search_ret")
            ])
        reply_markup = InlineKeyboardMarkup(keyboard)

        poster_url = build_poster_url(details)
        if poster_url:
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    img_response = await client.get(poster_url)
                    img_response.raise_for_status()
                    photo_bytes = BytesIO(img_response.content)
                    photo_bytes.seek(0)
                    await context.bot.send_photo(
                        chat_id=chat_id,
                        photo=photo_bytes,
                        caption=caption,
                        parse_mode="Markdown",
                        reply_markup=reply_markup,
                    )
                    return
            except Exception as e:
                logger.warning(f"Failed to send photo for TMDB {tmdb_id}: {e}. Falling back to text.")

        await context.bot.send_message(
            chat_id=chat_id,
            text=caption,
            parse_mode="Markdown",
            reply_markup=reply_markup,
        )
    except Exception as e:
        logger.error(f"Error showing confirmation card: {e}", exc_info=True)
        await context.bot.send_message(chat_id, "❌ An error occurred while preparing the confirmation.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Processes incoming messages. Handles video extraction or direct Overseerr search."""
    cleanup_expired_items(context.user_data)
    if not update.message or not update.message.text:
        return

    message_text = update.message.text.strip()

    # Try processing via Video AI handler first if YouTube or Instagram
    from bot.handlers.video import handle_video_message
    video_handled = await handle_video_message(update, context)
    if video_handled:
        return

    # Look for URLs in the message
    urls = re.findall(r"https?://[^\s]+", message_text)

    # If no URL is found, treat message as direct search query
    if not urls:
        query = message_text.strip()
        if len(query) < 2:
            return

        processing_msg = await update.message.reply_text("🔍 **Searching Overseerr...**", parse_mode="Markdown")
        try:
            results = await overseerr.search(query)
            if not results:
                await processing_msg.edit_text(
                    f"❌ No results found on Overseerr for **'{query}'**.",
                    parse_mode="Markdown"
                )
                return

            set_ttl_item(context.user_data, "last_search_query", query)
            set_ttl_item(context.user_data, "last_search_results", results)
            set_ttl_item(context.user_data, "last_search_page", 1)

            if len(results) == 1:
                tmdb_id = results[0]["id"]
                media_type = results[0].get("mediaType", "movie")
                await processing_msg.delete()
                await show_confirmation_card(
                    chat_id=update.effective_chat.id,
                    media_type=media_type,
                    tmdb_id=tmdb_id,
                    context=context,
                    origin="search"
                )
            else:
                await present_search_results(update, context, processing_msg, query, results, page=1)
        except Exception as e:
            logger.error(f"Search failed: {e}", exc_info=True)
            await processing_msg.edit_text("❌ An error occurred while searching.")
        return

    # URL processing fallback
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

        if media_info.get("source") == "tmdb_url":
            tmdb_id = media_info["tmdb_id"]
            media_type = media_info["media_type"]
            await processing_msg.delete()
            await show_confirmation_card(
                chat_id=update.effective_chat.id,
                media_type=media_type,
                tmdb_id=tmdb_id,
                context=context,
                origin="tmdb_url"
            )
            return

        title = media_info.get("title", "")
        year = media_info.get("year")
        media_type_hint = media_info.get("media_type")

        if not title:
            await processing_msg.edit_text(
                "❌ Could not extract a title from that link.",
                parse_mode="Markdown"
            )
            return

        results = await overseerr.search(title)
        if not results:
            await processing_msg.edit_text(
                f"❌ No results found on Seerr for **'{title}'**.",
                parse_mode="Markdown"
            )
            return

        if media_info.get("source") in ("youtube_oembed", "social_og", "meta_tags"):
            best = max(results, key=lambda r: fuzz.token_sort_ratio(
                title.lower(),
                (r.get("title") or r.get("name") or "").lower()
            ))
            best_title = best.get("title") or best.get("name") or ""
            score = fuzz.token_sort_ratio(title.lower(), best_title.lower())

            if score < CONFIDENCE_THRESHOLD:
                await processing_msg.edit_text(
                    f"❓ Couldn't confidently match _'{title}'_ to a movie/TV show.\n"
                    f"Best match was _'{best_title}'_ with {score}% similarity.\n\n"
                    f"Try searching by title directly.",
                    parse_mode="Markdown"
                )
                return

            if len(results) == 1:
                tmdb_id = best["id"]
                media_type = best.get("mediaType", "movie")
                set_ttl_item(context.user_data, "last_search_query", title)
                set_ttl_item(context.user_data, "last_search_results", results)
                set_ttl_item(context.user_data, "last_search_page", 1)
                await processing_msg.delete()
                await show_confirmation_card(
                    chat_id=update.effective_chat.id,
                    media_type=media_type,
                    tmdb_id=tmdb_id,
                    context=context,
                    origin="search"
                )
                return

        set_ttl_item(context.user_data, "last_search_query", title)
        set_ttl_item(context.user_data, "last_search_results", results)
        set_ttl_item(context.user_data, "last_search_page", 1)

        if len(results) == 1:
            tmdb_id = results[0]["id"]
            media_type = results[0].get("mediaType", "movie")
            await processing_msg.delete()
            await show_confirmation_card(
                chat_id=update.effective_chat.id,
                media_type=media_type,
                tmdb_id=tmdb_id,
                context=context,
                origin="search"
            )
        else:
            await present_search_results(
                update=update,
                context=context,
                message_to_edit=processing_msg,
                query=title,
                results=results,
                target_year=year,
                target_type=media_type_hint,
                page=1
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

    if data == "noop":
        return

    if data == "cancel":
        context.user_data.pop("last_search_query", None)
        context.user_data.pop("last_search_results", None)
        context.user_data.pop("last_search_page", None)
        try:
            await query.message.delete()
        except Exception:
            pass
        return

    parts = data.split(":")
    action = parts[0]

    if action in ("sel", "req", "search_sel", "search_req", "confirm_req"):
        media_type = parts[1]
        tmdb_id = int(parts[2])
        await query.message.delete()
        await show_confirmation_card(query.message.chat_id, media_type, tmdb_id, context, origin=action)

    elif action == "do_req":
        media_type = parts[1]
        tmdb_id = int(parts[2])

        first_line = (query.message.caption or query.message.text or "").split("\n")[0]
        title = first_line.replace("**", "").replace("🧾", "").strip()

        try:
            await query.message.edit_caption(caption="⏳ **Submitting request...**", parse_mode="Markdown")
        except Exception:
            try:
                await query.message.edit_text("⏳ **Submitting request...**", parse_mode="Markdown")
            except Exception:
                pass

        result = await overseerr.request_media(media_type, tmdb_id)
        if result:
            context.user_data.pop("last_search_query", None)
            context.user_data.pop("last_search_results", None)
            context.user_data.pop("last_search_page", None)

            success_text = f"🎉 **Request Submitted Successfully!**\n\n**{title}** has been requested in Seerr."
            try:
                await query.message.edit_caption(caption=success_text, parse_mode="Markdown", reply_markup=None)
            except Exception:
                await query.message.edit_text(success_text, parse_mode="Markdown", reply_markup=None)
        else:
            error_text = f"❌ **Failed to request {title}.**\n\nPlease verify Overseerr API connection or logs."
            try:
                await query.message.edit_caption(
                    caption=error_text,
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("✅ Retry", callback_data=f"do_req:{media_type}:{tmdb_id}"),
                        InlineKeyboardButton("❌ Cancel", callback_data="cancel"),
                    ]])
                )
            except Exception:
                await query.message.edit_text(error_text, parse_mode="Markdown")

    elif action == "search_ret":
        query_text = get_ttl_item(context.user_data, "last_search_query")
        results = get_ttl_item(context.user_data, "last_search_results")
        page = get_ttl_item(context.user_data, "last_search_page") or 1
        if query_text and results:
            await present_search_results(update, context, query.message, query_text, results, page=page)
        else:
            await query.message.edit_text("⚠️ No search history found (or it has expired). Please search again by typing the title.")

    elif action == "search_page":
        page = int(parts[1])
        query_text = get_ttl_item(context.user_data, "last_search_query")
        results = get_ttl_item(context.user_data, "last_search_results")
        if query_text and results:
            await present_search_results(update, context, query.message, query_text, results, page=page)
        else:
            await query.message.edit_text("⚠️ Search expired. Please search again by typing the title.")

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
            res = await overseerr.approve_request(request_id)
            status_msg = "approved" if res else "fail"
        elif act == "decline":
            await query.message.edit_text(f"⏳ Declining request #{request_id}...")
            res = await overseerr.decline_request(request_id)
            status_msg = "declined" if res else "fail"
        elif act == "retry":
            await query.message.edit_text(f"⏳ Retrying request #{request_id}...")
            res = await overseerr.retry_request(request_id)
            status_msg = "retried" if res else "fail"
        elif act == "delete":
            await query.message.edit_text(f"⏳ Deleting request #{request_id}...")
            res = await overseerr.delete_request(request_id)
            status_msg = "deleted" if res else "fail"
        else:
            res = None
            status_msg = "fail"

        keyboard.append([InlineKeyboardButton("🔎 View Details", callback_data=f"req_sel:{request_id}:{limit}")])

        if res:
            await query.message.edit_text(
                f"✅ Request #{request_id} has been {status_msg}.",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )
        else:
            await query.message.edit_text(
                f"❌ Action failed for request #{request_id}.",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )
