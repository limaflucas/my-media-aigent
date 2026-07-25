"""HTML Scraper and URL Metadata Parser Service.

Extracts titles, release years, and media types from various streaming services,
movie databases, and web pages (IMDb, Letterboxd, TMDB, MyAnimeList, AniList, Netflix, etc.).
"""

import re
import json
import logging
import urllib.parse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Standard headers to bypass basic scraper detection
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
}


def clean_title(title: str) -> str:
    """Removes common site suffixes and trailing garbage from scraped titles."""
    if not title:
        return ""
    # Remove common site suffixes
    suffixes = [
        r"\s*-\s*IMDb",
        r"\s*\|\s*Letterboxd",
        r"\s*-\s*MyAnimeList\.net",
        r"\s*-\s*AniList",
        r"\s*-\s*Netflix",
        r"\s*\|\s*Netflix",
        r"\s*-\s*Trakt",
        r"\s*-\s*Crunchyroll",
        r"\s*-\s*YouTube",
        r"\s*\|\s*YouTube",
        r"\s*-\s*Instagram",
        r"\s*\|\s*Instagram",
        r"\s*-\s*Facebook",
        r"\s*\|\s*Facebook",
        # Remove common suffixes from social media titles
        r"\s*\|\s*TikTok",
        r"\s*-\s*TikTok",
        r"\s*\|\s*Twitter",
        r"\s*-\s*Twitter",
        r"\s*\|\s*X",
    ]
    cleaned = title
    for suffix in suffixes:
        cleaned = re.sub(suffix, "", cleaned, flags=re.IGNORECASE)
    # Clean up any trailing/leading whitespaces
    cleaned = cleaned.strip()
    # Remove common trailing patterns like "| Official Trailer", "- Official Trailer"
    cleaned = re.sub(r"\s*[\|\-]\s*(Official\s+)?(Trailer|Teaser|Clip|Featurette|Behind\s+the\s+Scenes|BTS).*$", "", cleaned, flags=re.IGNORECASE)
    return cleaned


def parse_year_from_title(title: str) -> tuple[str, int | None]:
    """Extracts a 4-digit year from parentheses at the end of the title if present."""
    # Matches "Title (2022)" or "Title (2022-2024)"
    year_match = re.search(r"\s*\((\d{4})(?:-\d{4})?\)$", title)
    if year_match:
        year = int(year_match.group(1))
        # Strip the year part from the title
        clean_name = re.sub(r"\s*\((\d{4})(?:-\d{4})?\)$", "", title).strip()
        return clean_name, year
    return title, None


def _extract_youtube_id(url: str) -> str | None:
    """Extracts the 11-character video ID from various YouTube URL formats."""
    patterns = [
        r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/|youtube\.com/embed/|youtube\.com/v/)([\w-]{11})",
    ]
    for p in patterns:
        m = re.search(p, url)
        if m:
            return m.group(1)
    return None


def _fetch_youtube_oembed(url: str) -> dict | None:
    """
    Fetches video metadata via YouTube's oEmbed endpoint (no API key required).
    Works for regular videos and Shorts.
    Returns: {"title": str, "thumbnail_url": str} or None
    """
    try:
        response = requests.get(
            "https://www.youtube.com/oembed",
            params={"url": url, "format": "json"},
            headers=HEADERS,
            timeout=10
        )
        response.raise_for_status()
        data = response.json()
        title = data.get("title")
        if title:
            logger.info(f"Extracted from YouTube oEmbed: Title='{title}'")
            return {
                "title": clean_title(title),
                "year": None,
                "media_type": None,
                "source": "youtube_oembed",
                "thumbnail_url": data.get("thumbnail_url"),
            }
    except Exception as e:
        logger.warning(f"YouTube oEmbed failed for {url}: {e}")
    return None


def _fetch_og_tags(url: str) -> dict | None:
    """
    Fetches OpenGraph meta tags from a web page.
    Used as a fallback for Instagram, Facebook, and generic web pages.
    """
    try:
        response = requests.get(url, headers=HEADERS, timeout=10, allow_redirects=True)
        response.raise_for_status()
    except Exception as e:
        logger.warning(f"Failed to fetch URL for OG tags {url}: {e}")
        return None

    soup = BeautifulSoup(response.text, "html.parser")

    # Try JSON-LD first (IMDb, Letterboxd, etc. structure their metadata here)
    json_ld_tags = soup.find_all("script", type="application/ld+json")
    for tag in json_ld_tags:
        if not tag.string:
            continue
        try:
            data = json.loads(tag.string)
            items = data if isinstance(data, list) else [data]
            for item in items:
                context = item.get("@context", "")
                if "schema.org" not in context:
                    continue
                item_type = item.get("@type", "")
                if item_type in ["Movie", "TVSeries", "TVEpisode"]:
                    name = item.get("name")
                    if name:
                        name, parsed_year = parse_year_from_title(name)
                        year = parsed_year
                        if not year:
                            release_date = item.get("releaseDate") or item.get("dateCreated")
                            if release_date and len(release_date) >= 4:
                                year_match = re.search(r"\d{4}", release_date)
                                if year_match:
                                    year = int(year_match.group(0))
                        media_type = "movie" if item_type == "Movie" else "tv"
                        logger.info(f"Extracted from JSON-LD: Name='{name}', Year={year}, Type={media_type}")
                        return {
                            "title": name,
                            "year": year,
                            "media_type": media_type,
                            "source": "json_ld"
                        }
        except Exception as e:
            logger.warning(f"Error parsing JSON-LD: {e}")
            continue

    # Fallback to OpenGraph / Meta / Title tags
    og_title_tag = soup.find("meta", property="og:title") or soup.find("meta", attrs={"name": "twitter:title"})
    og_type_tag = soup.find("meta", property="og:type")
    og_image_tag = soup.find("meta", property="og:image") or soup.find("meta", attrs={"name": "twitter:image"})

    title = None
    if og_title_tag and og_title_tag.get("content"):
        title = clean_title(og_title_tag.get("content"))
    else:
        title_tag = soup.find("title")
        if title_tag:
            title = clean_title(title_tag.text)

    if not title:
        logger.warning(f"Could not extract any title from URL: {url}")
        return None

    # Parse title and extract year if contained in parentheses at the end
    title, year = parse_year_from_title(title)

    # Guess media type
    media_type = "movie"  # Default fallback
    if og_type_tag and og_type_tag.get("content"):
        og_type = og_type_tag.get("content").lower()
        if any(t in og_type for t in ["video.tv_show", "video.episode", "tv_show", "tv"]):
            media_type = "tv"

    # Refine guess based on domain/URL patterns
    url_lower = url.lower()
    if any(k in url_lower for k in ["/tv/", "/show/", "/series/", "myanimelist.net/anime", "anilist.co/anime"]):
        media_type = "tv"
    elif any(k in url_lower for k in ["/movie/", "/film/"]):
        media_type = "movie"

    logger.info(f"Fallback extracted: Title='{title}', Year={year}, Type={media_type}")
    return {
        "title": title,
        "year": year,
        "media_type": media_type,
        "source": "meta_tags",
        "thumbnail_url": og_image_tag.get("content") if og_image_tag else None,
    }


def extract_media_info_from_url(url: str) -> dict | None:
    """
    Scrapes the URL to extract the media title, year, and type.
    Handles TMDB direct links, IMDb JSON-LD/meta, Letterboxd, MAL, AniList,
    YouTube videos/shorts, Instagram posts/reels, Facebook videos/posts,
    and generic web pages with OG tags or JSON-LD schema.
    """
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower()

    # ──────────────────────────────────────────────────────────────
    # 1. Direct TMDB link — extract ID and type directly
    # ──────────────────────────────────────────────────────────────
    # e.g., https://www.themoviedb.org/movie/278-the-shawshank-redemption
    # e.g., https://www.themoviedb.org/tv/1399-game-of-thrones
    tmdb_match = re.search(r"themoviedb\.org/(movie|tv)/(\d+)", url)
    if tmdb_match:
        media_type = tmdb_match.group(1)
        tmdb_id = int(tmdb_match.group(2))
        logger.info(f"Detected direct TMDB URL. Type: {media_type}, ID: {tmdb_id}")
        return {
            "tmdb_id": tmdb_id,
            "media_type": media_type,
            "source": "tmdb_url"
        }

    # ──────────────────────────────────────────────────────────────
    # 2. IMDb URL — use the suggestion API to bypass WAF challenges
    # ──────────────────────────────────────────────────────────────
    imdb_match = re.search(r"imdb\.com/title/(tt\d+)", url)
    if imdb_match:
        imdb_id = imdb_match.group(1)
        logger.info(f"Detected IMDb URL. ID: {imdb_id}")
        try:
            suggest_url = f"https://v3.sg.media-imdb.com/suggestion/x/{imdb_id}.json"
            response = requests.get(suggest_url, headers=HEADERS, timeout=10)
            response.raise_for_status()
            data = response.json()
            if data and "d" in data:
                for item in data["d"]:
                    if item.get("id") == imdb_id:
                        title = item.get("l")
                        year = item.get("y")
                        qid = item.get("qid", "")
                        media_type = "tv" if "tv" in qid.lower() else "movie"
                        logger.info(f"Extracted from IMDb Suggest: Title='{title}', Year={year}, Type={media_type}")
                        return {
                            "title": title,
                            "year": year,
                            "media_type": media_type,
                            "source": "imdb_suggest"
                        }
        except Exception as e:
            logger.warning(f"Error querying IMDb suggest API: {e}")

    # ──────────────────────────────────────────────────────────────
    # 3. YouTube — videos and Shorts via oEmbed (no API key needed)
    # ──────────────────────────────────────────────────────────────
    if "youtube.com" in host or "youtu.be" in host:
        video_id = _extract_youtube_id(url)
        if video_id:
            # Normalize URL for oEmbed
            normalized_url = f"https://www.youtube.com/watch?v={video_id}"
            result = _fetch_youtube_oembed(normalized_url)
            if result:
                return result
        # Fallback to OG tags if oEmbed fails
        logger.info("YouTube oEmbed failed, falling back to OG tags")
        return _fetch_og_tags(url)

    # ──────────────────────────────────────────────────────────────
    # 4. Instagram — posts, reels, videos (best-effort OG tag scraping)
    # ──────────────────────────────────────────────────────────────
    if "instagram.com" in host:
        logger.info(f"Detected Instagram URL: {url}")
        result = _fetch_og_tags(url)
        if result:
            result["source"] = "social_og"
            return result
        return None

    # ──────────────────────────────────────────────────────────────
    # 5. Facebook — videos and posts (best-effort OG tag scraping)
    # ──────────────────────────────────────────────────────────────
    if "facebook.com" in host or "fb.watch" in host:
        logger.info(f"Detected Facebook URL: {url}")
        result = _fetch_og_tags(url)
        if result:
            result["source"] = "social_og"
            return result
        return None

    # ──────────────────────────────────────────────────────────────
    # 6. TikTok — videos (best-effort OG tag scraping)
    # ──────────────────────────────────────────────────────────────
    if "tiktok.com" in host:
        logger.info(f"Detected TikTok URL: {url}")
        result = _fetch_og_tags(url)
        if result:
            result["source"] = "social_og"
            return result
        return None

    # ──────────────────────────────────────────────────────────────
    # 7. Twitter / X — posts with video (best-effort OG tag scraping)
    # ──────────────────────────────────────────────────────────────
    if "twitter.com" in host or "x.com" in host:
        logger.info(f"Detected Twitter/X URL: {url}")
        result = _fetch_og_tags(url)
        if result:
            result["source"] = "social_og"
            return result
        return None

    # ──────────────────────────────────────────────────────────────
    # 8. Generic web page — try JSON-LD, then OG tags, then <title>
    # ──────────────────────────────────────────────────────────────
    logger.info(f"Processing generic URL: {url}")
    return _fetch_og_tags(url)


if __name__ == "__main__":
    # Quick test
    logging.basicConfig(level=logging.INFO)
    test_urls = [
        "https://www.imdb.com/title/tt0111161/",
        "https://letterboxd.com/film/the-batman/",
        "https://www.themoviedb.org/movie/278-the-shawshank-redemption",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://www.youtube.com/shorts/aqz-KE-bpKQ",
        "https://www.instagram.com/reel/CXYZ123/",
        "https://www.facebook.com/watch?v=123456789",
    ]
    for url in test_urls:
        print(f"Testing URL: {url}")
        print(extract_media_info_from_url(url))
        print("-" * 50)