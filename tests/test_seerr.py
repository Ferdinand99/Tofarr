import json
import httpx, respx, pytest
from pathlib import Path
from fastapi.testclient import TestClient
from app.config import Settings, LiveSettings
from app.db import Db
from app.discover.service import clear_cache
from app.models import SourceItem
from app.seerr import SeerrClient, SeerrError
from app.web.main import create_app

SEERR = "http://seerr:5055"


def settings(seerr=True, tmdb="K"):
    return Settings("http://tofa:33333", "k", tmdb, None, Path("."),
                    SEERR if seerr else None, "SKEY" if seerr else None)


# ---------- client ----------

@respx.mock
def test_movie_request_body_and_key_header():
    route = respx.post(f"{SEERR}/api/v1/request").mock(return_value=httpx.Response(201, json={"id": 1}))
    assert SeerrClient(SEERR, "SKEY").request(603, "movie") == "requested"
    req = route.calls[0].request
    assert json.loads(req.content) == {"mediaType": "movie", "mediaId": 603}
    assert req.headers["x-api-key"] == "SKEY"


@respx.mock
def test_series_request_asks_for_all_seasons():
    route = respx.post(f"{SEERR}/api/v1/request").mock(return_value=httpx.Response(201, json={"id": 2}))
    SeerrClient(SEERR + "/", "k").request(84958, "tv")
    assert json.loads(route.calls[0].request.content) == {"mediaType": "tv", "mediaId": 84958, "seasons": "all"}


@respx.mock
def test_duplicate_is_reported_not_raised():
    respx.post(f"{SEERR}/api/v1/request").mock(return_value=httpx.Response(409, json={"message": "exists"}))
    assert SeerrClient(SEERR, "k").request(1, "movie") == "exists"


@respx.mock
def test_errors_are_readable():
    c = SeerrClient(SEERR, "bad")
    respx.post(f"{SEERR}/api/v1/request").mock(return_value=httpx.Response(401))
    with pytest.raises(SeerrError, match="API key"):
        c.request(1, "movie")
    respx.post(f"{SEERR}/api/v1/request").mock(return_value=httpx.Response(403, json={"message": "no permission"}))
    with pytest.raises(SeerrError, match="permission"):
        c.request(1, "movie")
    respx.post(f"{SEERR}/api/v1/request").mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(SeerrError, match="Cannot reach Seerr"):
        c.request(1, "movie")


@respx.mock
def test_status_labels_from_media_info():
    respx.get(f"{SEERR}/api/v1/movie/1").mock(return_value=httpx.Response(200, json={"mediaInfo": {"status": 3}}))
    respx.get(f"{SEERR}/api/v1/movie/2").mock(return_value=httpx.Response(200, json={"id": 2}))
    respx.get(f"{SEERR}/api/v1/tv/3").mock(return_value=httpx.Response(200, json={"mediaInfo": {"status": 2}}))
    respx.get(f"{SEERR}/api/v1/tv/4").mock(return_value=httpx.Response(500))
    got = SeerrClient(SEERR, "k").statuses([(1, "movie"), (2, "movie"), (3, "tv"), (4, "tv")])
    assert got == {(1, "movie"): "Downloading", (3, "tv"): "Requested"}


@respx.mock
def test_connection_test_reports_version_and_user():
    respx.get(f"{SEERR}/api/v1/status").mock(return_value=httpx.Response(200, json={"version": "3.0.1"}))
    respx.get(f"{SEERR}/api/v1/auth/me").mock(return_value=httpx.Response(200, json={"displayName": "Ferdi"}))
    assert SeerrClient(SEERR, "k").test() == "Seerr 3.0.1, signed in as Ferdi"


# ---------- settings ----------

def test_live_settings_store_seerr_values(tmp_path):
    db = Db(tmp_path / "t.db")
    live = LiveSettings(Settings("", "", None, None, Path(".")), db)
    assert live.seerr_url is None
    live.save(seerr_url="http://s:5055/", seerr_api_key="abc")
    assert live.seerr_url == "http://s:5055" and live.seerr_api_key == "abc"


@respx.mock
def test_settings_page_saves_seerr_tests_it_and_never_echoes_the_key(tmp_path):
    respx.get("http://tofa:33333/api/v1/system/info").mock(return_value=httpx.Response(200, json={"version": "1"}))
    respx.get(f"{SEERR}/api/v1/status").mock(return_value=httpx.Response(200, json={"version": "3.0.1"}))
    respx.get(f"{SEERR}/api/v1/auth/me").mock(return_value=httpx.Response(200, json={"displayName": "Ferdi"}))
    db = Db(tmp_path / "t.db")
    live = LiveSettings(Settings("", "", None, None, Path(".")), db)
    class T:
        def system_info(self): return {"version": "1"}
    c = TestClient(create_app(live, db, lambda: T()))
    r = c.post("/settings", data={"tofa_url": "http://tofa:33333", "tofa_api_key": "", "tmdb_api_key": "",
                                  "trakt_client_id": "", "seerr_url": SEERR, "seerr_api_key": "SECRETSEERR"})
    assert "Seerr 3.0.1, signed in as Ferdi" in r.text and live.seerr_api_key == "SECRETSEERR"
    assert "SECRETSEERR" not in c.get("/settings").text


# ---------- web ----------

class FakeSeerr:
    def __init__(self): self.requested = []; self.fail = set(); self.status = {}
    def request(self, tmdb_id, media_type):
        if tmdb_id in self.fail:
            raise SeerrError("Seerr says no")
        self.requested.append((tmdb_id, media_type))
        return "exists" if tmdb_id == 99 else "requested"
    def statuses(self, items): return self.status


class WebTofa:
    def system_info(self): return {"version": "1"}
    def resolve_tmdb(self, items): return {k: "m" for k in items if k[0] == 1}
    def collection_item_order(self, cid): return []
    def create_collection(self, n, o): return "c"
    def add_item(self, *a): pass
    def remove_item(self, *a): pass


def app_for(tmp_path, seerr_on=True, fake=None):
    clear_cache()
    db = Db(tmp_path / "t.db")
    fake = fake or FakeSeerr()
    c = TestClient(create_app(settings(seerr_on), db, lambda: WebTofa(), seerr_factory=lambda: fake))
    return c, db, fake


def test_request_endpoint_returns_json_for_the_button(tmp_path):
    c, _, fake = app_for(tmp_path)
    r = c.post("/seerr/request", data={"tmdb_id": "603", "media_type": "movie"})
    assert r.json() == {"ok": True, "label": "Requested"} and fake.requested == [(603, "movie")]
    assert c.post("/seerr/request", data={"tmdb_id": "99", "media_type": "tv"}).json() == {"ok": True, "label": "Already requested"}
    fake.fail.add(5)
    bad = c.post("/seerr/request", data={"tmdb_id": "5", "media_type": "movie"})
    assert bad.status_code == 502 and bad.json() == {"ok": False, "label": "Seerr says no"}
    assert c.post("/seerr/request", data={"tmdb_id": "x", "media_type": "movie"}).status_code == 400
    assert c.post("/seerr/request", data={"tmdb_id": "1", "media_type": "game"}).status_code == 400


def test_request_endpoint_needs_seerr_configured(tmp_path):
    c, _, fake = app_for(tmp_path, seerr_on=False)
    r = c.post("/seerr/request", data={"tmdb_id": "1", "media_type": "movie"})
    assert r.status_code == 400 and fake.requested == []


def source_defs(db):
    return db.create_definition("D", "", "manual", {"text": "1 # Have\n2 # Lack\n3 tv # Lack Show"}, 60)


def test_preview_offers_request_buttons_and_bulk_request_for_missing_titles(tmp_path):
    c, db, fake = app_for(tmp_path)
    i = source_defs(db)
    page = c.get(f"/{i}/preview").text
    assert page.count("data-request data-id") == 2 and "Lack Show" in page and "Request all 2 missing" in page
    fake.status = {(2, "movie"): "Requested"}                           # Seerr already knows about title 2
    page = c.get(f"/{i}/preview").text
    assert page.count("data-request data-id") == 1 and "Requested" in page


def test_preview_has_no_request_ui_without_seerr(tmp_path):
    c, db, _ = app_for(tmp_path, seerr_on=False)
    i = source_defs(db)
    page = c.get(f"/{i}/preview").text
    assert "data-request data-id" not in page and "Request all" not in page


def test_bulk_request_needs_confirmation_and_reports_each_title(tmp_path):
    c, db, fake = app_for(tmp_path)
    i = source_defs(db)
    assert c.post(f"/{i}/request-missing", data={}).status_code == 400 and fake.requested == []
    fake.fail.add(3)
    r = c.post(f"/{i}/request-missing", data={"confirm": "yes"})
    assert fake.requested == [(2, "movie")]
    assert "Lack" in r.text and "Seerr says no" in r.text
    assert "Requested 1 of 2" in r.text


def test_bulk_request_is_capped(tmp_path):
    c, db, fake = app_for(tmp_path)
    text = "\n".join(f"{n} # t{n}" for n in range(1000, 1130))
    i = db.create_definition("Big", "", "manual", {"text": text}, 60)
    c.post(f"/{i}/request-missing", data={"confirm": "yes"})
    assert len(fake.requested) == 100


@respx.mock
def test_shelf_page_shows_request_buttons_only_for_titles_you_lack(tmp_path):
    respx.get("https://api.themoviedb.org/3/movie/popular").mock(return_value=httpx.Response(200, json={
        "page": 1, "total_pages": 1, "results": [{"id": 1, "title": "Have It"}, {"id": 2, "title": "Lack It"}]}))
    c, _, _ = app_for(tmp_path)
    page = c.get("/discover/shelf?type=tmdb_chart&arg=movie/popular").text
    assert page.count("data-request data-id") == 1 and 'data-id="2"' in page
