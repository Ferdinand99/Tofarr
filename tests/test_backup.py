import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from app.auth import AuthStore
from app.backup import BackupError, export_data, import_data
from app.config import LiveSettings, Settings
from app.db import Db
from app.web.main import create_app


def populated(tmp_path, name="a.db"):
    db = Db(tmp_path / name)
    one = db.create_definition("Marvel", "MCU", "manual", {"text": "1726 # Iron Man"}, 1440, True)
    two = db.create_definition("Trending", "", "tmdb_chart", {"path": "movie/popular"}, 360, False)
    db.set_tofa_id(one, "tofa-collection-id"); db.update_definition(two, enabled=False)
    db.set_setting("tofa_url", "http://tofa:33333"); db.set_setting("tofa_api_key", "SECRET-TOFA")
    db.set_setting("tmdb_api_key", "SECRET-TMDB"); db.set_setting("seerr_url", "http://seerr:5055")
    AuthStore(db).create_user("ferdi", "supersecret1")
    return db


# ---------- export ----------

def test_export_has_collections_and_addresses_but_no_keys_by_default(tmp_path):
    data = export_data(populated(tmp_path), include_keys=False)
    assert data["app"] == "tofarr" and data["version"] == 1
    assert [d["name"] for d in data["definitions"]] == ["Marvel", "Trending"]
    assert data["definitions"][0]["tofa_id"] == "tofa-collection-id"
    assert data["settings"] == {"tofa_url": "http://tofa:33333", "seerr_url": "http://seerr:5055"}
    assert "SECRET" not in json.dumps(data)


def test_keys_are_only_included_when_asked_and_the_login_never_is(tmp_path):
    db = populated(tmp_path)
    text = json.dumps(export_data(db, include_keys=True))
    assert "SECRET-TOFA" in text and "SECRET-TMDB" in text
    for private in ("auth_hash", "auth_secret", "auth_user", "supersecret1", "scrypt"):
        assert private not in text


# ---------- import ----------

def test_roundtrip_into_an_empty_install(tmp_path):
    data = export_data(populated(tmp_path), include_keys=True)
    fresh = Db(tmp_path / "b.db")
    report = import_data(fresh, json.loads(json.dumps(data)))
    assert (report.added, report.skipped, report.errors) == (2, 0, [])
    got = {d["name"]: d for d in fresh.list_definitions()}
    assert got["Marvel"]["source_config"] == {"text": "1726 # Iron Man"} and got["Marvel"]["interval_minutes"] == 1440
    assert got["Marvel"]["tofa_id"] == "tofa-collection-id" and got["Marvel"]["prune"] is True
    assert got["Trending"]["prune"] is False and got["Trending"]["source_type"] == "tmdb_chart"
    assert fresh.get_setting("tofa_api_key") == "SECRET-TOFA" and fresh.get_setting("seerr_url") == "http://seerr:5055"


def test_importing_twice_adds_nothing_and_never_overwrites_existing_settings(tmp_path):
    data = export_data(populated(tmp_path), include_keys=True)
    fresh = Db(tmp_path / "b.db")
    fresh.set_setting("tofa_url", "http://my-own-address:33333")
    import_data(fresh, data)
    again = import_data(fresh, data)
    assert (again.added, again.skipped) == (0, 2) and len(fresh.list_definitions()) == 2
    assert fresh.get_setting("tofa_url") == "http://my-own-address:33333"


def test_a_collection_that_was_never_created_in_tofa_comes_back_disabled(tmp_path):
    data = export_data(populated(tmp_path), include_keys=False)
    data["definitions"][0]["tofa_id"] = None
    data["definitions"][0]["enabled"] = True
    fresh = Db(tmp_path / "b.db"); import_data(fresh, data)
    assert {d["name"]: d["enabled"] for d in fresh.list_definitions()}["Marvel"] is False


def test_a_collection_already_linked_to_tofa_keeps_its_schedule(tmp_path):
    data = export_data(populated(tmp_path), include_keys=False)
    data["definitions"][0]["enabled"] = True
    fresh = Db(tmp_path / "b.db"); import_data(fresh, data)
    assert {d["name"]: d["enabled"] for d in fresh.list_definitions()}["Marvel"] is True


@pytest.mark.parametrize("bad", [None, [], "text", {"app": "other", "version": 1, "definitions": []},
                                 {"app": "tofarr", "version": 99, "definitions": []},
                                 {"app": "tofarr", "version": 1, "definitions": "nope"}])
def test_files_that_are_not_a_tofarr_backup_are_refused(tmp_path, bad):
    with pytest.raises(BackupError):
        import_data(Db(tmp_path / "b.db"), bad)


def test_broken_entries_are_skipped_with_a_reason_and_the_rest_is_imported(tmp_path):
    base = {"source_type": "manual", "source_config": {"text": "1"}, "interval_minutes": 60}
    data = {"app": "tofarr", "version": 1, "settings": {"tofa_url": "http://x", "evil_key": "1", "auth_hash": "hack"},
            "definitions": [{**base, "name": "Good"}, {**base, "name": ""}, {**base, "name": "Odd", "source_type": "nope"},
                            {**base, "name": "Fast", "interval_minutes": 1}, {**base, "name": "NoCfg", "source_config": "x"},
                            "not an object"]}
    db = Db(tmp_path / "b.db"); report = import_data(db, data)
    assert [d["name"] for d in db.list_definitions()] == ["Good"] and report.added == 1 and len(report.errors) == 5
    assert db.get_setting("tofa_url") == "http://x" and db.get_setting("evil_key") is None and db.get_setting("auth_hash") is None


# ---------- web ----------

def web(tmp_path):
    db = populated(tmp_path)
    live = LiveSettings(Settings("", "", None, None, Path(".")), db)
    stub = type("T", (), {"system_info": lambda self: {"version": "1"}})()
    return TestClient(create_app(live, db, lambda: stub)), db


def test_export_downloads_a_json_file(tmp_path):
    c, _ = web(tmp_path)
    r = c.get("/backup/export")
    assert r.headers["content-disposition"].startswith('attachment; filename="tofarr-backup-') and r.headers["content-type"].startswith("application/json")
    assert "SECRET" not in r.text and json.loads(r.text)["definitions"][0]["name"] == "Marvel"
    assert "SECRET-TOFA" in c.get("/backup/export?keys=1").text


def test_import_through_the_settings_page(tmp_path):
    c, _ = web(tmp_path)
    data = {"app": "tofarr", "version": 1, "settings": {}, "definitions": [
        {"name": "Fresh", "overview": "", "source_type": "manual", "source_config": {"text": "1"}, "interval_minutes": 60}]}
    r = c.post("/backup/import", files={"file": ("b.json", json.dumps(data), "application/json")})
    assert r.status_code == 200 and "Added 1" in r.text and "Fresh" in c.get("/").text
    bad = c.post("/backup/import", files={"file": ("b.json", "{not json", "application/json")})
    assert bad.status_code == 400 and "not a valid" in bad.text.lower()
    big = c.post("/backup/import", files={"file": ("b.json", "x" * 2_000_000, "application/json")})
    assert big.status_code == 400 and "too large" in big.text.lower()
    assert c.post("/backup/import", data={}).status_code == 400


def test_the_settings_page_offers_backup_in_the_page_body(tmp_path):
    c, _ = web(tmp_path)
    page = c.get("/settings").text
    assert "<title>Settings · Tofarr</title>" in page                    # the panel must not leak into the title
    body = page[page.index("<main"):]
    assert 'action="/backup/export"' in body and 'action="/backup/import"' in body and "Download backup" in body


def test_backup_needs_login_when_login_is_on(tmp_path):
    db = populated(tmp_path)
    c = TestClient(create_app(Settings("", "", None, None, Path(".")), db, lambda: None, require_auth=True))
    assert c.get("/backup/export", follow_redirects=False).status_code == 303
    assert c.post("/backup/import", follow_redirects=False).status_code in (303, 401)
