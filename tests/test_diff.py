from app.models import SourceItem, dedupe
from app.diff import compute_diff

def S(i, m="movie"): return SourceItem(i, m, f"t{i}")

def test_dedupe_keeps_first_order():
    assert [s.tmdb_id for s in dedupe([S(2), S(1), S(2)])] == [2, 1]

def test_diff_add_remove_missing_in_source_order():
    items = [S(3), S(1), S(2), S(9)]
    resolved = {(1, "movie"): "a", (2, "movie"): "b", (3, "movie"): "c"}
    d = compute_diff(items, resolved, current={"b", "z"}, prune=True)
    assert d.add == ["c", "a"]          # source order
    assert d.remove == ["z"]
    assert [m.tmdb_id for m in d.missing] == [9]
    assert d.unchanged == 1

def test_prune_false_never_removes():
    d = compute_diff([S(1)], {(1, "movie"): "a"}, current={"z"}, prune=False)
    assert d.remove == [] and d.add == ["a"]
