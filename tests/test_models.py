import pytest
from models.media import (
    ExtractedMediaInfo,
    MediaSearchResult,
    ExtractedMediaItem,
    VideoMediaExtractionResult,
    MediaRequestPayload,
)


def test_extracted_media_info():
    info = ExtractedMediaInfo(
        title="Inception",
        year=2010,
        media_type="movie",
        source="tmdb_url",
        tmdb_id=27205
    )
    assert info.title == "Inception"
    assert info.year == 2010
    assert info.media_type == "movie"
    assert info.tmdb_id == 27205


def test_media_search_result_alias():
    data = {
        "id": 27205,
        "mediaType": "movie",
        "title": "Inception",
        "releaseDate": "2010-07-16",
        "overview": "A thief who steals corporate secrets..."
    }
    result = MediaSearchResult.model_validate(data)
    assert result.id == 27205
    assert result.media_type == "movie"
    assert result.title == "Inception"
    assert result.release_date == "2010-07-16"


def test_video_media_extraction_result():
    json_data = """{
        "media_items": [
            {"title": "The Matrix", "year": 1999, "media_type": "movie", "context": "Sci-Fi classic"},
            {"title": "Stranger Things", "year": 2016, "media_type": "tv", "context": "Nostalgic series"}
        ]
    }"""
    res = VideoMediaExtractionResult.model_validate_json(json_data)
    assert len(res.media_items) == 2
    assert res.media_items[0].title == "The Matrix"
    assert res.media_items[0].year == 1999
    assert res.media_items[1].media_type == "tv"
