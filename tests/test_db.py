from app.db import Db

def test_definition_roundtrip_and_runs(tmp_path):
    db = Db(tmp_path / "t.db")
    i = db.create_definition("MCU", "d", "manual", {"text": "1"}, 1440)
    d = db.get_definition(i)
    assert d["source_config"] == {"text": "1"} and d["prune"] is True and d["tofa_id"] is None
    db.set_tofa_id(i, "abc"); assert db.get_definition(i)["tofa_id"] == "abc"
    db.add_run(i, "ok", "fine", 2, 1, [{"tmdb_id": 9, "title": "x"}])
    r = db.last_run(i)
    assert r["status"] == "ok" and r["added"] == 2 and r["missing"][0]["tmdb_id"] == 9
    db.delete_definition(i); assert db.get_definition(i) is None and db.last_run(i) is None
