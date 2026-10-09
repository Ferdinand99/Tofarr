import httpx, respx
from pathlib import Path
from fastapi.testclient import TestClient
from app.config import Settings, LiveSettings
from app.db import Db
from app.tofa.client import TofaClient
from app.web.main import create_app

ENV = Settings("", "", None, None, Path("."))


def build(tmp_path, env=ENV):
    db = Db(tmp_path / "t.db")
    live = LiveSettings(env, db)
    make = lambda: TofaClient(live.tofa_url, live.tofa_api_key, retries=1)
    return TestClient(create_app(live, db, make)), live, db


def test_db_overrides_env_and_blank_falls_back(tmp_path):
    env = Settings("http://env:1", "envkey", "envtmdb", None, Path("."))
    db = Db(tmp_path / "t.db")
    live = LiveSettings(env, db)
    assert live.tofa_url == "http://env:1" and live.tmdb_api_key == "envtmdb"
    live.save(tofa_url="http://db:2/", tofa_api_key="dbkey")
    assert live.tofa_url == "http://db:2" and live.tofa_api_key == "dbkey"
    assert live.tmdb_api_key == "envtmdb"


@respx.mock
def test_save_settings_tests_connection_and_never_echoes_key(tmp_path):
    respx.get("http://tofa:33333/api/v1/system/info").mock(
        return_value=httpx.Response(200, json={"version": "0.10.1"}))
    c, live, _ = build(tmp_path)
    r = c.post("/settings", data={"tofa_url": "http://tofa:33333", "tofa_api_key": "SECRET123",
                                  "tmdb_api_key": "", "trakt_client_id": ""})
    assert r.status_code == 200 and "0.10.1" in r.text
    assert live.tofa_url == "http://tofa:33333" and live.tofa_api_key == "SECRET123"
    assert "SECRET123" not in c.get("/settings").text


@respx.mock
def test_blank_key_keeps_existing_key(tmp_path):
    respx.get("http://tofa:33333/api/v1/system/info").mock(return_value=httpx.Response(200, json={"version": "1"}))
    c, live, _ = build(tmp_path)
    live.save(tofa_url="http://tofa:33333", tofa_api_key="KEEP")
    c.post("/settings", data={"tofa_url": "http://tofa:33333", "tofa_api_key": "",
                              "tmdb_api_key": "t", "trakt_client_id": ""})
    assert live.tofa_api_key == "KEEP" and live.tmdb_api_key == "t"


@respx.mock
def test_bad_key_is_saved_but_error_shown(tmp_path):
    respx.get("http://tofa:33333/api/v1/system/info").mock(return_value=httpx.Response(401))
    c, live, _ = build(tmp_path)
    r = c.post("/settings", data={"tofa_url": "http://tofa:33333", "tofa_api_key": "bad",
                                  "tmdb_api_key": "", "trakt_client_id": ""})
    assert "API key" in r.text and live.tofa_api_key == "bad"


def test_dashboard_links_to_settings_when_unconfigured(tmp_path):
    c, _, _ = build(tmp_path)
    page = c.get("/").text
    assert "/settings" in page and "not configured" in page.lower()


def test_url_without_scheme_rejected(tmp_path):
    c, live, _ = build(tmp_path)
    r = c.post("/settings", data={"tofa_url": "192.168.1.10:33333", "tofa_api_key": "k",
                                  "tmdb_api_key": "", "trakt_client_id": ""})
    assert r.status_code == 400 and live.tofa_url == ""
