import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from services.overseerr import OverseerrClient, STATUS_MAP
from services.extractor import MediaExtractorService
from services.llm import LLMService


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
