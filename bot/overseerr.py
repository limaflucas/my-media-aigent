import logging
import requests

logger = logging.getLogger(__name__)

STATUS_MAP = {
    1: "🆕 Not Requested",  # Unknown / Not Requested in library
    2: "⏳ Pending Approval",
    3: "⚙️ Processing / Downloading",
    4: "📂 Partially Available",
    5: "✅ Available"
}

class OverseerrClient:
    def __init__(self, base_url: str, api_key: str, ssl_verify: bool = True):
        # Ensure base_url doesn't end with a slash, then append /api/v1
        self.base_url = base_url.rstrip("/")
        if not self.base_url.endswith("/api/v1"):
            self.base_url = f"{self.base_url}/api/v1"
            
        self.headers = {
            "X-Api-Key": api_key,
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        self.ssl_verify = ssl_verify

    def _get(self, path: str, params: dict = None) -> dict | None:
        import urllib.parse
        url = f"{self.base_url}{path}"
        if params:
            # Enforce %20 encoding for spaces instead of + to satisfy strict servers/proxies
            query_string = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
            url = f"{url}?{query_string}"
        try:
            response = requests.get(url, headers=self.headers, timeout=10, verify=self.ssl_verify)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Overseerr GET request to {path} failed: {e}")
            return None

    def _post(self, path: str, json_data: dict) -> dict | None:
        url = f"{self.base_url}{path}"
        try:
            response = requests.post(url, headers=self.headers, json=json_data, timeout=10, verify=self.ssl_verify)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Overseerr POST request to {path} failed: {e}")
            # Try to log detailed error response if available
            try:
                logger.error(f"Response body: {response.text}")
            except Exception:
                pass
            return None

    def _delete(self, path: str) -> bool:
        url = f"{self.base_url}{path}"
        try:
            response = requests.delete(url, headers=self.headers, timeout=10, verify=self.ssl_verify)
            response.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Overseerr DELETE request to {path} failed: {e}")
            return False

    def search(self, query: str) -> list:
        """Searches Overseerr for movies/TV shows."""
        data = self._get("/search", params={"query": query})
        if data and "results" in data:
            return data["results"]
        return []

    def get_movie_details(self, tmdb_id: int) -> dict | None:
        """Gets detailed info for a movie."""
        return self._get(f"/movie/{tmdb_id}")

    def get_tv_details(self, tmdb_id: int) -> dict | None:
        """Gets detailed info for a TV show, including available seasons."""
        return self._get(f"/tv/{tmdb_id}")

    def get_media_status_str(self, media_info: dict | None) -> str:
        """Converts mediaInfo object from API to a human-readable status."""
        if not media_info:
            return STATUS_MAP[1]
        
        status_num = media_info.get("status", 1)
        return STATUS_MAP.get(status_num, STATUS_MAP[1])

    def request_media(self, media_type: str, tmdb_id: int, seasons: list[int] = None) -> dict | None:
        """
        Sends a request to Overseerr to download media.
        For TV shows, if seasons is not specified, it will request all available seasons.
        """
        payload = {
            "mediaType": media_type,
            "mediaId": tmdb_id
        }

        if media_type == "tv":
            if not seasons:
                # Fetch TV details to get all season numbers
                tv_details = self.get_tv_details(tmdb_id)
                if tv_details and "seasons" in tv_details:
                    # Filter out season 0 (specials) unless it's the only season
                    seasons = [
                        s["seasonNumber"]
                        for s in tv_details["seasons"]
                        if s.get("seasonNumber") is not None and s["seasonNumber"] > 0
                    ]
                    # If empty (only specials or no seasons returned), default to [1]
                    if not seasons:
                        seasons = [1]
                else:
                    seasons = [1]
            
            payload["seasons"] = seasons

        logger.info(f"Submitting request: {payload}")
        return self._post("/request", payload)

    def get_requests(self, take: int = 10, skip: int = 0, filter_status: str = None) -> dict | None:
        """Gets media requests from Overseerr/Seerr."""
        params = {"take": take, "skip": skip}
        if filter_status:
            params["filter"] = filter_status
        return self._get("/request", params=params)

    def get_request(self, request_id: int) -> dict | None:
        """Gets details of a specific request."""
        return self._get(f"/request/{request_id}")

    def approve_request(self, request_id: int) -> dict | None:
        """Approves a media request."""
        return self._post(f"/request/{request_id}/approve", {})

    def decline_request(self, request_id: int) -> dict | None:
        """Declines a media request."""
        return self._post(f"/request/{request_id}/decline", {})

    def retry_request(self, request_id: int) -> dict | None:
        """Retries a failed media request."""
        return self._post(f"/request/{request_id}/retry", {})

    def delete_request(self, request_id: int) -> bool:
        """Deletes a media request."""
        return self._delete(f"/request/{request_id}")
