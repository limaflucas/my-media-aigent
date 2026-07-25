import json
import re
import logging
from typing import List
from openai import AsyncOpenAI
from config import settings
from models.media import ExtractedMediaItem, VideoMediaExtractionResult

logger = logging.getLogger(__name__)


class LLMService:
    def __init__(self):
        self.client = AsyncOpenAI(
            base_url=settings.LITELLM_BASE_URL,
            api_key=settings.LITELLM_API_KEY.get_secret_value()
        )

    async def extract_media_items(self, transcript: str) -> List[ExtractedMediaItem]:
        """
        Analyzes video transcript and extracts all movies and TV shows mentioned or described in the video.
        """
        words = transcript.split()
        if len(words) > settings.MAX_TRANSCRIPT_TOKENS:
            logger.warning("Transcript exceeds token guardrail limit. Truncating transcript.")
            transcript = " ".join(words[:settings.MAX_TRANSCRIPT_TOKENS])

        system_instruction = (
            "You are an expert film and television analyst. Analyze the provided video transcript "
            "and extract all movies, films, TV shows, anime, or series mentioned, reviewed, or described in the video.\n\n"
            "Respond ONLY with a valid JSON object matching this schema:\n"
            "{\n"
            '  "media_items": [\n'
            '    {\n'
            '      "title": "Exact Title of Movie or Show",\n'
            '      "year": 2022,\n'
            '      "media_type": "movie",\n'
            '      "context": "Brief note on why it is mentioned in video"\n'
            '    }\n'
            '  ]\n'
            "}\n\n"
            "If no movies or TV shows are mentioned, return {\"media_items\": []}.\n"
            "Do NOT include markdown wrapping or extra commentary outside the JSON."
        )

        try:
            response = await self.client.chat.completions.create(
                model=settings.DEFAULT_LLM_MODEL,
                messages=[
                    {"role": "system", "content": system_instruction},
                    {"role": "user", "content": f"Transcript:\n{transcript}"}
                ],
                temperature=0.15
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

    async def analyze_transcript(self, transcript: str, user_prompt: str = "Summarize the key points of this video.") -> str:
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
                model=settings.DEFAULT_LLM_MODEL,
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
