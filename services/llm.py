import json
import re
import logging
from typing import Any, List, Optional, Union
from openai import AsyncOpenAI
from pydantic import SecretStr

from config import settings
from models.media import ExtractedMediaItem, VideoMediaExtractionResult

logger = logging.getLogger(__name__)


class LLMService:
    """Service for LiteLLM inference, media extraction, and transcript analysis."""

    def __init__(self) -> None:
        """Initializes the LLMService with LiteLLM OpenAI client."""
        base_url = str(settings.LITELLM_BASE_URL)
        api_key = settings.LITELLM_API_KEY.get_secret_value() if isinstance(settings.LITELLM_API_KEY, SecretStr) else str(settings.LITELLM_API_KEY)
        self.client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key
        )

    def _get_model_name(self) -> str:
        """Returns the configured model string."""
        if isinstance(settings.DEFAULT_LLM_MODEL, SecretStr):
            return settings.DEFAULT_LLM_MODEL.get_secret_value()
        return str(settings.DEFAULT_LLM_MODEL)

    async def extract_media_items(
        self,
        transcript: str,
        description: Optional[str] = None,
        images: Optional[List[str]] = None
    ) -> List[ExtractedMediaItem]:
        """Analyzes video/post transcript, description, and optional images to extract all movies and TV shows.

        Args:
            transcript: Transcribed speech from the video (empty string for image/text-only posts).
            description: Optional video description or post caption.
            images: Optional base64 data-URI images (e.g. post photos) used as additional visual evidence.

        Returns:
            List of ExtractedMediaItem objects.
        """
        words = transcript.split()
        if len(words) > settings.MAX_TRANSCRIPT_TOKENS:
            logger.warning("Transcript exceeds token guardrail limit. Truncating transcript.")
            transcript = " ".join(words[:settings.MAX_TRANSCRIPT_TOKENS])

        system_instruction = (
            "You are an expert film and television analyst. Analyze the provided video transcript "
            "and video description to extract all movies, films, TV shows, anime, or series mentioned, "
            "reviewed, recommended, or described in the video content.\n"
            "The media title might be wrong or poorly transcribed so use your extensive knowledge to provide the corrected title name.\n"
            "If images are attached (e.g. movie posters, promotional images, or screenshots), use them as additional visual evidence.\n\n"
            "Respond ONLY with a valid JSON object matching this schema:\n"
            "{\n"
            '  "media_items": [\n'
            '    {\n'
            '      "title": "Exact Title of Movie or Show",\n'
            '      "media_type": "movie",\n'
            '    }\n'
            '  ]\n'
            "}\n\n"
            "If no movies or TV shows are mentioned, return {\"media_items\": []}.\n"
            "Do NOT include markdown wrapping or extra commentary outside the JSON."
        )

        user_content_parts = []
        if description and description.strip():
            user_content_parts.append(f"Description:\n{description.strip()}")
        if transcript.strip():
            user_content_parts.append(f"Transcript:\n{transcript.strip()}")
        user_text = "\n\n".join(user_content_parts)

        try:
            return await self._run_media_extraction(
                system_instruction, self._build_user_content(user_text, images)
            )
        except Exception as e:
            if images:
                logger.warning(f"Multimodal LLM extraction failed ({e}); retrying text-only.")
                try:
                    return await self._run_media_extraction(system_instruction, user_text)
                except Exception as retry_err:
                    logger.error(f"LiteLLM media extraction failed on text-only retry: {retry_err}")
                    return []
            logger.error(f"LiteLLM media extraction failed: {e}")
            return []

    @staticmethod
    def _build_user_content(text: str, images: Optional[List[str]]) -> Union[str, List[dict]]:
        """Builds a plain string, or an OpenAI-style multimodal content list when `images` is provided."""
        if not images:
            return text
        content: List[dict] = [{"type": "text", "text": text}]
        content.extend({"type": "image_url", "image_url": {"url": img}} for img in images)
        return content

    async def _run_media_extraction(self, system_instruction: str, user_content: Any) -> List[ExtractedMediaItem]:
        """Sends the chat completion request and parses the JSON media-items response."""
        response = await self.client.chat.completions.create(
            model=self._get_model_name(),
            messages=[
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": user_content}
            ],
            temperature=0.3
        )
        content = response.choices[0].message.content or ""
        clean_content = content.strip()

        if clean_content.startswith("```"):
            clean_content = re.sub(r"^```(?:json)?\n?", "", clean_content)
            clean_content = re.sub(r"\n?```$", "", clean_content).strip()

        try:
            data = json.loads(clean_content)
            result = VideoMediaExtractionResult.model_validate(data)
            return result.media_items
        except Exception as parse_err:
            logger.warning(f"Direct JSON parsing failed: {parse_err}. Attempting regex extraction.")
            json_match = re.search(r"\{.*\}", clean_content, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group(0))
                result = VideoMediaExtractionResult.model_validate(data)
                return result.media_items
            logger.error(f"Failed to parse LLM output as media items: {content}")
            return []

    async def analyze_transcript(
        self,
        transcript: str,
        user_prompt: str = "Summarize the key points of this video."
    ) -> str:
        """Truncates transcript to guardrail token limit and sends it to LiteLLM."""
        words = transcript.split()
        if len(words) > settings.MAX_TRANSCRIPT_TOKENS:
            logger.warning("Transcript exceeds token guardrail limit. Truncating transcript.")
            transcript = " ".join(words[:settings.MAX_TRANSCRIPT_TOKENS])

        system_instruction = (
            "You are a helpful AI assistant. Analyze the provided video transcript "
            "and answer the user's request accurately and concisely."
        )

        try:
            response = await self.client.chat.completions.create(
                model=self._get_model_name(),
                messages=[
                    {"role": "system", "content": system_instruction},
                    {"role": "user", "content": f"Transcript:\n{transcript}\n\nUser Question: {user_prompt}"}
                ],
                temperature=0.3
            )
            return response.choices[0].message.content or "No output returned from AI model."
        except Exception as e:
            logger.error(f"LiteLLM invocation failed: {e}")
            raise RuntimeError("Failed to communicate with local AI service.") from e
