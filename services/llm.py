import json
import re
import logging
from typing import List, Optional
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
        description: Optional[str] = None
    ) -> List[ExtractedMediaItem]:
        """Analyzes video transcript and description to extract all movies and TV shows.

        Args:
            transcript: Transcribed speech from the video.
            description: Optional video description or caption.

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
            "The media title might be wrong or poorly transcribed so use your extensive knowledge to provide the corrected title name.\n\n"
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
            user_content_parts.append(f"Video Description:\n{description.strip()}")
        user_content_parts.append(f"Video Transcript:\n{transcript.strip()}")
        user_content = "\n\n".join(user_content_parts)

        try:
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
        except Exception as e:
            logger.error(f"LiteLLM media extraction failed: {e}")
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
