from app.models import SourceItem, DiffResult

def compute_diff(items: list[SourceItem], resolved: dict[tuple[int, str], str],
                 current: set[str], prune: bool) -> DiffResult:
    d = DiffResult()
    wanted: set[str] = set()
    for it in items:
        mid = resolved.get(it.key)
        if mid is None:
            d.missing.append(it); continue
        wanted.add(mid)
        if mid in current:
            d.unchanged += 1
        else:
            d.add.append(mid)
    if prune:
        d.remove = sorted(current - wanted)
    return d
