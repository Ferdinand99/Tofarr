import re
import httpx
from app.models import SourceItem
from app.sources.base import SourceError

GRAPHQL = "https://api.graphql.imdb.com/"
TMDB = "https://api.themoviedb.org/3"
QUERY = """query($id: ID!, $after: ID) { list(id: $id) { items(first: 250, after: $after) {
  pageInfo { hasNextPage endCursor }
  edges { node { item { ... on Title { id titleText { text } titleType { id } } } } } } } }"""
TV_TYPES = {"tvSeries", "tvMiniSeries"}
_ID = re.compile(r"(ls\d+)")
_FOUND: dict[str, tuple[int, str, str] | None] = {}  # tt id -> TMDB match; stable, so kept for the process


def list_id(ref: str) -> str:
    m = _ID.search(ref or "")
    if not m or ("/" in ref and "imdb.com" not in ref):
        raise SourceError("Enter an IMDb list URL or id, for example https://www.imdb.com/list/ls029032797/")
    return m.group(1)


class ImdbListSource:
    """Reads a public IMDb list through IMDb's unofficial GraphQL endpoint and maps titles to TMDB ids.

    IMDb has no public API for lists, so this can stop working without notice. IMDb allows this data
    for personal, non-commercial use only.
    """

    def __init__(self, tmdb_key: str, cfg: dict, transport=None):
        self.list_id = list_id(cfg.get("list", ""))
        self.http = httpx.Client(timeout=30, transport=transport, headers={
            "User-Agent": "Mozilla/5.0 (compatible; tofa-collection-creator)", "Content-Type": "application/json",
            "x-imdb-client-name": "imdb-web-next-localized", "Origin": "https://www.imdb.com",
            "Referer": "https://www.imdb.com/"})
        self.tmdb_key = tmdb_key

    def _titles(self) -> list[tuple[str, str, str]]:
        out: list[tuple[str, str, str]] = []
        after = None
        while True:
            try:
                r = self.http.post(GRAPHQL, json={"query": QUERY, "variables": {"id": self.list_id, "after": after}})
            except httpx.TransportError as e:
                raise SourceError(f"Cannot reach IMDb: {e}")
            if r.status_code >= 400:
                try:
                    detail = "; ".join(e["message"] for e in r.json().get("errors", []))[:200]
                except Exception:
                    detail = ""
                raise SourceError(f"IMDb returned {r.status_code}"
                                  f"{': ' + detail if detail else ''}. Its unofficial list API may have changed.")
            lst = (r.json().get("data") or {}).get("list")
            if not lst:
                raise SourceError(f"IMDb list {self.list_id} was not found or is private.")
            items = lst["items"]
            for e in items["edges"]:
                t = (e.get("node") or {}).get("item") or {}
                if t.get("id"):
                    out.append((t["id"], (t.get("titleType") or {}).get("id", ""),
                                (t.get("titleText") or {}).get("text", "")))
            page = items.get("pageInfo") or {}
            if not page.get("hasNextPage"):
                return out
            after = page["endCursor"]

    def _tmdb(self, tt: str, imdb_type: str):
        if tt in _FOUND:
            return _FOUND[tt]
        r = self.http.get(f"{TMDB}/find/{tt}", params={"api_key": self.tmdb_key, "external_source": "imdb_id"})
        if r.status_code == 401:
            raise SourceError("TMDB rejected TMDB_API_KEY")
        if r.status_code >= 400:
            raise SourceError(f"TMDB returned {r.status_code} while looking up {tt}")
        data = r.json()
        order = ["tv", "movie"] if imdb_type in TV_TYPES else ["movie", "tv"]
        found = None
        for kind in order:
            hits = data.get(f"{kind}_results") or []
            if hits:
                found = (hits[0]["id"], kind, hits[0].get("title") or hits[0].get("name") or "")
                break
        _FOUND[tt] = found
        return found

    def fetch(self) -> list[SourceItem]:
        seen: set[str] = set()
        items: list[SourceItem] = []
        for tt, imdb_type, imdb_title in self._titles():
            if tt in seen:
                continue
            seen.add(tt)
            hit = self._tmdb(tt, imdb_type)
            if hit:
                items.append(SourceItem(hit[0], hit[1], imdb_title or hit[2]))
        return items
