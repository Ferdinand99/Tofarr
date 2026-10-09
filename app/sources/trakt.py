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
                out.append(SourceItem(int(tmdb), "movie" if kind == "movie" else "tv", obj.get("title", ""), obj.get("year")))
        return out


CHART_PATHS = {f"{k}/{c}" for k in ("movies", "shows") for c in ("trending", "popular", "anticipated", "watched", "played")} | {"movies/boxoffice"}


def _headers(client_id: str) -> dict:
    return {"trakt-api-key": client_id, "trakt-api-version": "2", "Content-Type": "application/json"}


class TraktChartSource:
    """Trakt movie/show charts. Trakt answers either with bare items or with {movie|show: item} wrappers."""

    def __init__(self, client_id: str, cfg: dict, transport=None):
        self.path = cfg.get("path", "")
        if self.path not in CHART_PATHS:
            raise SourceError(f"Unknown Trakt chart: {self.path}")
        self.kind = "movie" if self.path.startswith("movies") else "tv"
        self.http = httpx.Client(base_url="https://api.trakt.tv", timeout=30, transport=transport,
                                 headers=_headers(client_id))

    def fetch(self) -> list[SourceItem]:
        r = self.http.get("/" + self.path, params={"limit": 40})
        if r.status_code >= 400:
            raise SourceError(f"Trakt returned {r.status_code}")
        out = []
        for row in r.json():
            obj = row.get("movie") or row.get("show") or row
            tmdb = (obj.get("ids") or {}).get("tmdb")
            if tmdb:
                out.append(SourceItem(int(tmdb), self.kind, obj.get("title", ""), obj.get("year")))
        return out


def trending_lists(client_id: str, kind: str = "popular", transport=None) -> list[dict]:
    """Public Trakt lists people follow, as {name, user, slug, count, likes}."""
    if kind not in ("trending", "popular"):
        raise SourceError(f"Unknown Trakt list kind: {kind}")
    http = httpx.Client(base_url="https://api.trakt.tv", timeout=30, transport=transport, headers=_headers(client_id))
    try:
        r = http.get(f"/lists/{kind}", params={"limit": 30})
    except httpx.TransportError as e:
        raise SourceError(f"Cannot reach Trakt: {e}")
    if r.status_code >= 400:
        raise SourceError(f"Trakt returned {r.status_code}")
    out = []
    for row in r.json():
        lst = row.get("list") or {}
        user = ((lst.get("user") or {}).get("ids") or {}).get("slug")
        slug = (lst.get("ids") or {}).get("slug")
        if user and slug:
            out.append({"name": lst.get("name", slug), "user": user, "slug": slug,
                        "count": lst.get("item_count", 0), "likes": row.get("like_count", 0)})
    return out
