import threading
import httpx, respx, pytest
from pathlib import Path
from fastapi.testclient import TestClient
from app.config import Settings
from app.db import Db
from app.scheduler import Scheduler
from app.sources import get_source
from app.sync import run_definition
from app.tofa.client import TofaClient, TofaError
from app.web.main import create_app

S = Settings("http://x", "k", "T", None, Path("."))


class FakeTofa:
    def __init__(self, library, coll_items=None, exists=True):
        self.library, self.items, self.exists = library, sorted(coll_items or []), exists
        self.created = 0
    def system_info(self): return {"version": "0"}
    def resolve_tmdb(self, items): return {k: self.library[k] for k in items if k in self.library}
    def create_collection(self, name, overview): self.created += 1; self.exists = True; return "cid"
    def collection_item_order(self, cid): return list(self.items) if self.exists else None
    def update_collection(self, cid, **kw): pass
    def add_item(self, cid, mid): self.items.append(mid) if mid not in self.items else None
    def remove_item(self, cid, mid): self.items.remove(mid) if mid in self.items else None
    def delete_collection(self, cid): pass


def make(tmp_path, text="1\n2"):
    db = Db(tmp_path / "t.db")
    return db, db.create_definition("A", "", "manual", {"text": text}, 60)


# 1: nothing resolved must never wipe or create
def test_nothing_resolved_leaves_collection_untouched(tmp_path):
    db, i = make(tmp_path)
    db.set_tofa_id(i, "cid")
    t = FakeTofa({}, coll_items={"a", "b"})
    r = run_definition(i, db=db, tofa=t, settings=S)
    assert r.status == "aborted" and set(t.items) == {"a", "b"}

def test_nothing_resolved_on_first_run_creates_nothing(tmp_path):
    db, i = make(tmp_path)
    t = FakeTofa({}, exists=False)
    r = run_definition(i, db=db, tofa=t, settings=S)
    assert r.status == "aborted" and t.created == 0


# 3: preview exposes which source items will be added
def test_preview_result_lists_add_items(tmp_path):
    db, i = make(tmp_path, text="1 # one\n2")
    t = FakeTofa({(1, "movie"): "a"}, exists=False)
    r = run_definition(i, db=db, tofa=t, settings=S, dry_run=True)
    assert [x.tmdb_id for x in r.add_items] == [1]


# 4: parallel runs of one definition must not both execute
def test_second_concurrent_run_is_refused(tmp_path):
    db, i = make(tmp_path)
    gate, started = threading.Event(), threading.Event()
    class Slow(FakeTofa):
        def resolve_tmdb(self, items):
            started.set(); gate.wait(5); return super().resolve_tmdb(items)
    sch = Scheduler(db, lambda: Slow({(1, "movie"): "a"}, exists=False), S)
    first = []
    th = threading.Thread(target=lambda: first.append(sch.run_now(i)))
    th.start(); assert started.wait(5)
    second = sch.run_now(i)
    gate.set(); th.join(5)
    assert second.status == "aborted" and "already running" in second.message.lower()
    assert first[0].status == "ok"


# 5: client retry/sleep behaviour
@respx.mock
def test_single_attempt_client_does_not_sleep(monkeypatch):
    slept = []
    monkeypatch.setattr("time.sleep", lambda s: slept.append(s))
    respx.get("http://t/api/v1/system/info").mock(side_effect=httpx.ConnectError("no"))
    with pytest.raises(TofaError):
        TofaClient("http://t", "k", retries=1).system_info()
    assert slept == []

@respx.mock
def test_no_sleep_after_final_attempt(monkeypatch):
    slept = []
    monkeypatch.setattr("time.sleep", lambda s: slept.append(s))
    respx.get("http://t/api/v1/system/info").mock(return_value=httpx.Response(503))
    with pytest.raises(TofaError):
        TofaClient("http://t", "k").system_info()
    assert len(slept) == 2

def test_dashboard_uses_probe_factory(tmp_path):
    db = Db(tmp_path / "t.db")
    class Down:
        def system_info(self): raise TofaError("down")
    c = TestClient(create_app(S, db, lambda: FakeTofa({}), probe_factory=lambda: Down()))
    assert "down" in c.get("/").text


# 6: cross-site POSTs rejected
def test_cross_origin_post_rejected(tmp_path):
    db, i = make(tmp_path)
    c = TestClient(create_app(S, db, lambda: FakeTofa({})))
    r = c.post(f"/{i}/delete", data={"confirm": "yes"}, headers={"Origin": "http://evil.example"})
    assert r.status_code == 403 and db.get_definition(i) is not None
    ok = c.post(f"/{i}/delete", data={"confirm": "yes"}, headers={"Origin": "http://testserver"},
                follow_redirects=False)
    assert ok.status_code == 303


# 2: no write before first preview
def test_run_before_preview_is_refused_then_allowed(tmp_path):
    db, i = make(tmp_path)
    t = FakeTofa({(1, "movie"): "a"}, exists=False)
    c = TestClient(create_app(S, db, lambda: t))
    r = c.post(f"/{i}/run", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/{i}/preview" and t.created == 0
    c.get(f"/{i}/preview")
    c.post(f"/{i}/run", follow_redirects=False)
    assert t.created == 1

def test_new_definitions_start_disabled_and_cannot_be_enabled_before_preview(tmp_path):
    db = Db(tmp_path / "t.db")
    c = TestClient(create_app(S, db, lambda: FakeTofa({(1, "movie"): "a"}, exists=False)))
    form = {"name": "X", "source_type": "manual", "manual_text": "1", "interval_minutes": "60",
            "enabled": "on"}
    c.post("/new", data=form, follow_redirects=False)
    d = db.list_definitions()[0]
    assert d["enabled"] is False
    r = c.post(f"/{d['id']}/edit", data=form)
    assert r.status_code == 400 and "preview" in r.text.lower()
    c.get(f"/{d['id']}/preview")
    c.post(f"/{d['id']}/edit", data=form, follow_redirects=False)
    assert db.get_definition(d["id"])["enabled"] is True


# 7: TMDB list pagination
@respx.mock
def test_tmdb_list_reads_all_pages():
    def h(r):
        p = int(r.url.params["page"])
        return httpx.Response(200, json={"page": p, "total_pages": 3,
                                         "items": [{"id": p, "media_type": "movie", "title": "t"}]})
    route = respx.get("https://api.themoviedb.org/3/list/9").mock(side_effect=h)
    got = get_source("tmdb_list", {"id": 9}, S).fetch()
    assert [i.tmdb_id for i in got] == [1, 2, 3] and route.call_count == 3
