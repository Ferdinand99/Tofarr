from dataclasses import dataclass, field

@dataclass(frozen=True)
class SourceItem:
    tmdb_id: int
    media_type: str = "movie"
    title: str = ""
    # display extras for Discover; they never take part in identity or comparison
    year: int | None = field(default=None, compare=False)
    poster: str = field(default="", compare=False)
    in_library: bool | None = field(default=None, compare=False)

    @property
    def key(self) -> tuple[int, str]:
        return (self.tmdb_id, self.media_type)

def dedupe(items: list[SourceItem]) -> list[SourceItem]:
    seen, out = set(), []
    for it in items:
        if it.key not in seen:
            seen.add(it.key); out.append(it)
    return out

@dataclass
class DiffResult:
    add: list[str] = field(default_factory=list)      # every PUT, in the order to run them
    remove: list[str] = field(default_factory=list)   # every DELETE (reorder + prune)
    new: list[str] = field(default_factory=list)      # added items that were not in the collection
    moved: list[str] = field(default_factory=list)    # present items removed and re-added to fix order
    missing: list[SourceItem] = field(default_factory=list)
    unchanged: int = 0
