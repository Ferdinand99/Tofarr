import httpx, respx
from app.db import Db
from app.seeds import seed_defaults, MCU_TIMELINE, MCU_TIMELINE_V1
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
def test_resolve_matches_series_even_if_tofa_echoes_another_media_type():
    respx.post("http://t/api/v1/media/by-tmdb/batch").mock(return_value=httpx.Response(200, json={"results": [
        {"tmdb_id": 84958, "media_type": "other", "media_id": "loki", "files": [{}]},
        {"tmdb_id": 1726, "media_type": "movie", "media_id": "im", "files": [{}]}]}))
    out = TofaClient("http://t", "k").resolve_tmdb([(84958, "tv"), (1726, "movie")])
    assert out == {(84958, "tv"): "loki", (1726, "movie"): "im"}
