import json
import httpx, respx, pytest
from pathlib import Path
from fastapi.testclient import TestClient
from app.config import Settings
from app.db import Db
from app.discover.service import load_shelf, cfg_for, clear_cache
from app.models import SourceItem
from app.sources import get_source
from app.sources.base import SourceError
from app.tofa.client import TofaClient
from app.web.main import create_app

TMDB = "https://api.themoviedb.org/3"
TRAKT = "https://api.trakt.tv"
GQL = "https://api.graphql.imdb.com/"


def st(tmdb="K", trakt="C"):
    return Settings("http://tofa:33333", "key", tmdb, trakt, Path("."))


# ---------- sources ----------

@respx.mock
def test_tmdb_chart_carries_title_year_poster_and_type():
    respx.get(f"{TMDB}/trending/tv/week").mock(return_value=httpx.Response(200, json={"page": 1, "total_pages": 1, "results": [
        {"id": 84958, "name": "Loki", "first_air_date": "2021-06-09", "poster_path": "/p.jpg"}]}))
    got = get_source("tmdb_chart", {"path": "trending/tv/week"}, st()).fetch()
    assert [(i.tmdb_id, i.media_type) for i in got] == [(84958, "tv")] and got[0].title == "Loki" and got[0].year == 2021
    assert got[0].poster == "https://image.tmdb.org/t/p/w342/p.jpg"


def test_tmdb_chart_rejects_unknown_paths():
    with pytest.raises(SourceError, match="chart"):
        get_source("tmdb_chart", {"path": "account/123/lists"}, st())


@respx.mock
def test_trakt_chart_understands_all_three_response_shapes():
    ids = lambda n: {"title": f"T{n}", "year": 2000 + n, "ids": {"tmdb": n}}
    respx.get(f"{TRAKT}/movies/trending").mock(return_value=httpx.Response(200, json=[{"watchers": 5, "movie": ids(1)}]))
    respx.get(f"{TRAKT}/movies/popular").mock(return_value=httpx.Response(200, json=[ids(2)]))
    respx.get(f"{TRAKT}/shows/anticipated").mock(return_value=httpx.Response(200, json=[{"list_count": 9, "show": ids(3)}, {"show": {"title": "NoTmdb", "ids": {}}}]))
    out = lambda p: [(i.tmdb_id, i.media_type, i.title) for i in get_source("trakt_chart", {"path": p}, st()).fetch()]
    assert out("movies/trending") == [(1, "movie", "T1")]
    assert out("movies/popular") == [(2, "movie", "T2")]
    assert out("shows/anticipated") == [(3, "tv", "T3")]


@respx.mock
def test_imdb_chart_maps_titles_with_posters():
    respx.post(GQL).mock(return_value=httpx.Response(200, json={"data": {"chartTitles": {"edges": [
        {"node": {"id": "tt9300001", "titleText": {"text": "Alpha"}, "releaseYear": {"year": 1994},
                  "titleType": {"id": "movie"}, "primaryImage": {"url": "https://img/a.jpg"}}}]}}}))
    respx.get(url__regex=r".*/find/tt9300001").mock(return_value=httpx.Response(200, json={"movie_results": [{"id": 278, "title": "Alpha"}], "tv_results": []}))
    got = get_source("imdb_chart", {"chart": "TOP_RATED_MOVIES"}, st()).fetch()
    assert (got[0].tmdb_id, got[0].media_type, got[0].title, got[0].year, got[0].poster) == (278, "movie", "Alpha", 1994, "https://img/a.jpg")


def test_imdb_chart_rejects_unknown_chart_names():
    with pytest.raises(SourceError, match="chart"):
        get_source("imdb_chart", {"chart": "DROP_TABLE"}, st())


@respx.mock
def test_tofa_shelf_source_returns_items_with_library_flag():
    respx.get("http://tofa:33333/api/v1/discovery/shelf/popular-movies").mock(return_value=httpx.Response(200, json={
        "key": "popular-movies", "items": [
            {"tmdb_id": 1, "type": "movie", "title": "A", "year": 2020, "poster_path": "https://x/p.jpg", "in_library": True},
            {"tmdb_id": 2, "type": "tv", "title": "B", "year": 2021, "poster_path": None, "in_library": False}]}))
    got = get_source("tofa_shelf", {"key": "popular-movies"}, st()).fetch()
    assert [(i.tmdb_id, i.media_type, i.in_library, i.poster) for i in got] == [(1, "movie", True, "https://x/p.jpg"), (2, "tv", False, "")]


# ---------- tofa client discovery ----------

@respx.mock
def test_client_lists_shelves_and_downloads_library_images():
    respx.get("http://t/api/v1/discovery/page").mock(return_value=httpx.Response(200, json={"heroes": [], "shelves": [
        {"key": "k", "title": "Shelf", "subtitle": "s", "kind": "now", "missing_count": 3, "items": [{}, {}, {}, {}]}]}))
    respx.get("http://t/api/v1/auth/image-token").mock(return_value=httpx.Response(200, json={"token": "TKN"}))
    img = respx.get("http://t/images/posters/a.jpg").mock(return_value=httpx.Response(200, content=b"JPEG", headers={"content-type": "image/jpeg"}))
    c = TofaClient("http://t", "k")
    assert c.discovery_shelves() == [{"key": "k", "title": "Shelf", "subtitle": "s", "kind": "now", "count": 4, "missing": 3}]
    assert c.fetch_image("images/posters/a.jpg") == (b"JPEG", "image/jpeg")
    assert img.calls[0].request.url.params["st"] == "TKN"


# ---------- service ----------

class FakeTofa:
    def __init__(self): self.calls = 0
    def resolve_tmdb(self, items):
        self.calls += 1
        return {k: "m" for k in items if k[0] == 1}


@respx.mock
def test_service_marks_library_status_and_caches():
    clear_cache()
    respx.get(f"{TMDB}/movie/popular").mock(return_value=httpx.Response(200, json={"page": 1, "total_pages": 1, "results": [
        {"id": 1, "title": "Have", "release_date": "2020-01-01"}, {"id": 2, "title": "Lack", "release_date": "2021-01-01"}]}))
    tofa = FakeTofa()
    v = load_shelf("tmdb_chart", "movie/popular", st(), tofa)
    assert [(i.title, i.in_library) for i in v.items] == [("Have", True), ("Lack", False)] and (v.have, v.total) == (1, 2)
    load_shelf("tmdb_chart", "movie/popular", st(), tofa)
    assert tofa.calls == 1


def test_cfg_for_maps_the_single_argument_to_each_sources_config():
    assert cfg_for("trakt_list", "someone/my-list") == {"user": "someone", "slug": "my-list"}
    assert cfg_for("tmdb_collection", "86311") == {"id": 86311}
    assert cfg_for("imdb_list", "ls1") == {"list": "ls1"}
    assert cfg_for("tofa_shelf", "k") == {"key": "k"}


# ---------- web ----------

class WebTofa(FakeTofa):
    def system_info(self): return {"version": "1"}
    def discovery_shelves(self):
        return [{"key": "popular-movies", "title": "Popular Movies", "subtitle": "Hot", "kind": "now", "count": 40, "missing": 10}]
    def fetch_image(self, path): return b"IMG", "image/jpeg"


def web(tmp_path):
    clear_cache()
    db = Db(tmp_path / "t.db")
    return TestClient(create_app(st(), db, lambda: WebTofa())), db


def test_discover_tofa_tab_lists_shelves_with_library_counts(tmp_path):
    c, _ = web(tmp_path)
    page = c.get("/discover").text
    assert "Popular Movies" in page and "30 of 40" in page and "/discover/shelf?type=tofa_shelf&amp;arg=popular-movies" in page


@respx.mock
def test_discover_other_tabs_render(tmp_path):
    respx.get(f"{TRAKT}/lists/popular").mock(return_value=httpx.Response(200, json=[
        {"like_count": 7, "list": {"name": "Best Heists", "item_count": 12, "ids": {"slug": "best-heists"},
                                   "user": {"ids": {"slug": "bob"}}}}]))
    c, _ = web(tmp_path)
    tab = c.get("/discover?source=trakt").text
    assert "Best Heists" in tab and "arg=bob/best-heists" in tab
    for src in ("trakt", "tmdb", "imdb"):
        r = c.get(f"/discover?source={src}")
        assert r.status_code == 200 and "Trending" in r.text or "Top rated" in r.text


@respx.mock
def test_shelf_page_shows_names_and_library_badges_and_create_makes_a_disabled_definition(tmp_path):
    respx.get(f"{TMDB}/movie/popular").mock(return_value=httpx.Response(200, json={"page": 1, "total_pages": 1, "results": [
        {"id": 1, "title": "Have It", "release_date": "2020-01-01", "poster_path": "/h.jpg"},
        {"id": 2, "title": "Lack It", "release_date": "2021-01-01"}]}))
    c, db = web(tmp_path)
    page = c.get("/discover/shelf?type=tmdb_chart&arg=movie/popular").text
    assert "Have It" in page and "Lack It" in page and "1 of 2" in page
    only = c.get("/discover/shelf?type=tmdb_chart&arg=movie/popular&only=1").text
    assert "Have It" in only and "Lack It" not in only
    r = c.post("/discover/create", data={"type": "tmdb_chart", "arg": "movie/popular", "name": "Popular"}, follow_redirects=False)
    d = db.list_definitions()[0]
    assert r.status_code == 303 and r.headers["location"] == f"/{d['id']}/preview"
    assert (d["source_type"], d["source_config"], d["enabled"]) == ("tmdb_chart", {"path": "movie/popular"}, False)


def test_image_proxy_only_serves_tofa_image_paths(tmp_path):
    c, _ = web(tmp_path)
    assert c.get("/discover/img", params={"path": "images/posters/a.jpg"}).content == b"IMG"
    for bad in ("../../etc/passwd", "http://evil/x", "api/v1/users", "images/../secret"):
        assert c.get("/discover/img", params={"path": bad}).status_code == 400


def test_create_refuses_unknown_source_and_missing_key(tmp_path):
    c, db = web(tmp_path)
    assert c.post("/discover/create", data={"type": "nope", "arg": "x", "name": "n"}).status_code == 400
    clear_cache()
    db2 = Db(tmp_path / "t2.db")
    nokey = TestClient(create_app(st(tmdb=None), db2, lambda: WebTofa()))
    r = nokey.post("/discover/create", data={"type": "tmdb_chart", "arg": "movie/popular", "name": "n"})
    assert r.status_code == 400 and "TMDB_API_KEY" in r.text and db2.list_definitions() == []
