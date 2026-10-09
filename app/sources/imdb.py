import re
import httpx
from app.models import SourceItem
from app.sources.base import SourceError

GRAPHQL = "https://api.graphql.imdb.com/"
TMDB = "https://api.themoviedb.org/3"
TITLE = "id titleText { text } releaseYear { year } titleType { id } primaryImage { url }"
LIST_QUERY = """query($id: ID!, $after: ID) { list(id: $id) { items(first: 250, after: $after) {
  pageInfo { hasNextPage endCursor }
  edges { node { item { ... on Title { %s } } } } } } }""" % TITLE
CHART_QUERY = "query { chartTitles(first: 100, chart: {chartType: %s}) { edges { node { " + TITLE + " } } } }"
CHARTS = {"TOP_RATED_MOVIES", "TOP_RATED_TV_SHOWS", "MOST_POPULAR_MOVIES", "MOST_POPULAR_TV_SHOWS"}
TV_TYPES = {"tvSeries", "tvMiniSeries"}
_ID = re.compile(r"(ls\d+)")
_FOUND: dict[str, tuple[int, str, str] | None] = {}  # tt id -> TMDB match; stable, so kept for the process


def list_id(ref: str) -> str:
    m = _ID.search(ref or "")
    if not m or ("/" in ref and "imdb.com" not in ref):
        raise SourceError("Enter an IMDb list URL or id, for example https://www.imdb.com/list/ls029032797/")
    return m.group(1)


class _Imdb:
    """Reads IMDb through its unofficial GraphQL endpoint and maps titles to TMDB ids.

    IMDb has no public API for this, so it can stop working without notice. IMDb allows this data
    for personal, non-commercial use only.
    """

    def __init__(self, tmdb_key: str, transport=None):
        self.tmdb_key = tmdb_key
        self.http = httpx.Client(timeout=30, transport=transport, headers={
            "User-Agent": "Mozilla/5.0 (compatible; tofa-collection-creator)", "Content-Type": "application/json",
            "x-imdb-client-name": "imdb-web-next-localized", "Origin": "https://www.imdb.com",
            "Referer": "https://www.imdb.com/"})

    def _post(self, payload: dict) -> dict:
        try:
            r = self.http.post(GRAPHQL, json=payload)
        except httpx.TransportError as e:
            raise SourceError(f"Cannot reach IMDb: {e}")
        if r.status_code >= 400:
            try:
                detail = "; ".join(e["message"] for e in r.json().get("errors", []))[:200]
            except Exception:
                detail = ""
            raise SourceError(f"IMDb returned {r.status_code}"
                              f"{': ' + detail if detail else ''}. Its unofficial API may have changed.")
        return r.json()

    @staticmethod
    def _row(t: dict) -> dict | None:
        if not t.get("id"):
            return None
        return {"tt": t["id"], "type": (t.get("titleType") or {}).get("id", ""),
                "title": (t.get("titleText") or {}).get("text", ""),
                "year": (t.get("releaseYear") or {}).get("year"),
                "poster": (t.get("primaryImage") or {}).get("url", "")}

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

    def _to_items(self, rows: list[dict]) -> list[SourceItem]:
        seen: set[str] = set()
        items: list[SourceItem] = []
        for row in rows:
            if row["tt"] in seen:
                continue
            seen.add(row["tt"])
            hit = self._tmdb(row["tt"], row["type"])
            if hit:
                items.append(SourceItem(hit[0], hit[1], row["title"] or hit[2], row["year"], row["poster"]))
        return items


class ImdbListSource(_Imdb):
    def __init__(self, tmdb_key: str, cfg: dict, transport=None):
        super().__init__(tmdb_key, transport)
        self.list_id = list_id(cfg.get("list", ""))

    def _rows(self) -> list[dict]:
        out: list[dict] = []
        after = None
        while True:
            body = self._post({"query": LIST_QUERY, "variables": {"id": self.list_id, "after": after}})
            lst = (body.get("data") or {}).get("list")
            if not lst:
                raise SourceError(f"IMDb list {self.list_id} was not found or is private.")
            for e in lst["items"]["edges"]:
                row = self._row((e.get("node") or {}).get("item") or {})
                if row:
                    out.append(row)
            page = lst["items"].get("pageInfo") or {}
            if not page.get("hasNextPage"):
                return out
            after = page["endCursor"]

    def fetch(self) -> list[SourceItem]:
        return self._to_items(self._rows())


class ImdbChartSource(_Imdb):
    def __init__(self, tmdb_key: str, cfg: dict, transport=None):
        super().__init__(tmdb_key, transport)
        self.chart = cfg.get("chart", "")
        if self.chart not in CHARTS:
            raise SourceError(f"Unknown IMDb chart: {self.chart}")

    def fetch(self) -> list[SourceItem]:
        body = self._post({"query": CHART_QUERY % self.chart})
        edges = (((body.get("data") or {}).get("chartTitles")) or {}).get("edges") or []
        rows = [r for r in (self._row(e.get("node") or {}) for e in edges) if r]
        return self._to_items(rows)
