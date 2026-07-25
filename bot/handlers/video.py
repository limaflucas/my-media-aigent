"""Telegram Bot Handler for Video AI Extraction and Media Identification."""

import asyncio
import logging
from telegram import Update
from telegram.ext import ContextTypes
from rapidfuzz import fuzz

from services.extractor import MediaExtractorService
from services.llm import LLMService
from services.overseerr import OverseerrClient

logger = logging.getLogger(__name__)

extractor_service = MediaExtractorService()
llm_service = LLMService()
overseerr = OverseerrClient()


async def handle_video_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Handles YouTube and Instagram links by extracting transcript, identifying movies/shows via LLM,
    searching Overseerr concurrently, and presenting interactive search results.

    Args:
        update: Incoming Telegram update.
        context: Callback context.

    Returns:
        Boolean indicating whether the message was recognized and processed as a video link.
    """
    if not update.message or not update.message.text:
        return False

    text = update.message.text.strip()
    if not (extractor_service.is_youtube(text) or extractor_service.is_instagram(text)):
        return False

    status_msg = await update.message.reply_text("⏳ Extracting video transcript...")

    try:
        # 1. Extract transcript
        transcript = await extractor_service.extract_transcript(text)
        await status_msg.edit_text("🤖 Analyzing video content for movies & TV shows...")

        # 2. Extract media items mentioned in video using LLM
        extracted_items = await llm_service.extract_media_items(transcript)
        if not extracted_items:
            await status_msg.edit_text("ℹ️ No movies or TV shows were identified in this video.")
            return True

        await status_msg.edit_text(f"🔍 Searching Overseerr for {len(extracted_items)} title(s) found in video...")

        # 3. Perform parallel concurrent searches on Overseerr for all extracted titles
        search_tasks = [overseerr.search(item.title) for item in extracted_items]
        search_results_list = await asyncio.gather(*search_tasks, return_exceptions=True)

        matched_results = []
        seen_ids = set()

        for item, results in zip(extracted_items, search_results_list):
            if not results or isinstance(results, Exception):
                continue

            # Sort search results by similarity to extracted title, year, and media type
            def match_score(r: dict) -> int:
                r_title = r.get("title") or r.get("name") or ""
                score = fuzz.token_sort_ratio(item.title.lower(), r_title.lower())
                r_date = r.get("releaseDate") or r.get("firstAirDate") or ""
                if item.year and r_date.startswith(str(item.year)):
                    score += 15
                if item.media_type and r.get("mediaType") == item.media_type:
                    score += 10
                return score

            best_match = max(results, key=match_score)
            res_id = best_match.get("id")
            if res_id and res_id not in seen_ids:
                seen_ids.add(res_id)
                matched_results.append(best_match)

        # 4. Display matched movies/TV shows as interactive search results
        if matched_results:
            from bot.handlers.overseerr import present_search_results

            header_title = f"🎥 **Movies & TV Shows Mentioned in Video:**\n_Found {len(matched_results)} matching title(s)_"
            await present_search_results(
                update=update,
                context=context,
                message_to_edit=status_msg,
                query="Video Analysis",
                results=matched_results,
                page=1,
                header_title=header_title
            )
            return True
        else:
            titles_list = ", ".join([f"_{i.title}_" for i in extracted_items])
            await status_msg.edit_text(
                f"❓ Extracted {len(extracted_items)} title(s) ({titles_list}), but none could be matched on Overseerr."
            )
            return True

    except NotImplementedError as e:
        logger.info(f"Video extraction not fully implemented for link: {e}")
        await status_msg.edit_text(f"ℹ️ {str(e)}")
        return False
    except Exception as e:
        logger.warning(f"Video AI extraction failed: {e}. Passing to media parser.", exc_info=True)
        try:
            await status_msg.delete()
        except Exception:
            pass
        return False
