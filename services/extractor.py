"""Media extraction service for video content processing.

Provides Unified extraction interfaces for YouTube caption scraping and Instagram Reel
audio download (via yt-dlp) followed by AI Speech-to-Text transcription.
"""

import re
import os
import asyncio
import logging
from youtube_transcript_api import YouTubeTranscriptApi
import yt_dlp
from openai import AsyncOpenAI
from pydantic import SecretStr
from config import settings

logger = logging.getLogger(__name__)


class MediaExtractorService:
    """Service for pulling transcripts and audio from video platforms (YouTube, Instagram)."""

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

    async def extract_transcript(self, url: str) -> str:
        """Main dispatcher for video transcript extraction based on platform.

        Args:
            url: The video URL to extract transcript from.

        Returns:
            Extracted video transcript text string.

        Raises:
            ValueError: If the platform URL is unsupported.
        """
        if self.is_youtube(url):
            return await self._extract_youtube(url)
        elif self.is_instagram(url):
            return await self._extract_instagram(url)
        else:
            raise ValueError("Unsupported media URL platform for extraction.")

    async def _extract_youtube(self, url: str) -> str:
        """Extracts captions from YouTube using YouTubeTranscriptApi.

        Args:
            url: YouTube video URL.

        Returns:
            Full transcript text string.
        """
        def _get():
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

        try:
            return await asyncio.to_thread(_get)
        except Exception as e:
            logger.error(f"Failed to extract YouTube transcript from {url}: {e}")
            raise RuntimeError(f"Could not retrieve YouTube transcript: {e}") from e

    async def _extract_instagram(self, url: str) -> str:
        """Downloads audio using yt-dlp and transcribes it via LiteLLM/Whisper.

        Args:
            url: Instagram Reel or Post URL.

        Returns:
            Transcribed text from video audio.
        """
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

        def _download():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
            return out_path

        logger.info(f"Downloading audio for IG Reel: {url}")
        audio_file = await asyncio.to_thread(_download)

        try:
            logger.info("Sending audio to LiteLLM/Whisper for transcription...")
            with open(audio_file, "rb") as file_payload:
                transcription = await self.ai_client.audio.transcriptions.create(
                    model="whisper-1",
                    file=file_payload,
                    response_format="text"
                )
            result_text = transcription
        except Exception as e:
            logger.error(f"Transcription failed: {e}")
            result_text = f"Transcription failed: {str(e)}"
        finally:
            if os.path.exists(audio_file):
                os.remove(audio_file)

        return result_text
