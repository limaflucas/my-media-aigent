"""Telegram Bot Handler for Video AI Extraction and Media Identification."""

import logging
from telegram import Update
from telegram.ext import ContextTypes

from bot.handlers.identification import identify_and_present
from services.extractor import MediaExtractorService, NoVideoStreamError

logger = logging.getLogger(__name__)

extractor_service = MediaExtractorService()


async def _discard_status_message(status_msg) -> None:
    """Removes the progress message so the next pipeline can post its own."""
    try:
        await status_msg.delete()
    except Exception:
        pass


async def handle_video_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Handles YouTube, Instagram, and Facebook video links by extracting the transcript and
    description, then identifying movies/shows and presenting interactive Overseerr results.

    Links without a downloadable video stream (photo/text posts) raise during extraction and
    return False, letting the caller fall through to the social post pipeline.

    Args:
        update: Incoming Telegram update.
        context: Callback context.

    Returns:
        Boolean indicating whether the message was recognized and processed as a video link.
    """
    if not update.message or not update.message.text:
        return False

    text = update.message.text.strip()
    if not (
        extractor_service.is_youtube(text)
        or extractor_service.is_instagram(text)
        or extractor_service.is_facebook(text)
    ):
        return False

    status_msg = await update.message.reply_text("⏳ Extracting video content & transcript...")

    try:
        video_data = await extractor_service.extract_video_data(text)
        return await identify_and_present(
            update=update,
            context=context,
            status_msg=status_msg,
            transcript=video_data.transcript,
            description=video_data.description,
            source_label="video",
            source_icon="🎥",
        )
    except NotImplementedError as e:
        logger.info(f"Video extraction not fully implemented for link: {e}")
        await status_msg.edit_text(f"ℹ️ {str(e)}")
        return False
    except NoVideoStreamError as e:
        # Expected for photo/text posts; the social post pipeline picks these up next.
        logger.info(f"No video stream at link, deferring to post pipeline: {e}")
        await _discard_status_message(status_msg)
        return False
    except Exception as e:
        logger.warning(f"Video AI extraction failed: {e}. Falling through to post pipeline.", exc_info=True)
        await _discard_status_message(status_msg)
        return False
