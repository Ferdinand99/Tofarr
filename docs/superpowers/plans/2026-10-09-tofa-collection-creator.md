# Tofa Collection Creator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A single Docker container for Unraid that creates Tofa custom collections from external sources (TMDB, Trakt, manual lists, rules) and keeps them in sync on a schedule.

**Architecture:** FastAPI web app + APScheduler in one process. `TofaClient` is the only module that knows Tofa endpoint shapes. Sources are plugins returning an ordered list of TMDB ids. `sync.run_definition` resolves ids to Tofa `media_id`s, diffs against the live collection and applies PUT/DELETE item calls. Definitions and run history live in SQLite under `/config`.

**Tech Stack:** Python 3.12, FastAPI, Jinja2, httpx, APScheduler, sqlite3 (stdlib), pytest + respx, Docker, GitHub Actions (ghcr.io).

**Spec:** `docs/superpowers/specs/2026-10-09-tofa-collection-creator-design.md`

## Global Constraints

- Tofa auth: `Authorization: Bearer <admin API key>`, direct connection to `http://<ip>:33333` (API keys are rejected by relay/connect service).
- Base path `/api/v1/`; feature-detect with `GET /api/v1/system/info` (`api_version`, `capabilities`), never match on version numbers.
- `POST /api/v1/media/by-tmdb/batch` accepts at most 200 items per request (400 otherwise); body `{"items":[{"tmdb_id":int,"media_type":"movie"|"tv"}]}`, result entries `{tmdb_id, media_type, media_id, files, ...}`.
- Collection endpoints used: `GET/POST /collections/custom`, `GET/PATCH/DELETE /collections/custom/{id}`, `PUT/DELETE /collections/custom/{id}/items/{media_id}`, `PUT /collections/{id}/artwork/{kind}`. Only the first two plus GET-by-id are in the public spec; the rest come from the server's Settings > API page and must be verified live (Task 1).
- No reorder endpoint exists. Order is not synchronised; items are added in source order.
- Container: port `8080`, volume `/config`, env `TOFA_URL`, `TOFA_API_KEY`, `TMDB_API_KEY`, `TRAKT_CLIENT_ID`, `TZ`. Image `ghcr.io/ferdinand99/tofa-collection-creator`.
- Unraid template file: `tofa-collection-creator.xml` in `Ferdinand99/unraid-templates/templates/`, same style as `sylo.xml`.
- Never delete a Tofa collection unless the user confirms in the UI; never write on first run without a previewed diff.

## Review Focus

- Source returns an id Tofa does not have in its library: reported as `missing`, not an error; picked up automatically on a later run once the file is scanned.
- More than 200 ids: lookups are chunked; 201 ids must not 400.
- Source returns duplicates or an empty list: duplicates collapsed; an empty source result must NOT wipe the collection (abort with a visible warning).
- Collection deleted in Tofa by hand: next run recreates it and stores the new id.
- Tofa unreachable / 401 / 5xx / 429: run is marked failed with a readable message, retried with backoff, scheduler keeps running.
- TMDB/Trakt API key missing: definition is rejected at save time with a clear message, not at 03:00.

---

## File Structure

```
app/
  __init__.py
  config.py            # env settings
  models.py            # SourceItem, DiffResult dataclasses
  db.py                # sqlite: definitions, runs
  tofa/client.py       # TofaClient (only place that knows endpoints)
  sources/__init__.py  # registry: get_source(type, cfg, settings)
  sources/base.py      # Source protocol
  sources/manual.py    # pasted TMDB ids
  sources/tmdb.py      # TMDB collection / list / discover rules
  sources/trakt.py     # Trakt list
  diff.py              # pure diff function
  sync.py              # run_definition()
  scheduler.py         # APScheduler wiring
  web/main.py          # FastAPI routes
  web/templates/*.html
tests/
Dockerfile  docker-compose.yml  requirements.txt  pytest.ini
.github/workflows/docker.yml
unraid/tofa-collection-creator.xml
scripts/probe_tofa.py
```

---

### Task 1: Project scaffold + live API probe

**Files:**
- Create: `requirements.txt`, `pytest.ini`, `app/__init__.py`, `app/config.py`, `scripts/probe_tofa.py`, `tests/test_config.py`

**Interfaces:**
- Produces: `app.config.Settings` (dataclass: `tofa_url: str`, `tofa_api_key: str`, `tmdb_api_key: str|None`, `trakt_client_id: str|None`, `config_dir: Path`) and `Settings.from_env() -> Settings`.

- [ ] **Step 1: Create `requirements.txt` and `pytest.ini`**

```
fastapi==0.115.*
uvicorn[standard]==0.32.*
jinja2==3.1.*
httpx==0.27.*
apscheduler==3.10.*
python-multipart==0.0.*
pytest==8.*
respx==0.21.*
```
```ini
[pytest]
testpaths = tests
pythonpath = .
```

- [ ] **Step 2: Failing test `tests/test_config.py`**

```python
from app.config import Settings

def test_from_env_strips_trailing_slash(monkeypatch, tmp_path):
    monkeypatch.setenv("TOFA_URL", "http://10.0.0.5:33333/")
    monkeypatch.setenv("TOFA_API_KEY", "k")
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
    s = Settings.from_env()
    assert s.tofa_url == "http://10.0.0.5:33333"
    assert s.tmdb_api_key is None
    assert s.config_dir == tmp_path
```

- [ ] **Step 3: Run** `python -m venv .venv && .venv/Scripts/pip install -r requirements.txt && .venv/Scripts/pytest tests/test_config.py -v` — Expected: FAIL (module missing).

- [ ] **Step 4: Implement `app/config.py`**

```python
import os
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class Settings:
    tofa_url: str
    tofa_api_key: str
    tmdb_api_key: str | None
    trakt_client_id: str | None
    config_dir: Path

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            tofa_url=os.environ.get("TOFA_URL", "").rstrip("/"),
            tofa_api_key=os.environ.get("TOFA_API_KEY", ""),
            tmdb_api_key=os.environ.get("TMDB_API_KEY") or None,
            trakt_client_id=os.environ.get("TRAKT_CLIENT_ID") or None,
            config_dir=Path(os.environ.get("CONFIG_DIR", "/config")),
        )
```
Also create empty `app/__init__.py`. Run test — PASS.

- [ ] **Step 5: Write `scripts/probe_tofa.py`** (throwaway discovery tool; prints status + body for each call so the executor records real item-endpoint shapes)

```python
"""Usage: TOFA_URL=... TOFA_API_KEY=... python scripts/probe_tofa.py <tmdb_id>"""
import os, sys, json, httpx

base, key = os.environ["TOFA_URL"].rstrip("/"), os.environ["TOFA_API_KEY"]
h = {"Authorization": f"Bearer {key}"}
c = httpx.Client(base_url=base + "/api/v1", headers=h, timeout=20)

def show(label, r):
    print(f"--- {label}: {r.status_code}\n{r.text[:800]}")

show("system/info", c.get("/system/info"))
tmdb = int(sys.argv[1])
look = c.post("/media/by-tmdb/batch", json={"items": [{"tmdb_id": tmdb, "media_type": "movie"}]})
show("by-tmdb", look)
media_id = look.json()["results"][0]["media_id"]
created = c.post("/collections/custom", json={"name": "probe-delete-me", "overview": "x"})
show("create", created)
cid = created.json()["id"]
show("PUT item", c.put(f"/collections/custom/{cid}/items/{media_id}"))
show("GET coll", c.get(f"/collections/custom/{cid}"))
show("PATCH", c.patch(f"/collections/custom/{cid}", json={"overview": "y"}))
show("DELETE item", c.delete(f"/collections/custom/{cid}/items/{media_id}"))
show("DELETE coll", c.delete(f"/collections/custom/{cid}"))
```

- [ ] **Step 6: Run the probe against the real server** with a TMDB id that is in the library. Record in `docs/superpowers/specs/tofa-api-observed.md`: status codes, whether PUT item needs a body, whether PATCH takes `name`/`overview`, whether `items` in GET collection carry `id` (media_id) or another field, and how the server orders items. **If PUT/PATCH/DELETE return 404/405, stop and report to the user** (fallback: capture browser network calls).

- [ ] **Step 7: Commit** `git init` first if no repo exists, then `git add -A && git commit -m "chore: scaffold project and probe tofa api"`

---

### Task 2: TofaClient

**Files:**
- Create: `app/tofa/__init__.py`, `app/tofa/client.py`, `tests/test_tofa_client.py`

**Interfaces:**
- Consumes: `Settings`.
- Produces:
  - `class TofaError(Exception)` with `.status: int|None`
  - `class TofaClient(base_url: str, api_key: str, *, transport=None)`
  - `.system_info() -> dict`
  - `.resolve_tmdb(items: list[tuple[int,str]]) -> dict[tuple[int,str], str]` maps `(tmdb_id, media_type)` to `media_id`, omitting items not in the library; chunks by 200.
  - `.create_collection(name: str, overview: str|None) -> str` (id)
  - `.get_collection(cid: str) -> dict | None` (None on 404)
  - `.collection_item_ids(cid: str) -> set[str]|None`
  - `.update_collection(cid, *, name=None, overview=None) -> None`
  - `.add_item(cid, media_id) -> None`, `.remove_item(cid, media_id) -> None`, `.delete_collection(cid) -> None`

Adjust field names to what Task 1 observed (the code below assumes `items[].id` is the media id).

- [ ] **Step 1: Failing tests**

```python
import httpx, respx, pytest
from app.tofa.client import TofaClient, TofaError

BASE = "http://t:33333"

@respx.mock
def test_resolve_chunks_by_200_and_omits_missing():
    calls = []
    def handler(request):
        import json
        items = json.loads(request.content)["items"]
        calls.append(len(items))
        return httpx.Response(200, json={"results": [
            {"tmdb_id": i["tmdb_id"], "media_type": "movie", "media_id": f"m{i['tmdb_id']}", "files": [{}]}
            for i in items if i["tmdb_id"] != 5]})
    respx.post(f"{BASE}/api/v1/media/by-tmdb/batch").mock(side_effect=handler)
    c = TofaClient(BASE, "k")
    out = c.resolve_tmdb([(i, "movie") for i in range(1, 202)])
    assert calls == [200, 1]
    assert (5, "movie") not in out and out[(6, "movie")] == "m6"

@respx.mock
def test_results_without_files_count_as_missing():
    respx.post(f"{BASE}/api/v1/media/by-tmdb/batch").mock(return_value=httpx.Response(
        200, json={"results": [{"tmdb_id": 1, "media_type": "movie", "media_id": "m1", "files": []}]}))
    assert TofaClient(BASE, "k").resolve_tmdb([(1, "movie")]) == {}

@respx.mock
def test_get_collection_404_returns_none():
    respx.get(f"{BASE}/api/v1/collections/custom/x").mock(return_value=httpx.Response(404))
    assert TofaClient(BASE, "k").get_collection("x") is None

@respx.mock
def test_401_raises_readable_error():
    respx.get(f"{BASE}/api/v1/system/info").mock(return_value=httpx.Response(401))
    with pytest.raises(TofaError) as e:
        TofaClient(BASE, "bad").system_info()
    assert "API key" in str(e.value)

@respx.mock
def test_retries_on_429_then_succeeds(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    route = respx.get(f"{BASE}/api/v1/system/info").mock(side_effect=[
        httpx.Response(429), httpx.Response(200, json={"version": "1"})])
    assert TofaClient(BASE, "k").system_info()["version"] == "1"
    assert route.call_count == 2
```

- [ ] **Step 2: Run** `pytest tests/test_tofa_client.py -v` — Expected: FAIL (import error).

- [ ] **Step 3: Implement `app/tofa/client.py`**

```python
import time
import httpx

class TofaError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status

class TofaClient:
    BATCH = 200
    RETRIES = 3

    def __init__(self, base_url: str, api_key: str, *, transport=None):
        self._http = httpx.Client(
            base_url=base_url.rstrip("/") + "/api/v1",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30, transport=transport)

    def _req(self, method: str, path: str, **kw) -> httpx.Response:
        last: Exception | None = None
        for attempt in range(self.RETRIES):
            try:
                r = self._http.request(method, path, **kw)
            except httpx.TransportError as e:
                last = TofaError(f"Cannot reach Tofa server: {e}")
            else:
                if r.status_code in (401, 403):
                    raise TofaError("Tofa rejected the API key (401/403). Create an admin API key "
                                    "under Server > Settings > API keys.", r.status_code)
                if r.status_code == 429 or r.status_code >= 500:
                    last = TofaError(f"Tofa returned {r.status_code}", r.status_code)
                else:
                    return r
            time.sleep(2 ** attempt)
        raise last  # type: ignore[misc]

    def _ok(self, r: httpx.Response) -> httpx.Response:
        if r.status_code >= 400:
            raise TofaError(f"Tofa returned {r.status_code}: {r.text[:200]}", r.status_code)
        return r

    def system_info(self) -> dict:
        return self._ok(self._req("GET", "/system/info")).json()

    def resolve_tmdb(self, items: list[tuple[int, str]]) -> dict[tuple[int, str], str]:
        out: dict[tuple[int, str], str] = {}
        for i in range(0, len(items), self.BATCH):
            chunk = items[i:i + self.BATCH]
            body = {"items": [{"tmdb_id": t, "media_type": m} for t, m in chunk]}
            data = self._ok(self._req("POST", "/media/by-tmdb/batch", json=body)).json()
            for res in data["results"]:
                if res.get("media_id") and res.get("files"):
                    out[(res["tmdb_id"], res["media_type"])] = res["media_id"]
        return out

    def create_collection(self, name: str, overview: str | None) -> str:
        r = self._ok(self._req("POST", "/collections/custom", json={"name": name, "overview": overview}))
        return r.json()["id"]

    def get_collection(self, cid: str) -> dict | None:
        r = self._req("GET", f"/collections/custom/{cid}")
        if r.status_code == 404:
            return None
        return self._ok(r).json()

    def collection_item_ids(self, cid: str) -> set[str] | None:
        c = self.get_collection(cid)
        return None if c is None else {i["id"] for i in c["items"]}

    def update_collection(self, cid: str, *, name: str | None = None, overview: str | None = None) -> None:
        body = {k: v for k, v in (("name", name), ("overview", overview)) if v is not None}
        self._ok(self._req("PATCH", f"/collections/custom/{cid}", json=body))

    def add_item(self, cid: str, media_id: str) -> None:
        self._ok(self._req("PUT", f"/collections/custom/{cid}/items/{media_id}"))

    def remove_item(self, cid: str, media_id: str) -> None:
        self._ok(self._req("DELETE", f"/collections/custom/{cid}/items/{media_id}"))

    def delete_collection(self, cid: str) -> None:
        self._ok(self._req("DELETE", f"/collections/custom/{cid}"))
```

- [ ] **Step 4: Run tests** — Expected: PASS.
- [ ] **Step 5: Commit** `git add -A && git commit -m "feat: tofa client"`

---

### Task 3: Models + diff (pure logic)

**Files:**
- Create: `app/models.py`, `app/diff.py`, `tests/test_diff.py`

**Interfaces:**
- Produces:
  - `SourceItem(tmdb_id: int, media_type: str = "movie", title: str = "")` frozen dataclass; key = `(tmdb_id, media_type)`.
  - `dedupe(items: list[SourceItem]) -> list[SourceItem]` keeps first occurrence, preserves order.
  - `DiffResult(add: list[str], remove: list[str], missing: list[SourceItem], unchanged: int)`
  - `compute_diff(items: list[SourceItem], resolved: dict[tuple[int,str], str], current: set[str], prune: bool) -> DiffResult`

- [ ] **Step 1: Failing tests**

```python
from app.models import SourceItem, dedupe
from app.diff import compute_diff

def S(i, m="movie"): return SourceItem(i, m, f"t{i}")

def test_dedupe_keeps_first_order():
    assert [s.tmdb_id for s in dedupe([S(2), S(1), S(2)])] == [2, 1]

def test_diff_add_remove_missing_in_source_order():
    items = [S(3), S(1), S(2), S(9)]
    resolved = {(1, "movie"): "a", (2, "movie"): "b", (3, "movie"): "c"}
    d = compute_diff(items, resolved, current={"b", "z"}, prune=True)
    assert d.add == ["c", "a"]          # source order
    assert d.remove == ["z"]
    assert [m.tmdb_id for m in d.missing] == [9]
    assert d.unchanged == 1

def test_prune_false_never_removes():
    d = compute_diff([S(1)], {(1, "movie"): "a"}, current={"z"}, prune=False)
    assert d.remove == [] and d.add == ["a"]
```

- [ ] **Step 2: Run** — FAIL. **Step 3: Implement**

```python
# app/models.py
from dataclasses import dataclass, field

@dataclass(frozen=True)
class SourceItem:
    tmdb_id: int
    media_type: str = "movie"
    title: str = ""
    @property
    def key(self) -> tuple[int, str]:
        return (self.tmdb_id, self.media_type)

def dedupe(items: list[SourceItem]) -> list[SourceItem]:
    seen, out = set(), []
    for it in items:
        if it.key not in seen:
            seen.add(it.key); out.append(it)
    return out

@dataclass
class DiffResult:
    add: list[str] = field(default_factory=list)
    remove: list[str] = field(default_factory=list)
    missing: list[SourceItem] = field(default_factory=list)
    unchanged: int = 0
```
```python
# app/diff.py
from app.models import SourceItem, DiffResult

def compute_diff(items: list[SourceItem], resolved: dict[tuple[int, str], str],
                 current: set[str], prune: bool) -> DiffResult:
    d = DiffResult()
    wanted: set[str] = set()
    for it in items:
        mid = resolved.get(it.key)
        if mid is None:
            d.missing.append(it); continue
        wanted.add(mid)
        if mid in current:
            d.unchanged += 1
        else:
            d.add.append(mid)
    if prune:
        d.remove = sorted(current - wanted)
    return d
```

- [ ] **Step 4: Run** — PASS. **Step 5: Commit** `feat: diff logic`.

---

### Task 4: Sources (manual, TMDB, Trakt)

**Files:**
- Create: `app/sources/__init__.py`, `base.py`, `manual.py`, `tmdb.py`, `trakt.py`, `tests/test_sources.py`

**Interfaces:**
- Consumes: `SourceItem`, `Settings`.
- Produces:
  - `SourceError(Exception)` in `base.py`
  - `get_source(type: str, cfg: dict, settings: Settings, transport=None)` returns object with `.fetch() -> list[SourceItem]`; raises `SourceError` on unknown type or missing key. `type` in `{"manual","tmdb_collection","tmdb_list","tmdb_discover","trakt_list"}`.
  - Configs: manual `{"text": "1726\n1724 movie\n1399 tv"}`; tmdb_collection `{"id": 86311}`; tmdb_list `{"id": 8200000}`; tmdb_discover `{"media_type":"movie","params":{"with_people":"1136406","sort_by":"primary_release_date.asc"},"max_pages":3}`; trakt_list `{"user":"u","slug":"s"}`.

- [ ] **Step 1: Failing tests**

```python
import httpx, respx, pytest
from app.config import Settings
from app.sources import get_source
from app.sources.base import SourceError
from pathlib import Path

def st(tmdb="T", trakt="C"):
    return Settings("http://x", "k", tmdb, trakt, Path("."))

def test_manual_parses_ids_types_comments():
    s = get_source("manual", {"text": "1726 # Iron Man\n\n1399 tv\nbad\n"}, st())
    got = s.fetch()
    assert [(i.tmdb_id, i.media_type) for i in got] == [(1726, "movie"), (1399, "tv")]

@respx.mock
def test_tmdb_collection_sorted_by_release_date():
    respx.get("https://api.themoviedb.org/3/collection/10").mock(return_value=httpx.Response(200, json={"parts": [
        {"id": 2, "title": "B", "release_date": "2012-01-01"},
        {"id": 1, "title": "A", "release_date": "2008-01-01"},
        {"id": 3, "title": "C", "release_date": ""}]}))
    got = get_source("tmdb_collection", {"id": 10}, st()).fetch()
    assert [i.tmdb_id for i in got] == [1, 2, 3]

@respx.mock
def test_tmdb_discover_paginates_up_to_max_pages():
    route = respx.get("https://api.themoviedb.org/3/discover/movie").mock(side_effect=lambda r: httpx.Response(
        200, json={"page": int(r.url.params["page"]), "total_pages": 5,
                   "results": [{"id": int(r.url.params["page"]) * 10, "title": "x"}]}))
    got = get_source("tmdb_discover", {"media_type": "movie", "params": {}, "max_pages": 2}, st()).fetch()
    assert [i.tmdb_id for i in got] == [10, 20] and route.call_count == 2

@respx.mock
def test_trakt_list_maps_movies_and_shows_in_rank_order():
    respx.get("https://api.trakt.tv/users/u/lists/s/items").mock(return_value=httpx.Response(200, json=[
        {"rank": 2, "type": "movie", "movie": {"title": "B", "ids": {"tmdb": 2}}},
        {"rank": 1, "type": "show", "show": {"title": "A", "ids": {"tmdb": 1}}},
        {"rank": 3, "type": "movie", "movie": {"title": "N", "ids": {"tmdb": None}}}]))
    got = get_source("trakt_list", {"user": "u", "slug": "s"}, st()).fetch()
    assert [(i.tmdb_id, i.media_type) for i in got] == [(1, "tv"), (2, "movie")]

def test_missing_key_rejected_at_construction():
    with pytest.raises(SourceError, match="TMDB_API_KEY"):
        get_source("tmdb_collection", {"id": 1}, st(tmdb=None))
    with pytest.raises(SourceError, match="TRAKT_CLIENT_ID"):
        get_source("trakt_list", {"user": "u", "slug": "s"}, st(trakt=None))
    with pytest.raises(SourceError, match="Unknown source"):
        get_source("nope", {}, st())
```

- [ ] **Step 2: Run** — FAIL. **Step 3: Implement**

```python
# app/sources/base.py
class SourceError(Exception):
    pass
```
```python
# app/sources/manual.py
import re
from app.models import SourceItem

class ManualSource:
    def __init__(self, cfg: dict):
        self.text = cfg.get("text", "")

    def fetch(self) -> list[SourceItem]:
        out = []
        for line in self.text.splitlines():
            line = line.split("#", 1)[0].strip()
            m = re.fullmatch(r"(\d+)(?:\s+(movie|tv))?", line)
            if m:
                out.append(SourceItem(int(m.group(1)), m.group(2) or "movie"))
        return out
```
```python
# app/sources/tmdb.py
import httpx
from app.models import SourceItem
from app.sources.base import SourceError

API = "https://api.themoviedb.org/3"

class _Tmdb:
    def __init__(self, key: str, transport=None):
        self.http = httpx.Client(base_url=API, params={"api_key": key}, timeout=30, transport=transport)

    def get(self, path: str, **params) -> dict:
        r = self.http.get(path, params=params)
        if r.status_code == 401:
            raise SourceError("TMDB rejected TMDB_API_KEY")
        if r.status_code >= 400:
            raise SourceError(f"TMDB returned {r.status_code} for {path}")
        return r.json()

class TmdbCollectionSource(_Tmdb):
    def __init__(self, key, cfg, transport=None):
        super().__init__(key, transport); self.id = cfg["id"]

    def fetch(self) -> list[SourceItem]:
        parts = self.get(f"/collection/{self.id}")["parts"]
        parts.sort(key=lambda p: p.get("release_date") or "9999")
        return [SourceItem(p["id"], "movie", p.get("title", "")) for p in parts]

class TmdbListSource(_Tmdb):
    def __init__(self, key, cfg, transport=None):
        super().__init__(key, transport); self.id = cfg["id"]

    def fetch(self) -> list[SourceItem]:
        data = self.get(f"/list/{self.id}")
        return [SourceItem(i["id"], i.get("media_type", "movie"), i.get("title") or i.get("name", ""))
                for i in data["items"]]

class TmdbDiscoverSource(_Tmdb):
    def __init__(self, key, cfg, transport=None):
        super().__init__(key, transport)
        self.mt = cfg.get("media_type", "movie")
        self.params = cfg.get("params", {})
        self.max_pages = int(cfg.get("max_pages", 3))

    def fetch(self) -> list[SourceItem]:
        out: list[SourceItem] = []
        for page in range(1, self.max_pages + 1):
            data = self.get(f"/discover/{self.mt}", page=page, **self.params)
            out += [SourceItem(r["id"], self.mt, r.get("title") or r.get("name", "")) for r in data["results"]]
            if page >= data.get("total_pages", 1):
                break
        return out
```
```python
# app/sources/trakt.py
import httpx
from app.models import SourceItem
from app.sources.base import SourceError

class TraktListSource:
    def __init__(self, client_id: str, cfg: dict, transport=None):
        self.user, self.slug = cfg["user"], cfg["slug"]
        self.http = httpx.Client(base_url="https://api.trakt.tv", timeout=30, transport=transport, headers={
            "trakt-api-key": client_id, "trakt-api-version": "2", "Content-Type": "application/json"})

    def fetch(self) -> list[SourceItem]:
        r = self.http.get(f"/users/{self.user}/lists/{self.slug}/items")
        if r.status_code >= 400:
            raise SourceError(f"Trakt returned {r.status_code} (is the list public?)")
        out = []
        for row in sorted(r.json(), key=lambda x: x.get("rank", 0)):
            kind = row["type"]
            if kind not in ("movie", "show"):
                continue
            obj = row[kind]
            tmdb = (obj.get("ids") or {}).get("tmdb")
            if tmdb:
                out.append(SourceItem(int(tmdb), "movie" if kind == "movie" else "tv", obj.get("title", "")))
        return out
```
```python
# app/sources/__init__.py
from app.config import Settings
from app.sources.base import SourceError
from app.sources.manual import ManualSource
from app.sources.tmdb import TmdbCollectionSource, TmdbListSource, TmdbDiscoverSource
from app.sources.trakt import TraktListSource

SOURCE_TYPES = ["manual", "tmdb_collection", "tmdb_list", "tmdb_discover", "trakt_list"]
_TMDB = {"tmdb_collection": TmdbCollectionSource, "tmdb_list": TmdbListSource, "tmdb_discover": TmdbDiscoverSource}

def get_source(type: str, cfg: dict, settings: Settings, transport=None):
    if type == "manual":
        return ManualSource(cfg)
    if type in _TMDB:
        if not settings.tmdb_api_key:
            raise SourceError("TMDB_API_KEY is not set (free key at themoviedb.org/settings/api)")
        return _TMDB[type](settings.tmdb_api_key, cfg, transport)
    if type == "trakt_list":
        if not settings.trakt_client_id:
            raise SourceError("TRAKT_CLIENT_ID is not set (create an app at trakt.tv/oauth/applications)")
        return TraktListSource(settings.trakt_client_id, cfg, transport)
    raise SourceError(f"Unknown source type: {type}")
```

- [ ] **Step 4: Run** `pytest tests/test_sources.py -v` — PASS. **Step 5: Commit** `feat: sources`.

---

### Task 5: Database

**Files:**
- Create: `app/db.py`, `tests/test_db.py`

**Interfaces:**
- Produces: `class Db(path)` with
  - `create_definition(name, overview, source_type, source_config: dict, interval_minutes: int, prune: bool=True) -> int`
  - `get_definition(id) -> dict|None`, `list_definitions() -> list[dict]`
  - `update_definition(id, **fields)`, `delete_definition(id)`
  - `set_tofa_id(id, tofa_id: str|None)`
  - `add_run(definition_id, status: str, message: str, added: int, removed: int, missing: list[dict]) -> None`
  - `last_run(definition_id) -> dict|None`
  - definition dict keys: `id,name,overview,source_type,source_config(dict),interval_minutes,prune(bool),tofa_id,enabled(bool)`.
  - run dict keys: `id,definition_id,ts,status,message,added,removed,missing(list)`.

- [ ] **Step 1: Failing test**

```python
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
```

- [ ] **Step 2: Run** FAIL. **Step 3: Implement**

```python
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
```

- [ ] **Step 4: Run** PASS. **Step 5: Commit** `feat: sqlite storage`.

---

### Task 6: Sync engine

**Files:**
- Create: `app/sync.py`, `tests/test_sync.py`

**Interfaces:**
- Consumes: `Db`, `TofaClient`, `get_source`, `compute_diff`, `dedupe`, `SourceError`, `TofaError`.
- Produces:
  - `RunResult(status: str, message: str, added: int, removed: int, missing: list[SourceItem], add_ids: list[str], remove_ids: list[str])`
  - `run_definition(def_id: int, *, db: Db, tofa: TofaClient, settings: Settings, dry_run: bool = False, transport=None) -> RunResult`
  - status values: `"ok"`, `"preview"` (dry_run), `"failed"`, `"aborted"` (empty source).
  - Non-dry-runs persist a run row via `db.add_run`. Never raises; failures become `status="failed"`.

- [ ] **Step 1: Failing tests** (fake Tofa object keeps tests fast)

```python
from pathlib import Path
from app.config import Settings
from app.db import Db
from app.sync import run_definition
from app.tofa.client import TofaError

class FakeTofa:
    def __init__(self, library: dict, coll_items=None, exists=True):
        self.library, self.items, self.exists = library, set(coll_items or []), exists
        self.created = 0; self.log = []
    def resolve_tmdb(self, items): return {k: self.library[k] for k in items if k in self.library}
    def create_collection(self, name, overview): self.created += 1; self.exists = True; return "cid2"
    def collection_item_ids(self, cid): return set(self.items) if self.exists else None
    def update_collection(self, cid, **kw): self.log.append(("patch", kw))
    def add_item(self, cid, mid): self.items.add(mid); self.log.append(("add", mid))
    def remove_item(self, cid, mid): self.items.discard(mid); self.log.append(("rm", mid))

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
    assert r.status == "aborted" and t.items == {"a", "b"}

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
```

- [ ] **Step 2: Run** FAIL. **Step 3: Implement `app/sync.py`**

```python
from dataclasses import dataclass, field
from app.config import Settings
from app.db import Db
from app.diff import compute_diff
from app.models import SourceItem, dedupe
from app.sources import get_source
from app.sources.base import SourceError
from app.tofa.client import TofaError

@dataclass
class RunResult:
    status: str
    message: str = ""
    added: int = 0
    removed: int = 0
    missing: list[SourceItem] = field(default_factory=list)
    add_ids: list[str] = field(default_factory=list)
    remove_ids: list[str] = field(default_factory=list)

def _record(db: Db, def_id: int, r: RunResult, dry_run: bool) -> RunResult:
    if not dry_run:
        db.add_run(def_id, r.status, r.message, r.added, r.removed,
                   [{"tmdb_id": m.tmdb_id, "media_type": m.media_type, "title": m.title} for m in r.missing])
    return r

def run_definition(def_id: int, *, db: Db, tofa, settings: Settings,
                   dry_run: bool = False, transport=None) -> RunResult:
    d = db.get_definition(def_id)
    if d is None:
        return RunResult("failed", "Definition not found")
    try:
        items = dedupe(get_source(d["source_type"], d["source_config"], settings, transport).fetch())
        if not items:
            return _record(db, def_id, RunResult("aborted", "Source returned no items; collection left untouched"), dry_run)
        resolved = tofa.resolve_tmdb([i.key for i in items])
        cid = d["tofa_id"]
        current = tofa.collection_item_ids(cid) if cid else None
        diff = compute_diff(items, resolved, current or set(), d["prune"])
        if dry_run:
            return RunResult("preview", "", len(diff.add), len(diff.remove), diff.missing, diff.add, diff.remove)
        if current is None:  # never created, or deleted in Tofa
            cid = tofa.create_collection(d["name"], d["overview"])
            db.set_tofa_id(def_id, cid)
        for mid in diff.add:
            tofa.add_item(cid, mid)
        for mid in diff.remove:
            tofa.remove_item(cid, mid)
        msg = f"{len(diff.add)} added, {len(diff.remove)} removed, {len(diff.missing)} not in library"
        return _record(db, def_id, RunResult("ok", msg, len(diff.add), len(diff.remove), diff.missing,
                                             diff.add, diff.remove), dry_run)
    except (SourceError, TofaError) as e:
        return _record(db, def_id, RunResult("failed", str(e)), dry_run)
    except Exception as e:  # keep the scheduler alive
        return _record(db, def_id, RunResult("failed", f"Unexpected error: {e}"), dry_run)
```

- [ ] **Step 4: Run** `pytest tests/test_sync.py -v` — PASS. **Step 5: Commit** `feat: sync engine`.

---

### Task 7: Scheduler + Web UI

**Files:**
- Create: `app/scheduler.py`, `app/web/__init__.py`, `app/web/main.py`, `app/web/templates/base.html`, `index.html`, `edit.html`, `preview.html`, `tests/test_web.py`

**Interfaces:**
- Consumes: everything above.
- Produces:
  - `Scheduler(db, make_tofa, settings)` with `.start()`, `.reschedule(def_id)`, `.remove(def_id)`, `.run_now(def_id) -> RunResult`; wraps APScheduler `BackgroundScheduler`, one interval job `def-{id}` per enabled definition, `coalesce=True, max_instances=1`.
  - `create_app(settings, db, tofa_factory, scheduler=None) -> FastAPI` routes:
    - `GET /` dashboard (list, last run status, Tofa connection state via `system_info`, missing counts)
    - `GET /new`, `POST /new` (validates source via `get_source`, rejects with 400 + message)
    - `GET /{id}/edit`, `POST /{id}/edit`
    - `GET /{id}/preview` (dry run, shows add/remove/missing titles)
    - `POST /{id}/run`
    - `POST /{id}/delete` (requires form field `confirm=yes`; also deletes the Tofa collection only if `delete_in_tofa=yes`)
    - `GET /health` -> `{"ok": true}`

- [ ] **Step 1: Failing tests** (`TestClient`, fake tofa factory from Task 6 pattern)

```python
from pathlib import Path
from fastapi.testclient import TestClient
from app.config import Settings
from app.db import Db
from app.web.main import create_app

S = Settings("http://x", "k", None, None, Path("."))

class T:
    def system_info(self): return {"version": "0.10"}
    def resolve_tmdb(self, items): return {}
    def collection_item_ids(self, cid): return set()
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
```

- [ ] **Step 2: Run** FAIL. **Step 3: Implement.**

`app/scheduler.py`:

```python
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
```

`app/web/main.py` — key points (write the full module):
- `_cfg_from_form(form) -> dict`: for `manual` -> `{"text": manual_text}`; `tmdb_collection`/`tmdb_list` -> `{"id": int(tmdb_id)}`; `tmdb_discover` -> `{"media_type", "params": parsed from "key=value" lines in `discover_params`, "max_pages"}`; `trakt_list` -> `{"user","slug"}`.
- On save, call `get_source(type, cfg, settings)`; on `SourceError` re-render `edit.html` with status 400 and the message; on `ValueError` (bad int) likewise.
- Redirects use status 303. Preview route calls `run_definition(..., dry_run=True)` and renders `preview.html` listing `add_ids` count, `remove_ids` count, and each missing item as `tmdb_id — title`.
- `delete`: 400 unless `confirm == "yes"`; if `delete_in_tofa == "yes"` and `tofa_id`, call `tofa.delete_collection`; then `db.delete_definition` and `scheduler.remove` when a scheduler exists.
- Dashboard catches `TofaError` from `system_info()` and shows a red banner with the message instead of failing.
- Templates: plain server-rendered HTML with one shared minimal CSS block in `base.html` (dark/light via `prefers-color-scheme`); the edit form shows only the fields relevant to the selected `source_type` using a small inline `<script>` toggle. Escape all values with Jinja autoescape (default for `.html`).
- Module bottom: `app = create_app(...)` is NOT created at import; add `app/main.py`:

```python
from app.config import Settings
from app.db import Db
from app.scheduler import Scheduler
from app.tofa.client import TofaClient
from app.web.main import create_app

settings = Settings.from_env()
db = Db(settings.config_dir / "tofa-collections.db")
make_tofa = lambda: TofaClient(settings.tofa_url, settings.tofa_api_key)
scheduler = Scheduler(db, make_tofa, settings)
scheduler.start()
app = create_app(settings, db, make_tofa, scheduler)
```

- [ ] **Step 4: Run** `pytest -v` (whole suite) — PASS.
- [ ] **Step 5: Run the app manually** `TOFA_URL=... TOFA_API_KEY=... CONFIG_DIR=./.cfg .venv/Scripts/uvicorn app.main:app --port 8080`, open http://localhost:8080, create a manual definition, preview, run.
- [ ] **Step 6: Commit** `feat: scheduler and web ui`.

---

### Task 8: Docker, CI, Unraid template, MCU starter

**Files:**
- Create: `Dockerfile`, `docker-compose.yml`, `.dockerignore`, `.github/workflows/docker.yml`, `unraid/tofa-collection-creator.xml`, `README.md`, `app/seeds.py`, `tests/test_seeds.py`

**Interfaces:**
- Consumes: `Db`.
- Produces: `seed_defaults(db) -> None` inserting the "MCU Timeline" manual definition once if the definitions table is empty; called from `app/main.py` after `Db(...)`.

- [ ] **Step 1: Failing test**

```python
from app.db import Db
from app.seeds import seed_defaults

def test_seed_once(tmp_path):
    db = Db(tmp_path / "t.db")
    seed_defaults(db); seed_defaults(db)
    ds = db.list_definitions()
    assert len(ds) == 1 and ds[0]["name"] == "MCU Timeline" and ds[0]["enabled"] is False
```

- [ ] **Step 2: Run** FAIL. **Step 3: Implement `app/seeds.py`** — chronological MCU movie list as `manual` source, inserted with `enabled=False` so nothing writes until the user previews and enables it. TMDB ids below must be checked in the preview screen (titles are shown from Tofa/TMDB) before enabling.

```python
MCU_TIMELINE = """\
1771   # Captain America: The First Avenger
299537 # Captain Marvel
1726   # Iron Man
10138  # Iron Man 2
1724   # The Incredible Hulk
10195  # Thor
24428  # The Avengers
68721  # Iron Man 3
76338  # Thor: The Dark World
100402 # Captain America: The Winter Soldier
118340 # Guardians of the Galaxy
99861  # Avengers: Age of Ultron
102899 # Ant-Man
271110 # Captain America: Civil War
284052 # Doctor Strange
283995 # Guardians of the Galaxy Vol. 2
315635 # Spider-Man: Homecoming
284053 # Thor: Ragnarok
284054 # Black Panther
299536 # Avengers: Infinity War
363088 # Ant-Man and the Wasp
299534 # Avengers: Endgame
"""

def seed_defaults(db) -> None:
    if db.list_definitions():
        return
    i = db.create_definition("MCU Timeline", "Marvel Cinematic Universe in chronological order",
                             "manual", {"text": MCU_TIMELINE}, 1440)
    db.update_definition(i, enabled=False)
```
Add `seed_defaults(db)` call in `app/main.py`. Run test — PASS.

- [ ] **Step 4: `Dockerfile`**

```dockerfile
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 CONFIG_DIR=/config
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir $(grep -v -E '^(pytest|respx)' requirements.txt)
COPY app ./app
VOLUME /config
EXPOSE 8080
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8080/health')"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
```
`docker-compose.yml`: service `tofa-collection-creator`, image `ghcr.io/ferdinand99/tofa-collection-creator:latest`, `build: .`, ports `8080:8080`, volume `./config:/config`, env from `.env` (`TOFA_URL`, `TOFA_API_KEY`, `TMDB_API_KEY`, `TRAKT_CLIENT_ID`, `TZ`). `.dockerignore`: `.venv`, `.git`, `tests`, `docs`, `.cfg`.

- [ ] **Step 5: `.github/workflows/docker.yml`** — on push to `main` and tags: checkout, `docker/login-action` to ghcr.io with `GITHUB_TOKEN` (`permissions: packages: write`), `docker/build-push-action` with tags `ghcr.io/ferdinand99/tofa-collection-creator:latest` (and `:<tag>` on tag pushes), a preceding job running `pip install -r requirements.txt && pytest`.

- [ ] **Step 6: `unraid/tofa-collection-creator.xml`** — before writing, read the real `sylo.xml` (`curl https://raw.githubusercontent.com/Ferdinand99/unraid-templates/main/templates/sylo.xml`) and copy its element order, `<TemplateURL>` pattern (pointing at `.../templates/tofa-collection-creator.xml`), `<Support>`, `<Icon>`, `<Category>` (`MediaServer:Other Tools:`) conventions. Required entries: `<Repository>ghcr.io/ferdinand99/tofa-collection-creator:latest</Repository>`, `<Network>bridge</Network>`, Config entries: WebUI port 8080 (Port, host default 8080), `/config` Path default `/mnt/user/appdata/tofa-collection-creator`, Variables `TOFA_URL` (required, description "e.g. http://192.168.1.10:33333 - direct address, not the relay"), `TOFA_API_KEY` (required, Mask=true, "Admin API key from Tofa Server > Settings > API keys"), `TMDB_API_KEY` (optional, Mask=true), `TRAKT_CLIENT_ID` (optional), `TZ` (default `Europe/Oslo`). `<WebUI>http://[IP]:[PORT:8080]/</WebUI>`.

- [ ] **Step 7: `README.md`** — what it is, env table, first-run flow (preview before enabling), limitations (no ordering API, only items present in the Tofa library are added, direct connection required), how to add the template repo.

- [ ] **Step 8: Verify the image** `docker build -t tofa-cc . && docker run --rm -p 8080:8080 -e TOFA_URL=http://host.docker.internal:33333 -e TOFA_API_KEY=x -v %cd%/.cfg:/config tofa-cc` then `curl http://localhost:8080/health` -> `{"ok":true}`.

- [ ] **Step 9: Commit** `feat: docker, ci, unraid template, mcu starter`. Copy the XML into the `unraid-templates` repo `templates/` and open a PR/commit there **only after the user approves** (outward-facing).

---

### Task 9: End-to-end check against the real Tofa server

**Files:** none (verification only; findings go into `README.md` limitations if behaviour differs).

- [ ] **Step 1:** Run the container locally against the user's Tofa server. Enable the seeded "MCU Timeline" only after reviewing `/ID/preview` (all ids resolve to the right titles; missing ones are listed).
- [ ] **Step 2:** Click "Run now". Confirm in the Tofa UI that the collection exists with expected items.
- [ ] **Step 3:** Run again: result must be `0 added, 0 removed`.
- [ ] **Step 4:** Remove one item from the definition text and run: item is removed from the collection. Delete the collection in Tofa by hand and run: it is recreated and `tofa_id` updated.
- [ ] **Step 5:** Stop Tofa (or use a wrong key): run shows `failed` with a readable message and the scheduler survives.
- [ ] **Step 6:** Install via Unraid template, restart container, confirm `/config` data persists.
- [ ] **Step 7:** Note whether Tofa orders collection items by add order, release date or name; document in README.

---

## Self-review notes

- Spec coverage: client (T2), sources manual/tmdb/trakt/discover-rules (T4), sync+diff+missing handling (T3, T6), scheduler + UI + preview + confirm-delete (T7), Docker/CI/Unraid template/MCU starter (T8), verification (T9). Rules-based "actor/genre" collections are covered by `tmdb_discover`; rules against the Tofa library itself (not TMDB) are intentionally out of scope (YAGNI, no documented filter endpoint beyond `GET /media`).
- Task 1 Step 6 gates all later tasks: the item-endpoint shapes are not in the public spec.
