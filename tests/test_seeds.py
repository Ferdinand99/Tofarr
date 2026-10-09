import httpx, respx
from app.db import Db
from app.seeds import seed_defaults, MCU_TIMELINE, MCU_TIMELINE_V1, MCU_TIMELINE_V2
from app.sources.manual import ManualSource
from app.tofa.client import TofaClient


def test_seed_once(tmp_path):
    db = Db(tmp_path / "t.db")
    seed_defaults(db); seed_defaults(db)
    ds = db.list_definitions()
    assert len(ds) == 1 and ds[0]["name"] == "MCU Timeline" and ds[0]["enabled"] is False


def test_full_timeline_has_movies_and_series_without_duplicates():
    items = ManualSource({"text": MCU_TIMELINE}).fetch()
    kinds = {i.media_type for i in items}
    assert kinds == {"movie", "tv"}
    assert len({i.key for i in items}) == len(items)
    tv = {i.tmdb_id for i in items if i.media_type == "tv"}
    assert {84958, 85271, 88396, 91363} <= tv          # Loki, WandaVision, Falcon&WS, What If
    assert items[0].tmdb_id == 1771                    # starts with Captain America: TFA
    assert len(items) >= 50


def test_old_untouched_seed_is_upgraded_but_edited_one_is_not(tmp_path):
    db = Db(tmp_path / "t.db")
    i = db.create_definition("MCU Timeline", "d", "manual", {"text": MCU_TIMELINE_V1}, 1440)
    seed_defaults(db)
    assert db.get_definition(i)["source_config"]["text"] == MCU_TIMELINE
    db.update_definition(i, source_config={"text": "1726"})
    seed_defaults(db)
    assert db.get_definition(i)["source_config"]["text"] == "1726"


@respx.mock
def test_series_are_resolved_from_the_library_listing_not_by_tmdb_batch():
    batch = respx.post("http://t/api/v1/media/by-tmdb/batch").mock(return_value=httpx.Response(200, json={"results": [
        {"tmdb_id": 1726, "media_type": "movie", "media_id": "im", "files": [{}]}]}))
    def listing(request):
        page = int(request.url.params["page"])
        rows = {1: [{"id": "loki", "tmdb_id": 84958, "media_type": "tv", "available": True},
                    {"id": "gone", "tmdb_id": 1, "media_type": "tv", "available": False}],
                2: [{"id": "wanda", "tmdb_id": 85271, "media_type": "tv", "available": True}]}[page]
        return httpx.Response(200, json={"items": rows, "page": page, "per_page": 2, "total": 3, "total_pages": 2})
    respx.get("http://t/api/v1/media").mock(side_effect=listing)
    out = TofaClient("http://t", "k").resolve_tmdb(
        [(84958, "tv"), (85271, "tv"), (1, "tv"), (999, "tv"), (1726, "movie")])
    assert out == {(84958, "tv"): "loki", (85271, "tv"): "wanda", (1726, "movie"): "im"}
    import json
    assert [i["media_type"] for i in json.loads(batch.calls[0].request.content)["items"]] == ["movie"]


@respx.mock
def test_no_series_requested_means_no_library_listing_call():
    respx.post("http://t/api/v1/media/by-tmdb/batch").mock(return_value=httpx.Response(200, json={"results": []}))
    listing = respx.get("http://t/api/v1/media").mock(return_value=httpx.Response(500))
    TofaClient("http://t", "k").resolve_tmdb([(1726, "movie")])
    assert listing.call_count == 0


def test_existing_mcu_definition_gets_an_in_universe_copy_once(tmp_path):
    db = Db(tmp_path / "t.db")
    db.create_definition("MCU Timeline", "", "tmdb_list", {"id": 84979}, 1440)   # user's own, release order
    seed_defaults(db)
    names = sorted(d["name"] for d in db.list_definitions())
    assert names == ["MCU Timeline", "MCU Timeline (in-universe order)"]
    copy = next(d for d in db.list_definitions() if d["name"].endswith("(in-universe order)"))
    assert copy["enabled"] is False and copy["source_config"]["text"] == MCU_TIMELINE
    db.delete_definition(copy["id"])
    seed_defaults(db)
    assert [d["name"] for d in db.list_definitions()] == ["MCU Timeline"]


def test_complete_timeline_includes_one_shots_and_netflix_series_in_place():
    items = ManualSource({"text": MCU_TIMELINE}).fetch()
    ids = [i.tmdb_id for i in items]
    assert len(set(ids)) == len(ids) and len(items) >= 66
    assert {61889, 38472, 62126, 62127, 62285, 67178} <= {i.tmdb_id for i in items if i.media_type == "tv"}
    assert {211387, 76122, 76535, 119569, 253980} <= set(ids)
    assert ids.index(1771) < ids.index(211387) < ids.index(1726) < ids.index(76122) < ids.index(24428)
    assert ids.index(299534) < ids.index(91363)       # Endgame before the Disney+ series


def test_untouched_second_generation_seed_is_upgraded_to_the_complete_one(tmp_path):
    db = Db(tmp_path / "t.db")
    i = db.create_definition("MCU Timeline (in-universe order)", "", "manual", {"text": MCU_TIMELINE_V2}, 1440)
    seed_defaults(db)
    assert db.get_definition(i)["source_config"]["text"] == MCU_TIMELINE
