import pytest
import yt_dlp
from unittest.mock import AsyncMock, patch, MagicMock
from services.overseerr import OverseerrClient, STATUS_MAP
from services.extractor import MediaExtractorService, WHISPER_TRANSCRIBE_PROMPT, NoVideoStreamError
from services.llm import LLMService
from services.post_extractor import SocialPostExtractorService
from models.media import VideoContentData, SocialPostContent


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


def test_media_extractor_service_is_facebook():
    assert MediaExtractorService.is_facebook("https://www.facebook.com/somepage/posts/12345")
    assert MediaExtractorService.is_facebook("https://facebook.com/photo.php?fbid=12345")
    assert not MediaExtractorService.is_facebook("https://www.instagram.com/p/CXYZ123/")
    assert not MediaExtractorService.is_facebook("https://imdb.com/title/tt0111161")


@pytest.mark.asyncio
async def test_extractor_raises_no_video_stream_for_photo_post():
    """A yt-dlp download failure becomes a routing signal, not a stack-trace-worthy error."""
    service = MediaExtractorService()
    with patch("services.extractor.yt_dlp.YoutubeDL") as mock_ydl:
        mock_ydl.return_value.__enter__.return_value.extract_info.side_effect = (
            yt_dlp.utils.DownloadError("ERROR: [Instagram] ABC: There is no video in this post")
        )
        with pytest.raises(NoVideoStreamError, match="no video in this post"):
            await service._extract_audio_transcription_data("https://www.instagram.com/p/ABC/")


def test_post_extractor_is_social_post():
    assert SocialPostExtractorService.is_social_post("https://www.instagram.com/p/Dba7ScGuPcU/")
    assert SocialPostExtractorService.is_social_post("https://www.facebook.com/3000YearsMovie/videos/591844145904138/")
    assert not SocialPostExtractorService.is_social_post("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert not SocialPostExtractorService.is_social_post("https://imdb.com/title/tt0111161")


def test_post_extractor_cleans_instagram_description():
    """Uses the real og:description format Instagram serves to link-preview crawlers."""
    raw = ('69K likes, 877 comments - eutopasada on July 30, 2026: '
           '"A série Luna Nera vem conquistando novos espectadores."')
    caption, author = SocialPostExtractorService._clean_og_text(raw)
    assert caption == "A série Luna Nera vem conquistando novos espectadores."
    assert author == "eutopasada"


def test_post_extractor_cleans_instagram_title():
    """Instagram's og:title wraps the caption differently from its og:description."""
    raw = 'EU TÔ PASSADA on Instagram: "A série Luna Nera vem conquistando novos espectadores."'
    caption, author = SocialPostExtractorService._clean_og_text(raw)
    assert caption == "A série Luna Nera vem conquistando novos espectadores."
    assert author == "EU TÔ PASSADA"


def test_post_extractor_cleans_facebook_title():
    """Facebook prefixes engagement counts and suffixes the page name."""
    raw = ("3.3K views · 98 reactions | “I'm beginning to wish we never met.” Starring "
           "Idris Elba and Tilda Swinton, watch a new clip from #3000YearsOfLonging | 3000 Years of Longing")
    caption, author = SocialPostExtractorService._clean_og_text(raw)
    assert caption.startswith("“I'm beginning to wish we never met.”")
    assert "#3000YearsOfLonging" in caption
    assert "3.3K views" not in caption
    assert author == "3000 Years of Longing"


def test_post_extractor_clean_og_text_passthrough():
    caption, author = SocialPostExtractorService._clean_og_text("A plain caption with no boilerplate")
    assert caption == "A plain caption with no boilerplate"
    assert author is None
    assert SocialPostExtractorService._clean_og_text(None) == ("", None)


def test_post_extractor_prefers_longer_caption_and_reads_image():
    """og:title beats a truncated og:description; both get cleaned before comparison."""
    html = '''
    <html><head>
      <meta property="og:title" content='Fan Page on Instagram: "Luna Nera is a hidden gem #witches"'>
      <meta property="og:description" content='69K likes, 12 comments - fanpage on July 30, 2026: "Luna Nera is..."'>
      <meta property="og:image" content="https://cdn.example.com/poster.jpg">
    </head></html>
    '''
    caption, author, title, image = SocialPostExtractorService()._parse_og_metadata(html)
    assert caption == "Luna Nera is a hidden gem #witches"
    assert author == "Fan Page"
    assert image == "https://cdn.example.com/poster.jpg"


def test_post_extractor_composes_description_with_hashtags_and_author():
    composed = SocialPostExtractorService._compose_description(
        "Luna Nera is a hidden gem #witches #netflix", "fanpage"
    )
    assert "Luna Nera is a hidden gem" in composed
    assert "Hashtags: #witches #netflix" in composed
    assert "Posted by: fanpage" in composed


@pytest.mark.asyncio
async def test_post_extractor_raises_when_nothing_extracted():
    """An empty scrape must raise so the caller falls back instead of prompting the LLM with nothing."""
    service = SocialPostExtractorService()
    with patch.object(service, "_fetch_html", new=AsyncMock(return_value="<html></html>")):
        with pytest.raises(RuntimeError, match="No caption or image"):
            await service.extract_post("https://www.instagram.com/p/EMPTY/")


@pytest.mark.asyncio
async def test_post_extractor_extract_post_returns_content():
    service = SocialPostExtractorService()
    html = '''<html><head>
      <meta property="og:title" content='Fan Page on Instagram: "Luna Nera #witches"'>
      <meta property="og:image" content="https://cdn.example.com/poster.jpg">
    </head></html>'''
    with patch.object(service, "_fetch_html", new=AsyncMock(return_value=html)), \
         patch.object(service, "_download_images_as_data_uris",
                      new=AsyncMock(return_value=["data:image/jpeg;base64,AAA"])):
        content = await service.extract_post("https://www.instagram.com/p/OK/")

    assert content.has_content
    assert "Luna Nera" in content.description
    assert "Posted by: Fan Page" in content.description
    assert content.images == ["data:image/jpeg;base64,AAA"]


def test_social_post_content_has_content():
    assert not SocialPostContent().has_content
    assert not SocialPostContent(description="   ").has_content
    assert SocialPostContent(description="a caption").has_content
    assert SocialPostContent(images=["data:image/jpeg;base64,AAA"]).has_content


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


@pytest.mark.asyncio
async def test_llm_extract_media_items_multimodal_payload():
    service = LLMService()
    mock_response = MagicMock()
    mock_response.choices = [
        MagicMock(message=MagicMock(content='{"media_items": [{"title": "Inception", "media_type": "movie"}]}'))
    ]
    service.client.chat.completions.create = AsyncMock(return_value=mock_response)

    items = await service.extract_media_items(
        transcript="",
        description="Great movie poster",
        images=["data:image/jpeg;base64,AAA"]
    )

    assert len(items) == 1
    assert items[0].title == "Inception"

    call_kwargs = service.client.chat.completions.create.call_args.kwargs
    user_content = call_kwargs["messages"][1]["content"]
    assert isinstance(user_content, list)
    assert user_content[0] == {"type": "text", "text": "Description:\nGreat movie poster"}
    assert user_content[1] == {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAA"}}


@pytest.mark.asyncio
async def test_llm_extract_media_items_retries_without_images_on_failure():
    service = LLMService()
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content='{"media_items": []}'))]
    service.client.chat.completions.create = AsyncMock(
        side_effect=[Exception("vision not supported"), mock_response]
    )

    items = await service.extract_media_items(
        transcript="",
        description="Some caption",
        images=["data:image/jpeg;base64,AAA"]
    )

    assert items == []
    assert service.client.chat.completions.create.call_count == 2
    second_call_content = service.client.chat.completions.create.call_args_list[1].kwargs["messages"][1]["content"]
    assert isinstance(second_call_content, str)
