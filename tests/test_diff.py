from app.models import SourceItem, dedupe
from app.diff import compute_diff

def S(i, m="movie"): return SourceItem(i, m, f"t{i}")

def R(*pairs):
    return {(i, "movie"): mid for i, mid in pairs}


def test_dedupe_keeps_first_order():
    assert [s.tmdb_id for s in dedupe([S(2), S(1), S(2)])] == [2, 1]


def test_empty_collection_gets_everything_in_source_order():
    d = compute_diff([S(3), S(1), S(2), S(9)], R((1, "a"), (2, "b"), (3, "c")), current=[], prune=True)
    assert d.add == ["c", "a", "b"] and d.remove == [] and d.new == ["c", "a", "b"] and d.moved == []
    assert [m.tmdb_id for m in d.missing] == [9]


def test_new_items_at_the_end_are_just_appended():
    d = compute_diff([S(1), S(2), S(3)], R((1, "a"), (2, "b"), (3, "c")), current=["a", "b"], prune=True)
    assert d.add == ["c"] and d.remove == [] and d.moved == [] and d.unchanged == 2


def test_item_inserted_in_the_middle_re_adds_everything_after_it():
    d = compute_diff([S(1), S(4), S(2), S(3)], R((1, "a"), (2, "b"), (3, "c"), (4, "x")),
                     current=["a", "b", "c"], prune=True)
    assert d.add == ["x", "b", "c"] and d.remove == ["b", "c"]
    assert d.new == ["x"] and d.moved == ["b", "c"] and d.unchanged == 1


def test_wrong_order_is_rebuilt():
    d = compute_diff([S(1), S(2)], R((1, "a"), (2, "b")), current=["b", "a"], prune=True)
    assert d.add == ["a", "b"] and sorted(d.remove) == ["a", "b"] and d.new == []


def test_identical_order_does_nothing():
    d = compute_diff([S(1), S(2)], R((1, "a"), (2, "b")), current=["a", "b"], prune=True)
    assert d.add == [] and d.remove == [] and d.unchanged == 2


def test_prune_removes_items_not_in_source_and_keeps_the_rest_in_place():
    d = compute_diff([S(1), S(2)], R((1, "a"), (2, "b")), current=["z", "a", "b"], prune=True)
    assert d.remove == ["z"] and d.add == [] and d.unchanged == 2


def test_prune_false_never_removes_extras():
    d = compute_diff([S(1)], R((1, "a")), current=["z", "a"], prune=False)
    assert d.remove == [] and d.add == []
