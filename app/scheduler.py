import threading
from datetime import datetime, timedelta, timezone
from apscheduler.schedulers.background import BackgroundScheduler
from app.sync import RunResult, run_definition

SOON = timedelta(seconds=15)       # how long after startup an overdue collection waits
STAGGER = timedelta(seconds=20)    # gap between overdue collections, so Tofa is not hit all at once
RETRY_AFTER_FAILURE = 30           # minutes; a failed run is retried sooner than a whole interval


def plan_next(now: datetime, interval_minutes: int, last_ts: str | None, last_status: str | None,
              index: int = 0) -> datetime:
    """When a collection should run next.

    The schedule survives restarts: it counts from the last run instead of from startup, so a container that is
    restarted often still updates. A collection that is overdue (or never ran) runs shortly after startup.
    """
    soonest = now + SOON + STAGGER * index
    if not last_ts:
        return soonest
    last = datetime.strptime(last_ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)  # SQLite stores UTC
    wait = interval_minutes
    if last_status in ("failed", "aborted"):
        wait = min(interval_minutes, RETRY_AFTER_FAILURE)
    return max(last + timedelta(minutes=wait), soonest)


class Scheduler:
    def __init__(self, db, make_tofa, settings, clock=None):
        self.db, self.make_tofa, self.settings = db, make_tofa, settings
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._locks: dict[int, threading.Lock] = {}
        self._guard = threading.Lock()
        self._s = BackgroundScheduler()

    def start(self):
        for index, d in enumerate(d for d in self.db.list_definitions() if d["enabled"]):
            self.reschedule(d["id"], index=index)
        self._s.start()

    def _lock_for(self, def_id):
        with self._guard:
            return self._locks.setdefault(def_id, threading.Lock())

    def _job(self, def_id):
        lock = self._lock_for(def_id)
        if not lock.acquire(blocking=False):
            return RunResult("aborted", "Already running")
        try:
            return run_definition(def_id, db=self.db, tofa=self.make_tofa(), settings=self.settings)
        finally:
            lock.release()

    def reschedule(self, def_id, index: int = 0):
        self.remove(def_id)
        d = self.db.get_definition(def_id)
        if d and d["enabled"]:
            last = self.db.last_run(def_id)
            first = plan_next(self._clock(), d["interval_minutes"], last and last["ts"], last and last["status"], index)
            self._s.add_job(self._job, "interval", minutes=d["interval_minutes"], args=[def_id],
                            id=f"def-{def_id}", coalesce=True, max_instances=1, misfire_grace_time=300,
                            next_run_time=first)

    def remove(self, def_id):
        if self._s.get_job(f"def-{def_id}"):
            self._s.remove_job(f"def-{def_id}")

    def run_now(self, def_id):
        return self._job(def_id)
