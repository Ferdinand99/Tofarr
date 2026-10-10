from datetime import datetime, timedelta, timezone
from pathlib import Path
from app.config import Settings
from app.db import Db
from app.scheduler import Scheduler, plan_next

NOW = datetime(2026, 10, 10, 12, 0, 0, tzinfo=timezone.utc)
SOON = timedelta(seconds=15)


def ts(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")      # the format SQLite CURRENT_TIMESTAMP writes (UTC)


# ---------- when should the next run happen ----------

def test_a_collection_that_never_ran_starts_soon():
    assert plan_next(NOW, 1440, None, None) == NOW + SOON


def test_it_waits_out_the_rest_of_the_interval_after_a_restart():
    last = NOW - timedelta(hours=1)
    assert plan_next(NOW, 1440, ts(last), "ok") == last + timedelta(minutes=1440)


def test_an_overdue_collection_runs_soon_instead_of_waiting_a_whole_new_interval():
    assert plan_next(NOW, 1440, ts(NOW - timedelta(days=3)), "ok") == NOW + SOON


def test_overdue_ones_are_staggered_so_they_do_not_all_hit_tofa_at_once():
    assert plan_next(NOW, 1440, None, None, index=3) == NOW + SOON + timedelta(seconds=60)


def test_failed_runs_are_retried_within_half_an_hour_not_a_day_later():
    last = NOW - timedelta(minutes=10)
    for status in ("failed", "aborted"):
        assert plan_next(NOW, 1440, ts(last), status) == last + timedelta(minutes=30)
    recent = NOW - timedelta(minutes=5)
    assert plan_next(NOW, 10, ts(recent), "failed") == recent + timedelta(minutes=10)    # never later than the interval
    assert plan_next(NOW, 1440, ts(NOW - timedelta(hours=2)), "failed") == NOW + SOON


# ---------- the scheduler uses it ----------

class FakeJobs:
    def __init__(self): self.added, self.removed = [], []
    def get_job(self, job_id): return None
    def add_job(self, func, trigger, **kw): self.added.append(kw)
    def remove_job(self, job_id): self.removed.append(job_id)


def make(tmp_path):
    db = Db(tmp_path / "t.db")
    sch = Scheduler(db, lambda: None, Settings("", "", None, None, Path(".")), clock=lambda: NOW)
    sch._s = FakeJobs()
    return db, sch


def test_reschedule_sets_the_first_run_from_the_last_run(tmp_path):
    db, sch = make(tmp_path)
    i = db.create_definition("A", "", "manual", {"text": "1"}, 120)
    db.add_run(i, "ok", "", 0, 0, [])
    db._c.execute("UPDATE runs SET ts=?", (ts(NOW - timedelta(minutes=30)),)); db._c.commit()
    sch.reschedule(i)
    assert sch._s.added[0]["next_run_time"] == NOW + timedelta(minutes=90)
    assert sch._s.added[0]["minutes"] == 120 and sch._s.added[0]["id"] == f"def-{i}"


def test_start_staggers_several_overdue_collections(tmp_path):
    db, sch = make(tmp_path)
    ids = [db.create_definition(n, "", "manual", {"text": "1"}, 60) for n in "ABC"]
    db.create_definition("off", "", "manual", {"text": "1"}, 60)
    db.update_definition(db.list_definitions()[-1]["id"], enabled=False)
    for d in db.list_definitions():
        if d["name"] != "off":
            sch.reschedule(d["id"], index=ids.index(d["id"]))
    when = sorted(j["next_run_time"] for j in sch._s.added)
    assert when == [NOW + SOON, NOW + SOON + timedelta(seconds=20), NOW + SOON + timedelta(seconds=40)]


# ---------- history does not grow forever ----------

def test_only_the_latest_runs_of_each_collection_are_kept(tmp_path):
    db = Db(tmp_path / "t.db")
    a = db.create_definition("A", "", "manual", {"text": "1"}, 60)
    b = db.create_definition("B", "", "manual", {"text": "1"}, 60)
    for n in range(70):
        db.add_run(a, "ok", f"run {n}", 0, 0, [])
    db.add_run(b, "ok", "only one", 0, 0, [])
    count = lambda d: db._c.execute("SELECT COUNT(*) FROM runs WHERE definition_id=?", (d,)).fetchone()[0]
    assert count(a) == 50 and count(b) == 1
    assert db.last_run(a)["message"] == "run 69"
