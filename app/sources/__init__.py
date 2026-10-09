from app.config import Settings
from app.sources.base import SourceError
from app.sources.imdb import ImdbChartSource, ImdbListSource
from app.sources.manual import ManualSource
from app.sources.tmdb import TmdbChartSource, TmdbCollectionSource, TmdbListSource, TmdbDiscoverSource
from app.sources.tofa_shelf import TofaShelfSource
from app.sources.trakt import TraktChartSource, TraktListSource

SOURCE_TYPES = ["manual", "tmdb_collection", "tmdb_list", "tmdb_discover", "trakt_list", "imdb_list",
                "tofa_shelf", "trakt_chart", "tmdb_chart", "imdb_chart"]
_TMDB = {"tmdb_collection": TmdbCollectionSource, "tmdb_list": TmdbListSource, "tmdb_discover": TmdbDiscoverSource,
         "tmdb_chart": TmdbChartSource}

def get_source(type: str, cfg: dict, settings: Settings, transport=None):
    if type == "manual":
        return ManualSource(cfg)
    if type in _TMDB:
        if not settings.tmdb_api_key:
            raise SourceError("TMDB_API_KEY is not set (free key at themoviedb.org/settings/api)")
        return _TMDB[type](settings.tmdb_api_key, cfg, transport)
    if type in ("imdb_list", "imdb_chart"):
        if not settings.tmdb_api_key:
            raise SourceError("TMDB_API_KEY is not set (needed to map IMDb titles to TMDB ids)")
        return (ImdbListSource if type == "imdb_list" else ImdbChartSource)(settings.tmdb_api_key, cfg, transport)
    if type == "tofa_shelf":
        return TofaShelfSource(settings, cfg, transport)
    if type in ("trakt_list", "trakt_chart"):
        if not settings.trakt_client_id:
            raise SourceError("TRAKT_CLIENT_ID is not set (create an app at trakt.tv/oauth/applications)")
        return (TraktListSource if type == "trakt_list" else TraktChartSource)(settings.trakt_client_id, cfg, transport)
    raise SourceError(f"Unknown source type: {type}")
