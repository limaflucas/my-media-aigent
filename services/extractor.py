"""Media extraction service for video content processing.

Provides Unified extraction interfaces for YouTube caption scraping, Instagram Reel audio download,
and video description extraction, followed by AI Speech-to-Text transcription via LiteLLM/Whisper.
"""

import re
import os
import asyncio
import logging
from typing import Optional, Tuple
from youtube_transcript_api import YouTubeTranscriptApi
import yt_dlp
from openai import AsyncOpenAI
from pydantic import SecretStr

from config import settings
from models.media import VideoContentData

logger = logging.getLogger(__name__)

WHISPER_TRANSCRIBE_PROMPT = (
    "Transcrição de áudio sobre filmes, séries, cinema e entretenimento em Português, Inglês ou Francês. "
    "Audio transcription about movies, TV shows, cinema and entertainment in English, Portuguese, or French. "
    "Transcription audio sur les films, séries, cinéma et divertissement en Français."
)


class MediaExtractorService:
    """Service for pulling transcripts, video descriptions, and audio from platforms (YouTube, Instagram)."""

    def __init__(self) -> None:
        """Initializes the MediaExtractorService with LiteLLM OpenAI client."""
        base_url = str(settings.LITELLM_BASE_URL)
        api_key = settings.LITELLM_API_KEY.get_secret_value() if isinstance(settings.LITELLM_API_KEY, SecretStr) else str(settings.LITELLM_API_KEY)
        self.ai_client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key
        )

    @staticmethod
    def is_youtube(url: str) -> bool:
        """Checks if the given URL belongs to YouTube or YouTube Shorts."""
        return bool(re.search(r'(youtube\.com|youtu\.be)', url))

    @staticmethod
    def is_instagram(url: str) -> bool:
        """Checks if the given URL belongs to an Instagram Reel, Post, or TV video."""
        return "instagram.com/reel" in url or "instagram.com/p" in url or "instagram.com/tv" in url

    async def extract_video_data(self, url: str) -> VideoContentData:
        """Main dispatcher for video transcript, title, and description extraction based on platform.

        Args:
            url: The video URL to extract data from.

        Returns:
            VideoContentData DTO containing transcript, description, and title.

        Raises:
            ValueError: If the platform URL is unsupported.
        """
        if self.is_youtube(url):
            return await self._extract_youtube_data(url)
        elif self.is_instagram(url):
            return await self._extract_instagram_data(url)
        else:
            raise ValueError("Unsupported media URL platform for extraction.")

    async def extract_transcript(self, url: str) -> str:
        """Backwards-compatible wrapper returning only the extracted transcript string."""
        data = await self.extract_video_data(url)
        return data.transcript

    def _fetch_yt_dlp_metadata(self, url: str) -> Tuple[Optional[str], Optional[str]]:
        """Scrapes video description and title using yt-dlp metadata extraction."""
        ydl_opts = {
            'skip_download': True,
            'quiet': True,
            'no_warnings': True,
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if info:
                    desc = info.get("description") or info.get("caption")
                    title = info.get("title")
                    return desc, title
        except Exception as e:
            logger.warning(f"yt-dlp metadata extraction failed for {url}: {e}")
        return None, None

    async def _extract_youtube_data(self, url: str) -> VideoContentData:
        """Extracts captions and video description from YouTube."""
        def _get_transcript():
            video_id_match = re.search(r'(?:v=|\/|shorts\/)([0-9A-Za-z_-]{11})', url)
            if not video_id_match:
                raise ValueError("Invalid YouTube URL: could not extract video ID.")
            video_id = video_id_match.group(1)

            if hasattr(YouTubeTranscriptApi, 'get_transcript'):
                snippets = YouTubeTranscriptApi.get_transcript(video_id)
            else:
                api = YouTubeTranscriptApi()
                if hasattr(api, 'fetch'):
                    snippets = api.fetch(video_id)
                elif hasattr(api, 'list'):
                    transcript_list = api.list(video_id)
                    snippets = transcript_list.find_transcript(['en']).fetch()
                else:
                    raise AttributeError("YouTubeTranscriptApi does not support fetch or get_transcript")

            text_parts = []
            for s in snippets:
                if hasattr(s, 'text'):
                    text_parts.append(s.text)
                elif isinstance(s, dict) and 'text' in s:
                    text_parts.append(s['text'])
                else:
                    text_parts.append(str(s))
            return " ".join(text_parts)

        # Execute transcript extraction and metadata scraping concurrently
        transcript_task = asyncio.to_thread(_get_transcript)
        metadata_task = asyncio.to_thread(self._fetch_yt_dlp_metadata, url)

        results = await asyncio.gather(transcript_task, metadata_task, return_exceptions=True)

        transcript_res = results[0]
        metadata_res = results[1]

        if isinstance(transcript_res, Exception):
            logger.error(f"Failed to extract YouTube transcript from {url}: {transcript_res}")
            raise RuntimeError(f"Could not retrieve YouTube transcript: {transcript_res}") from transcript_res

        description, title = (None, None)
        if isinstance(metadata_res, tuple):
            description, title = metadata_res

        return VideoContentData(
            transcript=transcript_res,
            description=description,
            title=title
        )

    async def _extract_instagram_data(self, url: str) -> VideoContentData:
        """Downloads audio using yt-dlp, extracts post description, and transcribes audio via LiteLLM/Whisper."""
        out_path = f"/tmp/ig_{hash(url)}.mp3"
        ydl_opts = {
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'outtmpl': out_path.replace('.mp3', ''),
            'quiet': True,
        }

        def _download_and_extract_metadata():
            desc = None
            title = None
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if info:
                    desc = info.get("description") or info.get("caption")
                    title = info.get("title")
            return out_path, desc, title

        logger.info(f"Downloading audio and metadata for IG Reel: {url}")
        audio_file, description, title = await asyncio.to_thread(_download_and_extract_metadata)

        try:
            logger.info("Sending audio to LiteLLM/Whisper for transcription with multilingual prompt...")
            with open(audio_file, "rb") as file_payload:
                transcription = await self.ai_client.audio.transcriptions.create(
                    model="whisper-1",
                    file=file_payload,
                    prompt=WHISPER_TRANSCRIBE_PROMPT,
                    response_format="text"
                )
            result_text = str(transcription)
        except Exception as e:
            logger.error(f"Transcription failed: {e}")
            result_text = f"Transcription failed: {str(e)}"
        finally:
            if os.path.exists(audio_file):
                os.remove(audio_file)

        return VideoContentData(
            transcript=result_text,
            description=description,
            title=title
        )
