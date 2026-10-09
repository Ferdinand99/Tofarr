import httpx
from app.models import SourceItem
from app.sources.base import SourceError

API = "https://api.themoviedb.org/3"
IMG = "https://image.tmdb.org/t/p/w342"
CHART_PATHS = {"trending/movie/week", "trending/tv/week", "trending/movie/day", "trending/tv/day",
               "movie/popular", "movie/top_rated", "movie/upcoming", "movie/now_playing",
               "tv/popular", "tv/top_rated", "tv/on_the_air", "tv/airing_today"}


def _item(r: dict, kind: str) -> "SourceItem":
    date = r.get("release_date") or r.get("first_air_date") or ""
    return SourceItem(r["id"], kind, r.get("title") or r.get("name", ""),
                      int(date[:4]) if date[:4].isdigit() else None,
                      IMG + r["poster_path"] if r.get("poster_path") else "")


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
        return [_item(p, "movie") for p in parts]

class TmdbListSource(_Tmdb):
    def __init__(self, key, cfg, transport=None):
        super().__init__(key, transport); self.id = cfg["id"]

    def fetch(self) -> list[SourceItem]:
        out: list[SourceItem] = []
        page = 1
        while True:
            data = self.get(f"/list/{self.id}", page=page)
            out += [_item(i, i.get("media_type", "movie")) for i in data["items"]]
            if page >= data.get("total_pages", 1):
                return out
            page += 1

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
            out += [_item(r, self.mt) for r in data["results"]]
            if page >= data.get("total_pages", 1):
                break
        return out


class TmdbChartSource(_Tmdb):
    def __init__(self, key, cfg, transport=None):
        super().__init__(key, transport)
        self.path = cfg.get("path", "")
        if self.path not in CHART_PATHS:
            raise SourceError(f"Unknown TMDB chart: {self.path}")
        self.kind = "tv" if "tv" in self.path.split("/")[:2] else "movie"

    def fetch(self) -> list[SourceItem]:
        out: list[SourceItem] = []
        for page in (1, 2):
            data = self.get(f"/{self.path}", page=page)
            out += [_item(r, self.kind) for r in data["results"]]
            if page >= data.get("total_pages", 1):
                break
        return out


def search_collections(key: str, query: str, transport=None) -> list[dict]:
    data = _Tmdb(key, transport).get("/search/collection", query=query)
    return [{"id": r["id"], "name": r.get("name", ""),
             "poster": IMG + r["poster_path"] if r.get("poster_path") else ""} for r in data["results"]]
