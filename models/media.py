"""Domain Data Transfer Objects (DTOs) and Pydantic schemas."""

from typing import Optional, List
from pydantic import BaseModel, Field, ConfigDict


class ExtractedMediaInfo(BaseModel):
    """Metadata extracted from a parsed URL (TMDB, IMDb, Letterboxd, etc.)."""
    title: Optional[str] = None
    year: Optional[int] = None
    media_type: Optional[str] = None  # "movie" or "tv"
    source: str
    tmdb_id: Optional[int] = None
    thumbnail_url: Optional[str] = None


class MediaSearchResult(BaseModel):
    """Overseerr / TMDb media search item representation."""
    id: int
    media_type: str = Field(alias="mediaType", default="movie")
    title: Optional[str] = None
    name: Optional[str] = None
    release_date: Optional[str] = Field(alias="releaseDate", default=None)
    first_air_date: Optional[str] = Field(alias="firstAirDate", default=None)
    poster_path: Optional[str] = Field(alias="posterPath", default=None)
    backdrop_path: Optional[str] = Field(alias="backdropPath", default=None)
    overview: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True)


class MediaRequestPayload(BaseModel):
    """Payload format for submitting a media request to Overseerr."""
    media_type: str = Field(alias="mediaType")
    media_id: int = Field(alias="mediaId")
    seasons: Optional[List[int]] = None

    model_config = ConfigDict(populate_by_name=True)


class ExtractedMediaItem(BaseModel):
    """Single movie or TV show title extracted from a video transcript."""
    title: str = Field(description="Title of the movie or TV show")
    year: Optional[int] = Field(default=None, description="Release year if mentioned")
    media_type: Optional[str] = Field(default=None, description="'movie' or 'tv' if determinable")
    context: Optional[str] = Field(default=None, description="Brief note on context in video")


class VideoMediaExtractionResult(BaseModel):
    """Container schema for media items extracted from a video transcript."""
    media_items: List[ExtractedMediaItem] = Field(default_factory=list)


class VideoContentData(BaseModel):
    """Container DTO for extracted transcript, description, and metadata from a video link."""
    transcript: str
    description: Optional[str] = None
    title: Optional[str] = None


class SocialPostContent(BaseModel):
    """Container DTO for content scraped from an Instagram/Facebook post carrying no video stream."""
    description: Optional[str] = None
    title: Optional[str] = None
    images: List[str] = Field(default_factory=list)

    @property
    def has_content(self) -> bool:
        """True when the post yielded caption text or an image usable for identification."""
        return bool((self.description or "").strip() or self.images)


class TranscriptAnalysisRequest(BaseModel):
    """Request DTO for transcript analysis."""
    url: str
    user_prompt: Optional[str] = "Summarize the key points of this video."


class TranscriptAnalysisResult(BaseModel):
    """Result DTO for transcript analysis."""
    url: str
    transcript: str
    summary: str
