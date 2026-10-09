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
