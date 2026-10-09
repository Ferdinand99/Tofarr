import json
import httpx, respx, pytest
from pathlib import Path
from fastapi.testclient import TestClient
from app.config import Settings
from app.db import Db
from app.sources import get_source
from app.sources.base import SourceError
from app.web.main import create_app

GQL = "https://api.graphql.imdb.com/"


def st(tmdb="K"):
    return Settings("http://x", "k", tmdb, None, Path("."))


def graphql(rows, **extra):
    edges = [{"node": {"item": {"id": i, "titleText": {"text": n}, "titleType": {"id": t}}}} for i, t, n in rows]
    return httpx.Response(200, json={"data": {"list": {"name": {"originalText": "L"}, "items": {
        "total": len(edges), "pageInfo": {"hasNextPage": False, "endCursor": None}, "edges": edges}}}, **extra})


@respx.mock
def test_imdb_list_keeps_order_dedupes_and_maps_types_through_tmdb_find():
    respx.post(GQL).mock(return_value=graphql([
        ("tt9000001", "tvMiniSeries", "Loki"), ("tt9000002", "movie", "Iron Man"),
        ("tt9000001", "tvMiniSeries", "Loki"), ("tt9000003", "video", "One-Shot")]))
    found = {
        "tt9000001": {"movie_results": [], "tv_results": [{"id": 84958, "name": "Loki"}]},
        "tt9000002": {"movie_results": [{"id": 1726, "title": "Iron Man"}], "tv_results": []},
        "tt9000003": {"movie_results": [{"id": 76122, "title": "The Consultant"}], "tv_results": []}}
    route = respx.get(url__regex=r"https://api\.themoviedb\.org/3/find/(tt\d+)").mock(
        side_effect=lambda r: httpx.Response(200, json=found[r.url.path.rsplit("/", 1)[1]]))
    got = get_source("imdb_list", {"list": "https://www.imdb.com/list/ls029032797/"}, st()).fetch()
    assert [(i.tmdb_id, i.media_type) for i in got] == [(84958, "tv"), (1726, "movie"), (76122, "movie")]
    assert route.call_count == 3                      # one lookup per unique title
    assert [i.title for i in got] == ["Loki", "Iron Man", "One-Shot"]


@respx.mock
def test_titles_tmdb_does_not_know_are_skipped():
    respx.post(GQL).mock(return_value=graphql([("tt9100001", "movie", "Obscure"), ("tt9100002", "movie", "Known")]))
    respx.get(url__regex=r".*/find/tt9100001").mock(return_value=httpx.Response(200, json={"movie_results": [], "tv_results": []}))
    respx.get(url__regex=r".*/find/tt9100002").mock(return_value=httpx.Response(200, json={"movie_results": [{"id": 5, "title": "K"}], "tv_results": []}))
    assert [i.tmdb_id for i in get_source("imdb_list", {"list": "ls1"}, st()).fetch()] == [5]


def test_needs_tmdb_key_and_a_valid_list_reference():
    with pytest.raises(SourceError, match="TMDB_API_KEY"):
        get_source("imdb_list", {"list": "ls029032797"}, st(tmdb=None))
    with pytest.raises(SourceError, match="IMDb list"):
        get_source("imdb_list", {"list": "https://example.com/nope"}, st())


@respx.mock
def test_imdb_failure_gives_a_readable_error():
    respx.post(GQL).mock(return_value=httpx.Response(503))
    with pytest.raises(SourceError, match="IMDb"):
        get_source("imdb_list", {"list": "ls029032797"}, st()).fetch()
    respx.post(GQL).mock(return_value=httpx.Response(200, json={"data": {"list": None}}))
    with pytest.raises(SourceError, match="not found|private"):
        get_source("imdb_list", {"list": "ls029032797"}, st()).fetch()


def test_form_saves_an_imdb_definition(tmp_path):
    db = Db(tmp_path / "t.db")
    class T:
        def system_info(self): return {"version": "1"}
    c = TestClient(create_app(st(), db, lambda: T()))
    r = c.post("/new", data={"name": "I", "source_type": "imdb_list", "imdb_list": "https://www.imdb.com/list/ls029032797/",
                             "interval_minutes": "1440"}, follow_redirects=False)
    assert r.status_code == 303
    d = db.list_definitions()[0]
    assert d["source_type"] == "imdb_list" and d["source_config"] == {"list": "https://www.imdb.com/list/ls029032797/"}
    assert "ls029032797" in c.get(f"/{d['id']}/edit").text


@respx.mock
def test_imdb_error_text_is_shown_and_cursor_variable_is_an_id():
    route = respx.post(GQL).mock(return_value=httpx.Response(400, json={"errors": [{"message": "bad variable"}]}))
    with pytest.raises(SourceError, match="bad variable"):
        get_source("imdb_list", {"list": "ls029032797"}, st()).fetch()
    assert "$after: ID" in json.loads(route.calls[0].request.content)["query"]


@respx.mock
def test_title_falls_back_to_tmdb_when_imdb_gives_none():
    respx.post(GQL).mock(return_value=graphql([("tt9200001", "movie", "")]))
    respx.get(url__regex=r".*/find/tt9200001").mock(return_value=httpx.Response(
        200, json={"movie_results": [{"id": 7, "title": "From TMDB"}], "tv_results": []}))
    assert [i.title for i in get_source("imdb_list", {"list": "ls1"}, st()).fetch()] == ["From TMDB"]
