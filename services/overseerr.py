"""Overseerr and Seerr API integration client service.

Provides a fault-tolerant async client for communicating with Overseerr / Seerr API endpoints,
handling search, media details, ratings, requests, approvals, and error mapping.
"""

import logging
import urllib.parse
from typing import Optional, List, Dict, Any
import httpx
from tenacity import AsyncRetrying, stop_after_attempt, wait_exponential, retry_if_exception

from config import settings

logger = logging.getLogger(__name__)


class OverseerrAPIError(Exception):
    """Raised when Overseerr returns an unexpected HTTP API error response."""
    pass


class OverseerrConnectionError(Exception):
    """Raised when connection to the Overseerr server fails or times out."""
    pass


STATUS_MAP = {
    1: "🆕 Not Requested",
    2: "⏳ Pending Approval",
    3: "⚙️ Processing / Downloading",
    4: "📂 Partially Available",
    5: "✅ Available"
}


def _is_retryable_exception(exception: Exception) -> bool:
    """Checks if an HTTP or connection error is eligible for retry attempt."""
    if isinstance(exception, httpx.HTTPStatusError):
        return exception.response.status_code >= 500
    return isinstance(exception, (httpx.ConnectError, httpx.TimeoutException, httpx.RequestError))


class OverseerrClient:
    """Async API client interface for Overseerr / Seerr media request management.

    Attributes:
        base_url: Base URL string for Overseerr API v1.
        headers: Default HTTP headers containing authentication API key.
        ssl_verify: Boolean indicating whether SSL certificates are verified.
        timeout: Request timeout duration in seconds.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        ssl_verify: Optional[bool] = None,
        timeout: Optional[float] = None,
    ) -> None:
        """Initializes the Overseerr client with API credentials and endpoints."""
        url_str = str(base_url) if base_url else str(settings.OVERSEERR_URL)
        self.base_url = url_str.rstrip("/")
        if not self.base_url.endswith("/api/v1"):
            self.base_url = f"{self.base_url}/api/v1"

        key = api_key if api_key else settings.OVERSEERR_API_KEY.get_secret_value()
        self.headers = {
            "X-Api-Key": key,
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        self.ssl_verify = ssl_verify if ssl_verify is not None else settings.OVERSEERR_SSL_VERIFY
        self.timeout = timeout if timeout is not None else settings.OVERSEERR_TIMEOUT
        self._client: Optional[httpx.AsyncClient] = None

    def _get_client(self) -> httpx.AsyncClient:
        """Returns active httpx.AsyncClient connection pool or initializes a new instance."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                headers=self.headers,
                verify=self.ssl_verify,
                timeout=self.timeout
            )
        return self._client

    async def aclose(self) -> None:
        """Closes the underlying HTTP client connection pool gracefully."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def _get(self, path: str, params: Optional[dict] = None) -> Optional[Dict[str, Any]]:
        """Performs an async GET request to Overseerr API."""
        url = f"{self.base_url}{path}"
        if params:
            query_string = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
            url = f"{url}?{query_string}"
        try:
            client = self._get_client()
            response = await client.get(url)
            response.raise_for_status()
            return response.json()
        except httpx.RequestError as e:
            logger.error(f"Overseerr GET connection failed for {path}: {e}")
            raise OverseerrConnectionError(f"Connection failed: {e}") from e
        except httpx.HTTPStatusError as e:
            logger.error(f"Overseerr GET HTTP error for {path}: {e.response.status_code}")
            raise OverseerrAPIError(f"HTTP error {e.response.status_code}") from e
        except Exception as e:
            logger.error(f"Overseerr GET request to {path} failed: {e}")
            return None

    async def _post(self, path: str, json_data: dict) -> Optional[Dict[str, Any]]:
        """Performs an async POST request to Overseerr API with automatic retries."""
        url = f"{self.base_url}{path}"
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(3),
                wait=wait_exponential(multiplier=1, min=1, max=5),
                retry=retry_if_exception(_is_retryable_exception),
                reraise=True,
                before_sleep=lambda retry_state: logger.warning(
                    f"Overseerr POST to {path} failed (attempt {retry_state.attempt_number}). Retrying..."
                )
            ):
                with attempt:
                    client = self._get_client()
                    response = await client.post(url, json=json_data)
                    response.raise_for_status()
                    return response.json()
        except httpx.RequestError as e:
            logger.error(f"Overseerr POST connection failed for {path}: {e}")
            raise OverseerrConnectionError(f"Connection failed: {e}") from e
        except httpx.HTTPStatusError as e:
            logger.error(f"Overseerr POST HTTP error for {path}: {e.response.status_code}")
            raise OverseerrAPIError(f"HTTP error {e.response.status_code}") from e
        except Exception as e:
            logger.error(f"Overseerr POST request to {path} failed: {e}")
            return None

    async def _delete(self, path: str) -> bool:
        """Performs an async DELETE request to Overseerr API."""
        url = f"{self.base_url}{path}"
        try:
            client = self._get_client()
            response = await client.delete(url)
            response.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Overseerr DELETE request to {path} failed: {e}")
            return False

    async def search(self, query: str) -> List[Dict[str, Any]]:
        """Searches Overseerr for movies or TV shows matching query."""
        try:
            data = await self._get("/search", params={"query": query})
            if data and "results" in data:
                return data["results"]
        except Exception as e:
            logger.error(f"Search failed for query '{query}': {e}")
        return []

    async def get_movie_details(self, tmdb_id: int) -> Optional[Dict[str, Any]]:
        """Retrieves detailed metadata for a movie by TMDb ID."""
        return await self._get(f"/movie/{tmdb_id}")

    async def get_tv_details(self, tmdb_id: int) -> Optional[Dict[str, Any]]:
        """Retrieves detailed metadata for a TV show by TMDb ID."""
        return await self._get(f"/tv/{tmdb_id}")

    def get_media_status_str(self, media_info: Optional[Dict[str, Any]]) -> str:
        """Translates mediaInfo status code into a formatted display string."""
        if not media_info:
            return STATUS_MAP[1]
        status_num = media_info.get("status", 1)
        return STATUS_MAP.get(status_num, STATUS_MAP[1])

    async def request_media(self, media_type: str, tmdb_id: int, seasons: Optional[List[int]] = None) -> Optional[Dict[str, Any]]:
        """Submits a download request to Overseerr for a movie or TV show."""
        payload: Dict[str, Any] = {
            "mediaType": media_type,
            "mediaId": tmdb_id
        }
        if media_type == "tv":
            if not seasons:
                tv_details = await self.get_tv_details(tmdb_id)
                if tv_details and "seasons" in tv_details:
                    seasons = [
                        s["seasonNumber"]
                        for s in tv_details["seasons"]
                        if s.get("seasonNumber") is not None and s["seasonNumber"] > 0
                    ]
                    if not seasons:
                        seasons = [1]
                else:
                    seasons = [1]
            payload["seasons"] = seasons
        logger.info(f"Submitting Overseerr request: {payload}")
        return await self._post("/request", payload)

    async def get_requests(self, take: int = 10, skip: int = 0, filter_status: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Fetches a list of media requests from Overseerr."""
        params: Dict[str, Any] = {"take": take, "skip": skip}
        if filter_status:
            params["filter"] = filter_status
        return await self._get("/request", params=params)

    async def get_request(self, request_id: int) -> Optional[Dict[str, Any]]:
        """Gets details of a specific media request by request ID."""
        return await self._get(f"/request/{request_id}")

    async def approve_request(self, request_id: int) -> Optional[Dict[str, Any]]:
        """Approves a pending media request."""
        return await self._post(f"/request/{request_id}/approve", {})

    async def decline_request(self, request_id: int) -> Optional[Dict[str, Any]]:
        """Declines a pending media request."""
        return await self._post(f"/request/{request_id}/decline", {})

    async def retry_request(self, request_id: int) -> Optional[Dict[str, Any]]:
        """Retries a failed media request."""
        return await self._post(f"/request/{request_id}/retry", {})

    async def delete_request(self, request_id: int) -> bool:
        """Deletes a media request from Overseerr."""
        return await self._delete(f"/request/{request_id}")

    async def get_ratings(self, media_type: str, tmdb_id: int) -> Dict[str, Optional[float]]:
        """Fetches TMDb, Rotten Tomatoes, and IMDb ratings for a title."""
        ratings: Dict[str, Optional[float]] = {"tmdb": None, "rt_critics": None, "rt_audience": None, "imdb": None}
        try:
            if media_type == "movie":
                data = await self._get(f"/movie/{tmdb_id}/ratingscombined")
                if data:
                    rt = data.get("rt", {})
                    if rt:
                        ratings["rt_critics"] = rt.get("criticsScore")
                        ratings["rt_audience"] = rt.get("audienceScore")
                    imdb = data.get("imdb", {})
                    if imdb:
                        ratings["imdb"] = imdb.get("criticsScore")
            else:
                data = await self._get(f"/tv/{tmdb_id}/ratings")
                if data:
                    ratings["rt_critics"] = data.get("criticsScore")
                    ratings["rt_audience"] = data.get("audienceScore")
        except Exception as e:
            logger.warning(f"Failed to fetch ratings for {media_type} {tmdb_id}: {e}")
        return ratings
