from dataclasses import dataclass, field
from app.config import Settings
from app.db import Db
from app.diff import compute_diff
from app.models import SourceItem, dedupe
from app.sources import get_source
from app.sources.base import SourceError
from app.tofa.client import TofaError

@dataclass
class RunResult:
    status: str
    message: str = ""
    added: int = 0
    removed: int = 0
    moved: int = 0
    missing: list[SourceItem] = field(default_factory=list)
    add_ids: list[str] = field(default_factory=list)
    remove_ids: list[str] = field(default_factory=list)
    add_items: list[SourceItem] = field(default_factory=list)

def _record(db: Db, def_id: int, r: RunResult, dry_run: bool) -> RunResult:
    if not dry_run:
        db.add_run(def_id, r.status, r.message, r.added, r.removed,
                   [{"tmdb_id": m.tmdb_id, "media_type": m.media_type, "title": m.title} for m in r.missing])
    return r

def run_definition(def_id: int, *, db: Db, tofa, settings: Settings,
                   dry_run: bool = False, transport=None) -> RunResult:
    d = db.get_definition(def_id)
    if d is None:
        return RunResult("failed", "Definition not found")
    try:
        items = dedupe(get_source(d["source_type"], d["source_config"], settings, transport).fetch())
        if not items:
            return _record(db, def_id, RunResult("aborted", "Source returned no items; collection left untouched"), dry_run)
        resolved = tofa.resolve_tmdb([i.key for i in items])
        if not resolved:
            return _record(db, def_id, RunResult(
                "aborted", f"None of the {len(items)} source items were found in the Tofa library; "
                           "collection left untouched", missing=items), dry_run)
        cid = d["tofa_id"]
        current = tofa.collection_item_order(cid) if cid else None
        diff = compute_diff(items, resolved, current or [], d["prune"])
        new_set = set(diff.new)
        add_items = [i for i in items if resolved.get(i.key) in new_set]
        pruned = len(diff.remove) - len(diff.moved)
        if dry_run:
            return RunResult("preview", added=len(diff.new), removed=pruned, moved=len(diff.moved),
                             missing=diff.missing, add_ids=diff.add, remove_ids=diff.remove,
                             add_items=add_items)
        if current is None:  # never created, or deleted in Tofa
            cid = tofa.create_collection(d["name"], d["overview"])
            db.set_tofa_id(def_id, cid)
        for mid in diff.remove:  # removals first: re-adding a moved item only works after it is gone
            tofa.remove_item(cid, mid)
        for mid in diff.add:
            tofa.add_item(cid, mid)
        msg = (f"{len(diff.new)} added, {pruned} removed, {len(diff.moved)} reordered, "
               f"{len(diff.missing)} not in library")
        return _record(db, def_id, RunResult("ok", msg, added=len(diff.new), removed=pruned,
                                             moved=len(diff.moved), missing=diff.missing,
                                             add_ids=diff.add, remove_ids=diff.remove), dry_run)
    except (SourceError, TofaError) as e:
        return _record(db, def_id, RunResult("failed", str(e)), dry_run)
    except Exception as e:  # keep the scheduler alive
        return _record(db, def_id, RunResult("failed", f"Unexpected error: {e}"), dry_run)
