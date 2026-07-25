import pytest
from bot.parser import clean_title, parse_year_from_title, _extract_youtube_id, extract_media_info_from_url


def test_clean_title():
    assert clean_title("Inception - IMDb") == "Inception"
    assert clean_title("The Batman | Letterboxd") == "The Batman"
    assert clean_title("Fullmetal Alchemist - MyAnimeList.net") == "Fullmetal Alchemist"
    assert clean_title("Stranger Things - Official Trailer") == "Stranger Things"


def test_parse_year_from_title():
    title, year = parse_year_from_title("The Shawshank Redemption (1994)")
    assert title == "The Shawshank Redemption"
    assert year == 1994

    title_no_year, year_none = parse_year_from_title("Inception")
    assert title_no_year == "Inception"
    assert year_none is None


def test_extract_youtube_id():
    url1 = "https://www.youtube.com/watch?v=wZti8QKBWPo"
    assert _extract_youtube_id(url1) == "wZti8QKBWPo"

    url2 = "https://youtu.be/wZti8QKBWPo"
    assert _extract_youtube_id(url2) == "wZti8QKBWPo"

    url3 = "https://www.youtube.com/shorts/wZti8QKBWPo"
    assert _extract_youtube_id(url3) == "wZti8QKBWPo"


def test_extract_media_info_direct_tmdb():
    url = "https://www.themoviedb.org/movie/278-the-shawshank-redemption"
    info = extract_media_info_from_url(url)
    assert info is not None
    assert info["tmdb_id"] == 278
    assert info["media_type"] == "movie"
    assert info["source"] == "tmdb_url"
