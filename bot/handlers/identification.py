"""Shared media identification stage for the extraction pipelines.

Both the video pipeline (bot/handlers/video.py) and the social post pipeline (bot/handlers/post.py)
converge here once their content is extracted: identify titles via LLM, search Overseerr for each,
then hand the matches to the interactive result presenter. Extraction stays in the pipeline modules.
"""

import asyncio
import logging
from typing import List, Optional
from telegram import Update
from telegram.ext import ContextTypes
from rapidfuzz import fuzz

from models.media import ExtractedMediaItem
from services.llm import LLMService
from services.overseerr import OverseerrClient

logger = logging.getLogger(__name__)

llm_service = LLMService()
overseerr = OverseerrClient()


def _best_overseerr_match(item: ExtractedMediaItem, results: List[dict]) -> Optional[dict]:
    """Picks the Overseerr search result closest to an extracted title, by name, year, and type."""
    def match_score(result: dict) -> int:
        result_title = result.get("title") or result.get("name") or ""
        score = fuzz.token_sort_ratio(item.title.lower(), result_title.lower())
        result_date = result.get("releaseDate") or result.get("firstAirDate") or ""
        if item.year and result_date.startswith(str(item.year)):
            score += 15
        if item.media_type and result.get("mediaType") == item.media_type:
            score += 10
        return score

    return max(results, key=match_score) if results else None


async def identify_and_present(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    status_msg,
    transcript: str = "",
    description: Optional[str] = None,
    images: Optional[List[str]] = None,
    source_label: str = "video",
    source_icon: str = "🎥",
) -> bool:
    """Identifies movies/TV shows in extracted content and presents matching Overseerr results.

    Args:
        update: Incoming Telegram update.
        context: Callback context.
        status_msg: The progress message to edit as the stages advance.
        transcript: Transcribed speech, if the source had audio.
        description: Video description or post caption.
        images: Optional base64 data-URI images for vision-based identification.
        source_label: Human-readable source noun used in status messages ("video", "post").
        source_icon: Emoji prefix for the results header.

    Returns:
        True once the message has been handled and a reply sent.
    """
    await status_msg.edit_text(f"🤖 Analyzing {source_label} content for movies & TV shows...")

    extracted_items = await llm_service.extract_media_items(
        transcript=transcript,
        description=description,
        images=images,
    )
    if not extracted_items:
        await status_msg.edit_text(f"ℹ️ No movies or TV shows were identified in this {source_label}.")
        return True

    await status_msg.edit_text(
        f"🔍 Searching Overseerr for {len(extracted_items)} title(s) found in {source_label}..."
    )

    # Perform parallel concurrent searches on Overseerr for all extracted titles
    search_results_list = await asyncio.gather(
        *(overseerr.search(item.title) for item in extracted_items),
        return_exceptions=True,
    )

    matched_results = []
    seen_ids = set()
    for item, results in zip(extracted_items, search_results_list):
        if not results or isinstance(results, Exception):
            continue
        best_match = _best_overseerr_match(item, results)
        if not best_match:
            continue
        result_id = best_match.get("id")
        if result_id and result_id not in seen_ids:
            seen_ids.add(result_id)
            matched_results.append(best_match)

    if not matched_results:
        titles_list = ", ".join(f"_{item.title}_" for item in extracted_items)
        await status_msg.edit_text(
            f"❓ Extracted {len(extracted_items)} title(s) ({titles_list}), "
            "but none could be matched on Overseerr."
        )
        return True

    # Imported here to avoid a circular import with the main Overseerr handler module
    from bot.handlers.overseerr import present_search_results

    noun = source_label.capitalize()
    await present_search_results(
        update=update,
        context=context,
        message_to_edit=status_msg,
        query=f"{noun} Analysis",
        results=matched_results,
        page=1,
        header_title=(
            f"{source_icon} **Movies & TV Shows Mentioned in {noun}:**\n"
            f"_Found {len(matched_results)} matching title(s)_"
        ),
    )
    return True
