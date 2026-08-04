"""Social post content extraction service.

Scrapes OpenGraph metadata (caption, author, image) from Instagram and Facebook posts that carry
no downloadable video stream. Kept separate from MediaExtractorService, which owns the distinct
video/audio -> transcript pipeline; this service never downloads media or calls Whisper.
"""

import re
import base64
import logging
from typing import List, Optional, Tuple

import httpx
from bs4 import BeautifulSoup

from models.media import SocialPostContent

logger = logging.getLogger(__name__)

# Instagram serves OpenGraph metadata only to recognized link-preview crawlers; a browser
# User-Agent receives a JavaScript shell containing no meta tags at all. Facebook serves them
# to any agent. Accept-Language pins the engagement-count boilerplate below to English so that
# it strips predictably regardless of any ?locale= parameter on the URL.
SCRAPE_HEADERS = {
    "User-Agent": "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
    "Accept-Language": "en-US,en;q=0.9",
}

# Instagram og:description: '69K likes, 877 comments - someuser on July 30, 2026: "caption"'
IG_ENGAGEMENT_RE = re.compile(
    r'^[\d.,]+[KMB]?\s+\S+,\s*[\d.,]+[KMB]?\s+\S+\s+-\s+(?P<author>[^\s:]+)\s+on\s+[^:]+:\s*'
    r'"(?P<caption>.*)"\s*\.?\s*$',
    re.DOTALL,
)

# Instagram og:title uses a different wrapper: 'Display Name on Instagram: "caption"'
IG_TITLE_RE = re.compile(
    r'^(?P<author>.+?)\s+on\s+Instagram:\s*"(?P<caption>.*)"\s*\.?\s*$',
    re.DOTALL,
)

# Facebook prefixes engagement counts and suffixes the page name:
# '3.3K views · 98 reactions | caption | Page Name'
FB_ENGAGEMENT_RE = re.compile(r'^[\d.,]+[KMB]?\s+\S+\s+·\s+[\d.,]+[KMB]?\s+\S+\s*\|\s*')

MAX_IMAGES = 3
MAX_IMAGE_BYTES = 5 * 1024 * 1024
FETCH_TIMEOUT = 15.0


class SocialPostExtractorService:
    """Extracts caption text and images from Instagram/Facebook posts via OpenGraph scraping."""

    @staticmethod
    def is_social_post(url: str) -> bool:
        """Checks whether the URL is an Instagram or Facebook link this service can scrape."""
        return "instagram.com" in url or "facebook.com" in url

    async def extract_post(self, url: str) -> SocialPostContent:
        """Scrapes a post's caption, author, and image for LLM-based media identification.

        Args:
            url: The Instagram or Facebook post URL.

        Returns:
            SocialPostContent with a composed description and base64 data-URI images.

        Raises:
            RuntimeError: If neither caption text nor an image could be recovered.
        """
        html = await self._fetch_html(url)
        caption, author, title, image_url = self._parse_og_metadata(html)
        images = await self._download_images_as_data_uris([image_url] if image_url else [])

        content = SocialPostContent(
            description=self._compose_description(caption, author),
            title=title,
            images=images,
        )
        if not content.has_content:
            raise RuntimeError(f"No caption or image could be extracted from post: {url}")

        logger.info(
            f"Scraped post {url}: caption={len(content.description or '')} chars, "
            f"images={len(content.images)}"
        )
        return content

    @staticmethod
    async def _fetch_html(url: str) -> str:
        """Fetches a post page the way a link-preview crawler would."""
        async with httpx.AsyncClient(timeout=FETCH_TIMEOUT, follow_redirects=True) as client:
            response = await client.get(url, headers=SCRAPE_HEADERS)
            response.raise_for_status()
            return response.text

    def _parse_og_metadata(self, html: str) -> Tuple[str, Optional[str], Optional[str], Optional[str]]:
        """Extracts (caption, author, title, image_url) from a post page's OpenGraph tags."""
        soup = BeautifulSoup(html, "html.parser")

        def meta(prop: str) -> Optional[str]:
            tag = soup.find("meta", property=prop) or soup.find("meta", attrs={"name": prop})
            content = tag.get("content") if tag else None
            return content.strip() if content else None

        raw_title = meta("og:title")

        # Facebook's og:title carries the full caption while og:description is truncated;
        # Instagram is the reverse. Clean both and keep whichever yields more caption text.
        candidates = [self._clean_og_text(meta("og:description")), self._clean_og_text(raw_title)]
        caption, author = max(candidates, key=lambda candidate: len(candidate[0]))
        if not author:
            author = next((known for _, known in candidates if known), None)

        return caption, author, raw_title, meta("og:image")

    @staticmethod
    def _clean_og_text(text: Optional[str]) -> Tuple[str, Optional[str]]:
        """Strips engagement-count boilerplate from an OpenGraph value, returning (caption, author)."""
        text = (text or "").strip()
        if not text:
            return "", None

        for pattern in (IG_ENGAGEMENT_RE, IG_TITLE_RE):
            instagram = pattern.match(text)
            if instagram:
                return instagram.group("caption").strip(), instagram.group("author")

        stripped = FB_ENGAGEMENT_RE.sub("", text)
        if stripped != text:
            author = None
            if " | " in stripped:
                stripped, author = stripped.rsplit(" | ", 1)
                author = author.strip() or None
            return stripped.strip(), author

        return text, None

    @staticmethod
    def _compose_description(caption: str, author: Optional[str]) -> Optional[str]:
        """Combines caption, hashtags, and author into a single text block for the LLM."""
        parts = []
        if caption:
            parts.append(caption)
        hashtags = re.findall(r"#\w+", caption)
        if hashtags:
            parts.append(f"Hashtags: {' '.join(hashtags)}")
        if author:
            parts.append(f"Posted by: {author}")
        return "\n".join(parts) or None

    @staticmethod
    async def _download_images_as_data_uris(urls: List[str]) -> List[str]:
        """Downloads images and base64-encodes them as data URIs for a vision LLM call.

        Failures and oversized images are skipped individually rather than failing the extraction.
        """
        data_uris: List[str] = []
        if not urls:
            return data_uris

        async with httpx.AsyncClient(timeout=FETCH_TIMEOUT, follow_redirects=True) as client:
            for url in urls[:MAX_IMAGES]:
                try:
                    response = await client.get(url, headers=SCRAPE_HEADERS)
                    response.raise_for_status()
                    if len(response.content) > MAX_IMAGE_BYTES:
                        logger.warning(f"Skipping oversized post image ({len(response.content)} bytes)")
                        continue
                    content_type = response.headers.get("content-type", "image/jpeg").split(";")[0]
                    encoded = base64.b64encode(response.content).decode("ascii")
                    data_uris.append(f"data:{content_type};base64,{encoded}")
                except Exception as e:
                    logger.warning(f"Failed to download post image {url}: {e}")
        return data_uris
