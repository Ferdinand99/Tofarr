from pathlib import Path
from fastapi.testclient import TestClient
from app.config import Settings
from app.db import Db
from app.seerr import SeerrError
from app.wanted import collect_wanted
from app.web.main import create_app


def seed(db):
    a = db.create_definition("Marvel", "", "manual", {"text": "1"}, 60)
    b = db.create_definition("Best of 2020", "", "manual", {"text": "1"}, 60)
    c = db.create_definition("Never run", "", "manual", {"text": "1"}, 60)
    m = lambda i, t, ty="movie": {"tmdb_id": i, "media_type": ty, "title": t}
    db.add_run(a, "ok", "", 0, 0, [m(1, "Zeta"), m(2, "Alpha", "tv")])
    db.add_run(a, "ok", "", 0, 0, [m(1, "Zeta"), m(2, "Alpha", "tv"), m(3, "Old news")])   # the latest run wins
    db.add_run(a, "ok", "", 0, 0, [m(1, "Zeta"), m(2, "Alpha", "tv")])
    db.add_run(b, "ok", "", 0, 0, [m(1, "Zeta"), m(4, "Beta")])
    return a, b, c


def test_missing_titles_are_gathered_across_collections(tmp_path):
    db = Db(tmp_path / "t.db"); seed(db)
    got = collect_wanted(db)
    assert [(w.tmdb_id, w.media_type, w.title, w.collections) for w in got] == [
        (1, "movie", "Zeta", ["Best of 2020", "Marvel"]),      # wanted by two collections comes first
        (2, "tv", "Alpha", ["Marvel"]),
        (4, "movie", "Beta", ["Best of 2020"])]


def test_only_the_last_run_counts_and_never_run_collections_add_nothing(tmp_path):
    db = Db(tmp_path / "t.db"); a, b, c = seed(db)
    db.add_run(a, "ok", "", 0, 0, [])                          # everything arrived in the library
    assert [w.tmdb_id for w in collect_wanted(db)] == [4, 1]    # equal weight: by title, Beta before Zeta


def test_the_same_id_as_movie_and_series_stays_two_entries(tmp_path):
    db = Db(tmp_path / "t.db")
    a = db.create_definition("A", "", "manual", {"text": "1"}, 60)
    db.add_run(a, "ok", "", 0, 0, [{"tmdb_id": 7, "media_type": "movie", "title": "M"},
                                   {"tmdb_id": 7, "media_type": "tv", "title": "S"}])
    assert sorted((w.media_type, w.title) for w in collect_wanted(db)) == [("movie", "M"), ("tv", "S")]


# ---------- web ----------

class FakeSeerr:
    def __init__(self): self.requested = []; self.fail = set(); self.status = {}
    def request(self, tmdb_id, media_type):
        if tmdb_id in self.fail:
            raise SeerrError("Seerr says no")
        self.requested.append((tmdb_id, media_type)); return "requested"
    def statuses(self, items): return self.status


def app_for(tmp_path, seerr_on=True):
    db = Db(tmp_path / "t.db"); seed(db)
    fake = FakeSeerr()
    s = Settings("http://tofa:33333", "k", None, None, Path("."), "http://seerr:5055" if seerr_on else None,
                 "key" if seerr_on else None)
    return TestClient(create_app(s, db, lambda: None, seerr_factory=lambda: fake)), fake


def test_wanted_page_lists_titles_with_the_collections_that_want_them(tmp_path):
    c, fake = app_for(tmp_path)
    page = c.get("/wanted").text
    assert "Zeta" in page and "Alpha" in page and "Beta" in page and "Old news" not in page
    assert "Marvel" in page and "Best of 2020" in page
    assert page.count("data-request data-id") == 3 and "Request all 3" in page
    fake.status = {(1, "movie"): "Downloading"}
    page = c.get("/wanted").text
    assert page.count("data-request data-id") == 2 and "Downloading" in page


def test_wanted_page_works_without_seerr_but_has_no_request_buttons(tmp_path):
    c, _ = app_for(tmp_path, seerr_on=False)
    page = c.get("/wanted").text
    assert "Zeta" in page and "data-request data-id" not in page and "Request all" not in page
    assert "Seerr" in page                                   # points to Settings


def test_wanted_page_has_a_friendly_empty_state(tmp_path):
    db = Db(tmp_path / "t.db")
    c = TestClient(create_app(Settings("", "", None, None, Path(".")), db, lambda: None))
    assert "Nothing is missing" in c.get("/wanted").text


def test_request_all_needs_confirmation_reports_each_title_and_is_capped(tmp_path):
    c, fake = app_for(tmp_path)
    assert c.post("/wanted/request-all", data={}).status_code == 400 and fake.requested == []
    fake.fail.add(4)
    r = c.post("/wanted/request-all", data={"confirm": "yes"})
    assert sorted(fake.requested) == [(1, "movie"), (2, "tv")]
    assert "Requested 2 of 3" in r.text and "Seerr says no" in r.text and "Beta" in r.text


def test_request_all_without_seerr_is_refused(tmp_path):
    c, fake = app_for(tmp_path, seerr_on=False)
    assert c.post("/wanted/request-all", data={"confirm": "yes"}).status_code == 400 and fake.requested == []
