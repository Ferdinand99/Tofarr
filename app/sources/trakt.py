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
