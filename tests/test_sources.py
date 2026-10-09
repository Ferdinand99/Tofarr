import httpx, respx, pytest
from app.config import Settings
from app.sources import get_source
from app.sources.base import SourceError
from pathlib import Path

def st(tmdb="T", trakt="C"):
    return Settings("http://x", "k", tmdb, trakt, Path("."))

def test_manual_parses_ids_types_comments():
    s = get_source("manual", {"text": "1726 # Iron Man\n\n1399 tv\nbad\n"}, st())
    got = s.fetch()
    assert [(i.tmdb_id, i.media_type) for i in got] == [(1726, "movie"), (1399, "tv")]
    assert [i.title for i in got] == ["Iron Man", ""]

@respx.mock
def test_tmdb_collection_sorted_by_release_date():
    respx.get("https://api.themoviedb.org/3/collection/10").mock(return_value=httpx.Response(200, json={"parts": [
        {"id": 2, "title": "B", "release_date": "2012-01-01"},
        {"id": 1, "title": "A", "release_date": "2008-01-01"},
        {"id": 3, "title": "C", "release_date": ""}]}))
    got = get_source("tmdb_collection", {"id": 10}, st()).fetch()
    assert [i.tmdb_id for i in got] == [1, 2, 3]

@respx.mock
def test_tmdb_discover_paginates_up_to_max_pages():
    route = respx.get("https://api.themoviedb.org/3/discover/movie").mock(side_effect=lambda r: httpx.Response(
        200, json={"page": int(r.url.params["page"]), "total_pages": 5,
                   "results": [{"id": int(r.url.params["page"]) * 10, "title": "x"}]}))
    got = get_source("tmdb_discover", {"media_type": "movie", "params": {}, "max_pages": 2}, st()).fetch()
    assert [i.tmdb_id for i in got] == [10, 20] and route.call_count == 2

@respx.mock
def test_trakt_list_maps_movies_and_shows_in_rank_order():
    respx.get("https://api.trakt.tv/users/u/lists/s/items").mock(return_value=httpx.Response(200, json=[
        {"rank": 2, "type": "movie", "movie": {"title": "B", "ids": {"tmdb": 2}}},
        {"rank": 1, "type": "show", "show": {"title": "A", "ids": {"tmdb": 1}}},
        {"rank": 3, "type": "movie", "movie": {"title": "N", "ids": {"tmdb": None}}}]))
    got = get_source("trakt_list", {"user": "u", "slug": "s"}, st()).fetch()
    assert [(i.tmdb_id, i.media_type) for i in got] == [(1, "tv"), (2, "movie")]

def test_missing_key_rejected_at_construction():
    with pytest.raises(SourceError, match="TMDB_API_KEY"):
        get_source("tmdb_collection", {"id": 1}, st(tmdb=None))
    with pytest.raises(SourceError, match="TRAKT_CLIENT_ID"):
        get_source("trakt_list", {"user": "u", "slug": "s"}, st(trakt=None))
    with pytest.raises(SourceError, match="Unknown source"):
        get_source("nope", {}, st())
