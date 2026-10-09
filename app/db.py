import json, sqlite3, threading
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS definitions(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, overview TEXT,
  source_type TEXT NOT NULL, source_config TEXT NOT NULL, interval_minutes INTEGER NOT NULL,
  prune INTEGER NOT NULL DEFAULT 1, tofa_id TEXT, enabled INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS runs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, definition_id INTEGER NOT NULL, ts TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  status TEXT NOT NULL, message TEXT, added INTEGER, removed INTEGER, missing TEXT);
"""

class Db:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._c = sqlite3.connect(str(path), check_same_thread=False)
        self._c.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._c.executescript(SCHEMA)

    @staticmethod
    def _def(r):
        d = dict(r)
        d["source_config"] = json.loads(d["source_config"])
        d["prune"], d["enabled"] = bool(d["prune"]), bool(d["enabled"])
        return d

    def create_definition(self, name, overview, source_type, source_config, interval_minutes, prune=True) -> int:
        with self._lock:
            cur = self._c.execute(
                "INSERT INTO definitions(name,overview,source_type,source_config,interval_minutes,prune) VALUES(?,?,?,?,?,?)",
                (name, overview, source_type, json.dumps(source_config), interval_minutes, int(prune)))
            self._c.commit(); return cur.lastrowid

    def get_definition(self, id):
        with self._lock:
            r = self._c.execute("SELECT * FROM definitions WHERE id=?", (id,)).fetchone()
        return self._def(r) if r else None

    def list_definitions(self):
        with self._lock:
            return [self._def(r) for r in self._c.execute("SELECT * FROM definitions ORDER BY name")]

    def update_definition(self, id, **f):
        if "source_config" in f:
            f["source_config"] = json.dumps(f["source_config"])
        for k in ("prune", "enabled"):
            if k in f:
                f[k] = int(f[k])
        cols = ",".join(f"{k}=?" for k in f)
        with self._lock:
            self._c.execute(f"UPDATE definitions SET {cols} WHERE id=?", (*f.values(), id)); self._c.commit()

    def delete_definition(self, id):
        with self._lock:
            self._c.execute("DELETE FROM runs WHERE definition_id=?", (id,))
            self._c.execute("DELETE FROM definitions WHERE id=?", (id,)); self._c.commit()

    def set_tofa_id(self, id, tofa_id):
        self.update_definition(id, tofa_id=tofa_id)

    def add_run(self, definition_id, status, message, added, removed, missing):
        with self._lock:
            self._c.execute("INSERT INTO runs(definition_id,status,message,added,removed,missing) VALUES(?,?,?,?,?,?)",
                            (definition_id, status, message, added, removed, json.dumps(missing)))
            self._c.commit()

    def last_run(self, definition_id):
        with self._lock:
            r = self._c.execute("SELECT * FROM runs WHERE definition_id=? ORDER BY id DESC LIMIT 1",
                                (definition_id,)).fetchone()
        if not r:
            return None
        d = dict(r); d["missing"] = json.loads(d["missing"] or "[]"); return d
