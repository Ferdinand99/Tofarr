from pathlib import Path
from fastapi.testclient import TestClient
from app.config import Settings
from app.db import Db
from app.web.main import create_app


def client(tmp_path):
    db = Db(tmp_path / "t.db")
    return TestClient(create_app(Settings("", "", None, None, Path(".")), db, lambda: None))


def test_sidebar_shows_the_running_version(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_VERSION", "1.2.3")
    assert "1.2.3" in client(tmp_path).get("/settings").text


def test_version_defaults_to_dev(tmp_path, monkeypatch):
    monkeypatch.delenv("APP_VERSION", raising=False)
    assert ">dev<" in client(tmp_path).get("/settings").text
