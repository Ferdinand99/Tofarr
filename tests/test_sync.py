from pathlib import Path
from app.config import Settings
from app.db import Db
from app.sync import run_definition
from app.tofa.client import TofaError

class FakeTofa:
    def __init__(self, library: dict, coll_items=None, exists=True):
        self.library, self.items, self.exists = library, sorted(coll_items or []), exists
        self.created = 0; self.log = []
    def resolve_tmdb(self, items): return {k: self.library[k] for k in items if k in self.library}
    def create_collection(self, name, overview): self.created += 1; self.exists = True; return "cid2"
    def collection_item_order(self, cid): return list(self.items) if self.exists else None
    def update_collection(self, cid, **kw): self.log.append(("patch", kw))
    def add_item(self, cid, mid): self.items.append(mid) if mid not in self.items else None; self.log.append(("add", mid))
    def remove_item(self, cid, mid): self.items.remove(mid) if mid in self.items else None; self.log.append(("rm", mid))

S = Settings("http://x", "k", None, None, Path("."))

def setup(tmp_path, text="1\n2\n3", **kw):
    db = Db(tmp_path / "t.db")
    i = db.create_definition("MCU", "d", "manual", {"text": text}, 60)
    return db, i

def test_first_run_creates_collection_and_adds_in_order(tmp_path):
    db, i = setup(tmp_path)
    t = FakeTofa({(1, "movie"): "a", (2, "movie"): "b"}, exists=False)
    r = run_definition(i, db=db, tofa=t, settings=S)
    assert r.status == "ok" and t.created == 1 and db.get_definition(i)["tofa_id"] == "cid2"
    assert [x for x in t.log if x[0] == "add"] == [("add", "a"), ("add", "b")]
    assert [m.tmdb_id for m in r.missing] == [3]

def test_second_run_is_noop(tmp_path):
    db, i = setup(tmp_path)
    t = FakeTofa({(1, "movie"): "a", (2, "movie"): "b", (3, "movie"): "c"}, exists=False)
    run_definition(i, db=db, tofa=t, settings=S); t.log.clear()
    r = run_definition(i, db=db, tofa=t, settings=S)
    assert r.added == 0 and r.removed == 0 and not [x for x in t.log if x[0] in ("add", "rm")]

def test_dry_run_writes_nothing(tmp_path):
    db, i = setup(tmp_path)
    t = FakeTofa({(1, "movie"): "a"}, exists=False)
    r = run_definition(i, db=db, tofa=t, settings=S, dry_run=True)
    assert r.status == "preview" and t.created == 0 and r.add_ids == ["a"] and db.last_run(i) is None

def test_empty_source_does_not_wipe_collection(tmp_path):
    db, i = setup(tmp_path, text="")
    db.set_tofa_id(i, "cid"); t = FakeTofa({}, coll_items={"a", "b"})
    r = run_definition(i, db=db, tofa=t, settings=S)
    assert r.status == "aborted" and set(t.items) == {"a", "b"}

def test_collection_deleted_in_tofa_is_recreated(tmp_path):
    db, i = setup(tmp_path, text="1")
    db.set_tofa_id(i, "old"); t = FakeTofa({(1, "movie"): "a"}, exists=False)
    run_definition(i, db=db, tofa=t, settings=S)
    assert db.get_definition(i)["tofa_id"] == "cid2"

def test_tofa_failure_is_recorded_not_raised(tmp_path):
    db, i = setup(tmp_path)
    class Boom(FakeTofa):
        def resolve_tmdb(self, items): raise TofaError("down")
    r = run_definition(i, db=db, tofa=Boom({}), settings=S)
    assert r.status == "failed" and "down" in r.message and db.last_run(i)["status"] == "failed"


def test_existing_collection_is_reordered_to_match_the_source(tmp_path):
    db, i = setup(tmp_path, text="1\n2\n3")
    db.set_tofa_id(i, "cid")
    t = FakeTofa({(1, "movie"): "a", (2, "movie"): "b", (3, "movie"): "c"}, coll_items=["c", "a", "b"])
    t.items = ["c", "a", "b"]
    r = run_definition(i, db=db, tofa=t, settings=S)
    assert t.items == ["a", "b", "c"] and r.moved == 3 and r.added == 0
    again = run_definition(i, db=db, tofa=t, settings=S)
    assert again.moved == 0 and again.removed == 0 and again.added == 0


def test_late_addition_at_the_end_keeps_existing_items_untouched(tmp_path):
    db, i = setup(tmp_path, text="1\n2\n3")
    db.set_tofa_id(i, "cid")
    t = FakeTofa({(1, "movie"): "a", (2, "movie"): "b", (3, "movie"): "c"})
    t.items = ["a", "b"]
    t.log.clear()
    r = run_definition(i, db=db, tofa=t, settings=S)
    assert t.items == ["a", "b", "c"] and t.log == [("add", "c")] and r.moved == 0
