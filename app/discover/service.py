import time
from dataclasses import dataclass, field, replace
from app.models import SourceItem
from app.sources import get_source
from app.sources.base import SourceError

# one argument per source type is enough to describe any shelf in a URL
CFG_KEY = {"tofa_shelf": "key", "trakt_chart": "path", "tmdb_chart": "path", "imdb_chart": "chart",
           "imdb_list": "list", "tmdb_collection": "id", "tmdb_list": "id"}
DISCOVERABLE = set(CFG_KEY) | {"trakt_list"}
TTL = 600


@dataclass(frozen=True)
class Shelf:
    type: str
    arg: str
    title: str
    subtitle: str = ""


@dataclass
class ShelfView:
    items: list[SourceItem] = field(default_factory=list)
    have: int = 0
    total: int = 0


CATALOG: dict[str, list[Shelf]] = {
    "trakt": [
        Shelf("trakt_chart", "movies/trending", "Trending movies", "Most watched right now"),
        Shelf("trakt_chart", "movies/popular", "Popular movies", "Most popular overall"),
        Shelf("trakt_chart", "movies/anticipated", "Anticipated movies", "Most awaited"),
        Shelf("trakt_chart", "movies/boxoffice", "Box office", "This weekend's top earners"),
        Shelf("trakt_chart", "shows/trending", "Trending shows", "Most watched right now"),
        Shelf("trakt_chart", "shows/popular", "Popular shows", "Most popular overall"),
        Shelf("trakt_chart", "shows/anticipated", "Anticipated shows", "Most awaited")],
    "tmdb": [
        Shelf("tmdb_chart", "trending/movie/week", "Trending movies", "This week"),
        Shelf("tmdb_chart", "trending/tv/week", "Trending shows", "This week"),
        Shelf("tmdb_chart", "movie/popular", "Popular movies"),
        Shelf("tmdb_chart", "tv/popular", "Popular shows"),
        Shelf("tmdb_chart", "movie/top_rated", "Top rated movies"),
        Shelf("tmdb_chart", "tv/top_rated", "Top rated shows"),
        Shelf("tmdb_chart", "movie/upcoming", "Upcoming movies"),
        Shelf("tmdb_chart", "movie/now_playing", "In cinemas now")],
    "imdb": [
        Shelf("imdb_chart", "TOP_RATED_MOVIES", "Top rated movies", "Highest rated on IMDb"),
        Shelf("imdb_chart", "TOP_RATED_TV_SHOWS", "Top rated shows", "Highest rated on IMDb"),
        Shelf("imdb_chart", "MOST_POPULAR_MOVIES", "Most popular movies", "What people look up now"),
        Shelf("imdb_chart", "MOST_POPULAR_TV_SHOWS", "Most popular shows", "What people look up now")],
}
TITLES = {(s.type, s.arg): s.title for shelves in CATALOG.values() for s in shelves}

_cache: dict[tuple, tuple[float, ShelfView]] = {}


def clear_cache() -> None:
    _cache.clear()


def cfg_for(type: str, arg: str) -> dict:
    if type == "trakt_list":
        user, _, slug = arg.partition("/")
        return {"user": user, "slug": slug}
    if type not in CFG_KEY:
        raise SourceError(f"Unknown source type: {type}")
    if type in ("tmdb_collection", "tmdb_list"):
        try:
            return {CFG_KEY[type]: int(arg)}
        except ValueError:
            raise SourceError(f"{arg!r} is not a TMDB id")
    return {CFG_KEY[type]: arg}


def load_shelf(type: str, arg: str, settings, tofa) -> ShelfView:
    """Fetch a shelf and mark which titles exist in the Tofa library. Cached for ten minutes."""
    ck = (type, arg, settings.tofa_url)
    hit = _cache.get(ck)
    if hit and time.time() - hit[0] < TTL:
        return hit[1]
    items = get_source(type, cfg_for(type, arg), settings).fetch()
    if any(i.in_library is None for i in items):
        have = tofa.resolve_tmdb([i.key for i in items])
        items = [replace(i, in_library=i.key in have) for i in items]
    view = ShelfView(items, sum(1 for i in items if i.in_library), len(items))
    _cache[ck] = (time.time(), view)
    return view
