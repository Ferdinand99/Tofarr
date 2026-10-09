from app.models import SourceItem, DiffResult

def compute_diff(items: list[SourceItem], resolved: dict[tuple[int, str], str],
                 current: list[str], prune: bool) -> DiffResult:
    """Plan the changes that make the collection hold the source items in source order.

    Tofa orders a collection by when items were added, and adding an item that is already
    there does not move it. So the longest correctly ordered prefix stays as it is, and
    everything after it is removed and added again in source order.
    """
    d = DiffResult()
    desired: list[str] = []
    for it in items:
        mid = resolved.get(it.key)
        if mid is None:
            d.missing.append(it)
        elif mid not in desired:
            desired.append(mid)
    wanted = set(desired)
    present = set(current)
    ordered_now = [m for m in current if m in wanted]
    keep = 0
    while keep < len(ordered_now) and keep < len(desired) and ordered_now[keep] == desired[keep]:
        keep += 1
    d.unchanged = keep
    d.moved = [m for m in desired[keep:] if m in present]
    d.new = [m for m in desired[keep:] if m not in present]
    d.add = desired[keep:]
    d.remove = list(ordered_now[keep:])
    if prune:
        d.remove += sorted(present - wanted)
    return d
