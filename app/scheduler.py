from apscheduler.schedulers.background import BackgroundScheduler
from app.sync import run_definition

class Scheduler:
    def __init__(self, db, make_tofa, settings):
        self.db, self.make_tofa, self.settings = db, make_tofa, settings
        self._s = BackgroundScheduler()

    def start(self):
        for d in self.db.list_definitions():
            self.reschedule(d["id"])
        self._s.start()

    def _job(self, def_id):
        return run_definition(def_id, db=self.db, tofa=self.make_tofa(), settings=self.settings)

    def reschedule(self, def_id):
        self.remove(def_id)
        d = self.db.get_definition(def_id)
        if d and d["enabled"]:
            self._s.add_job(self._job, "interval", minutes=d["interval_minutes"], args=[def_id],
                            id=f"def-{def_id}", coalesce=True, max_instances=1, misfire_grace_time=300)

    def remove(self, def_id):
        if self._s.get_job(f"def-{def_id}"):
            self._s.remove_job(f"def-{def_id}")

    def run_now(self, def_id):
        return self._job(def_id)
