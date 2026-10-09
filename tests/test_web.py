from pathlib import Path
from fastapi.testclient import TestClient
from app.config import Settings
from app.db import Db
from app.web.main import create_app

S = Settings("http://x", "k", None, None, Path("."))

class T:
    def system_info(self): return {"version": "0.10"}
    def resolve_tmdb(self, items): return {}
    def collection_item_order(self, cid): return []
    def create_collection(self, n, o): return "c1"
    def add_item(self, *a): pass
    def remove_item(self, *a): pass
    def delete_collection(self, cid): self.deleted = cid

def client(tmp_path):
    db = Db(tmp_path / "t.db")
    return TestClient(create_app(S, db, lambda: T())), db

def test_health_and_empty_dashboard(tmp_path):
    c, _ = client(tmp_path)
    assert c.get("/health").json() == {"ok": True}
    assert c.get("/").status_code == 200

def test_create_manual_definition_then_preview(tmp_path):
    c, db = client(tmp_path)
    r = c.post("/new", data={"name": "MCU", "overview": "", "source_type": "manual",
                             "manual_text": "1726", "interval_minutes": "1440"}, follow_redirects=False)
    assert r.status_code == 303 and len(db.list_definitions()) == 1
    assert "MCU" in c.get("/").text
    p = c.get(f"/{db.list_definitions()[0]['id']}/preview")
    assert p.status_code == 200 and "1726" in p.text

def test_missing_tmdb_key_rejected_at_save(tmp_path):
    c, db = client(tmp_path)
    r = c.post("/new", data={"name": "X", "source_type": "tmdb_collection", "tmdb_id": "10",
                             "interval_minutes": "60"})
    assert r.status_code == 400 and "TMDB_API_KEY" in r.text and db.list_definitions() == []

def test_delete_requires_confirm(tmp_path):
    c, db = client(tmp_path)
    i = db.create_definition("A", "", "manual", {"text": "1"}, 60)
    assert c.post(f"/{i}/delete", data={}).status_code == 400
    assert c.post(f"/{i}/delete", data={"confirm": "yes"}, follow_redirects=False).status_code == 303
    assert db.get_definition(i) is None
