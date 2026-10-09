from app.db import Db
from app.seeds import seed_defaults

def test_seed_once(tmp_path):
    db = Db(tmp_path / "t.db")
    seed_defaults(db); seed_defaults(db)
    ds = db.list_definitions()
    assert len(ds) == 1 and ds[0]["name"] == "MCU Timeline" and ds[0]["enabled"] is False
