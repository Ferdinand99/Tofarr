from dataclasses import dataclass, field

@dataclass(frozen=True)
class SourceItem:
    tmdb_id: int
    media_type: str = "movie"
    title: str = ""
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
    add: list[str] = field(default_factory=list)
    remove: list[str] = field(default_factory=list)
    missing: list[SourceItem] = field(default_factory=list)
    unchanged: int = 0
