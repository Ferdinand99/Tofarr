from app.config import Settings
from app.sources.base import SourceError
from app.sources.imdb import ImdbListSource
from app.sources.manual import ManualSource
from app.sources.tmdb import TmdbCollectionSource, TmdbListSource, TmdbDiscoverSource
from app.sources.trakt import TraktListSource

SOURCE_TYPES = ["manual", "tmdb_collection", "tmdb_list", "tmdb_discover", "trakt_list", "imdb_list"]
_TMDB = {"tmdb_collection": TmdbCollectionSource, "tmdb_list": TmdbListSource, "tmdb_discover": TmdbDiscoverSource}

def get_source(type: str, cfg: dict, settings: Settings, transport=None):
    if type == "manual":
        return ManualSource(cfg)
    if type in _TMDB:
        if not settings.tmdb_api_key:
            raise SourceError("TMDB_API_KEY is not set (free key at themoviedb.org/settings/api)")
        return _TMDB[type](settings.tmdb_api_key, cfg, transport)
    if type == "imdb_list":
        if not settings.tmdb_api_key:
            raise SourceError("TMDB_API_KEY is not set (needed to map IMDb titles to TMDB ids)")
        return ImdbListSource(settings.tmdb_api_key, cfg, transport)
    if type == "trakt_list":
        if not settings.trakt_client_id:
            raise SourceError("TRAKT_CLIENT_ID is not set (create an app at trakt.tv/oauth/applications)")
        return TraktListSource(settings.trakt_client_id, cfg, transport)
    raise SourceError(f"Unknown source type: {type}")
