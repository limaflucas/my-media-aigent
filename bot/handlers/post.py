"""Telegram Bot Handler for Instagram/Facebook post identification.

Handles social posts that carry no downloadable video stream (photo and text posts) by scraping
their caption and image, then delegating to the shared identification stage. Video content is the
video pipeline's responsibility (bot/handlers/video.py) and is never processed here.
"""

import logging
from telegram import Update
from telegram.ext import ContextTypes

from bot.handlers.identification import identify_and_present
from services.post_extractor import SocialPostExtractorService

logger = logging.getLogger(__name__)

post_extractor = SocialPostExtractorService()


async def _discard_status_message(status_msg) -> None:
    """Removes the progress message so the fallback parser can post its own reply."""
    try:
        await status_msg.delete()
    except Exception:
        pass


async def handle_post_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Handles Instagram/Facebook post links by scraping caption and image, then identifying media.

    Runs after the video pipeline has declined the link, so reaching here means no video stream was
    available. Returns False on failure so the caller can fall back to plain URL metadata parsing.

    Args:
        update: Incoming Telegram update.
        context: Callback context.

    Returns:
        Boolean indicating whether the message was recognized and processed as a social post.
    """
    if not update.message or not update.message.text:
        return False

    text = update.message.text.strip()
    if not post_extractor.is_social_post(text):
        return False

    status_msg = await update.message.reply_text("⏳ Extracting post content...")

    try:
        post = await post_extractor.extract_post(text)
        return await identify_and_present(
            update=update,
            context=context,
            status_msg=status_msg,
            description=post.description,
            images=post.images,
            source_label="post",
            source_icon="📸",
        )
    except RuntimeError as e:
        # Nothing scrapable (login-walled, restricted, or a post with no caption or image).
        logger.info(f"No post content available, passing to media parser: {e}")
        await _discard_status_message(status_msg)
        return False
    except Exception as e:
        logger.warning(f"Post extraction failed: {e}. Passing to media parser.", exc_info=True)
        await _discard_status_message(status_msg)
        return False
