from app.models import SourceItem
from app.sources.base import SourceError
from app.tofa.client import TofaClient, TofaError


class TofaShelfSource:
    """A Tofa discovery shelf (trending, popular, decades, genres, ...). No TMDB key needed."""

    def __init__(self, settings, cfg: dict, transport=None):
        if not settings.tofa_url:
            raise SourceError("Tofa is not configured yet (Settings)")
        self.key = cfg.get("key", "")
        self.tofa = TofaClient(settings.tofa_url, settings.tofa_api_key, transport=transport, retries=2)

    def fetch(self) -> list[SourceItem]:
        try:
            rows = self.tofa.discovery_shelf(self.key)
        except TofaError as e:
            raise SourceError(f"Tofa shelf {self.key}: {e}")
        out = []
        for r in rows:
            poster = r.get("poster_path") or ""
            out.append(SourceItem(r["tmdb_id"], "tv" if r.get("type") == "tv" else "movie", r.get("title", ""),
                                  r.get("year"), poster, bool(r.get("in_library"))))
        return out
