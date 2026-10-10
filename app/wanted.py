from dataclasses import dataclass, field


@dataclass
class WantedItem:
    tmdb_id: int
    media_type: str
    title: str
    collections: list[str] = field(default_factory=list)


def collect_wanted(db) -> list[WantedItem]:
    """Every title that the last run of some collection could not find in the Tofa library.

    The same title wanted by several collections is listed once. Titles wanted by the most collections come first.
    """
    found: dict[tuple[int, str], WantedItem] = {}
    for d in db.list_definitions():
        run = db.last_run(d["id"])
        for m in (run or {}).get("missing", []):
            key = (m["tmdb_id"], m.get("media_type", "movie"))
            item = found.setdefault(key, WantedItem(key[0], key[1], m.get("title") or ""))
            if not item.title and m.get("title"):
                item.title = m["title"]
            if d["name"] not in item.collections:
                item.collections.append(d["name"])
    return sorted(found.values(), key=lambda w: (-len(w.collections), w.title.lower(), w.tmdb_id))
