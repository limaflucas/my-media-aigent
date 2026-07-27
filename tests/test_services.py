import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from services.overseerr import OverseerrClient, STATUS_MAP
from services.extractor import MediaExtractorService, WHISPER_TRANSCRIBE_PROMPT
from services.llm import LLMService
from models.media import VideoContentData


@pytest.mark.asyncio
async def test_overseerr_media_status_str():
    client = OverseerrClient(base_url="http://localhost:5055", api_key="test_key")
    assert client.get_media_status_str(None) == STATUS_MAP[1]
    assert client.get_media_status_str({"status": 5}) == STATUS_MAP[5]
    assert client.get_media_status_str({"status": 2}) == STATUS_MAP[2]
    await client.aclose()


def test_media_extractor_service_regex():
    assert MediaExtractorService.is_youtube("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert MediaExtractorService.is_youtube("https://youtu.be/dQw4w9WgXcQ")
    assert MediaExtractorService.is_instagram("https://www.instagram.com/reel/CXYZ123/")
    assert not MediaExtractorService.is_youtube("https://imdb.com/title/tt0111161")


def test_whisper_prompt_languages():
    assert "Português" in WHISPER_TRANSCRIBE_PROMPT
    assert "English" in WHISPER_TRANSCRIBE_PROMPT
    assert "Français" in WHISPER_TRANSCRIBE_PROMPT


def test_video_content_data_dto():
    dto = VideoContentData(
        transcript="Hello cinema fans",
        description="Top 10 movies of 2024",
        title="Movie Review Video"
    )
    assert dto.transcript == "Hello cinema fans"
    assert dto.description == "Top 10 movies of 2024"
    assert dto.title == "Movie Review Video"
