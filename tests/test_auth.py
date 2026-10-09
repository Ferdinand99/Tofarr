import time
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from app.auth import AuthStore, hash_password, verify_password, is_local_request
from app.config import Settings
from app.db import Db
from app.web.main import create_app

S = Settings("", "", None, None, Path("."))


# ---------- password hashing ----------

def test_hash_is_salted_and_verifies():
    a, b = hash_password("correct horse"), hash_password("correct horse")
    assert a != b and "correct horse" not in a
    assert verify_password("correct horse", a) and not verify_password("wrong", a)


def test_malformed_hashes_never_verify():
    for bad in ("", "x", "scrypt$1$2", "plain", "scrypt$a$b$c$d$e"):
        assert verify_password("anything", bad) is False


# ---------- store and session tokens ----------

def test_store_creates_user_and_signs_sessions(tmp_path):
    a = AuthStore(Db(tmp_path / "t.db"))
    assert not a.has_user()
    a.create_user("ferdi", "supersecret1")
    assert a.has_user() and a.check("ferdi", "supersecret1") and not a.check("ferdi", "nope")
    assert not a.check("someone", "supersecret1")
    tok = a.make_token("ferdi")
    assert a.read_token(tok) == "ferdi"
    assert a.read_token(tok + "x") is None and a.read_token("") is None and a.read_token("a.b") is None


def test_expired_tokens_and_password_changes_end_sessions(tmp_path):
    a = AuthStore(Db(tmp_path / "t.db"))
    a.create_user("ferdi", "supersecret1")
    assert a.read_token(a.make_token("ferdi", lifetime=-1)) is None
    old = a.make_token("ferdi")
    a.set_password("newsecret22")
    assert a.read_token(old) is None and a.check("ferdi", "newsecret22")


def test_clear_removes_the_account(tmp_path):
    a = AuthStore(Db(tmp_path / "t.db"))
    a.create_user("ferdi", "supersecret1"); a.clear()
    assert not a.has_user()


def test_local_request_detection():
    class R:
        def __init__(self, host, headers=None):
            self.client = type("C", (), {"host": host})(); self.headers = headers or {}
    assert is_local_request(R("192.168.1.20")) and is_local_request(R("10.0.3.25")) and is_local_request(R("172.17.0.1"))
    assert is_local_request(R("127.0.0.1"))
    assert not is_local_request(R("8.8.8.8")) and not is_local_request(R("testclient")) and not is_local_request(R(""))
    for h in ("x-forwarded-for", "forwarded", "x-real-ip", "cf-connecting-ip"):   # came through a proxy
        assert not is_local_request(R("192.168.1.20", {h: "1.2.3.4"}))


# ---------- web ----------

def make(tmp_path, client_addr=None, **kw):
    db = Db(tmp_path / "t.db")
    app = create_app(S, db, lambda: type("T", (), {"system_info": lambda s: {"version": "1"}})(), require_auth=True)
    c = TestClient(app, client=client_addr) if client_addr else TestClient(app)
    return c, db


def setup_user(c, user="ferdi", pw="supersecret1"):
    return c.post("/setup", data={"username": user, "password": pw, "confirm": pw}, follow_redirects=False)


def test_first_start_forces_setup_but_health_stays_open(tmp_path):
    c, _ = make(tmp_path)
    r = c.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/setup"
    assert c.get("/health").json() == {"ok": True}
    assert c.get("/setup").status_code == 200
    assert c.post("/seerr/request", data={"tmdb_id": "1", "media_type": "movie"}).status_code == 401


def test_setup_creates_the_account_and_logs_in(tmp_path):
    c, db = make(tmp_path)
    r = setup_user(c)
    assert r.status_code == 303 and "tofarr_session" in r.headers["set-cookie"]
    assert c.get("/").status_code == 200
    assert "supersecret1" not in str(db.get_setting("auth_hash"))
    again = c.get("/setup", follow_redirects=False)
    assert again.status_code == 303                      # setup is gone once an account exists
    attempt = c.post("/setup", data={"username": "evil", "password": "hackedhacked", "confirm": "hackedhacked"},
                     follow_redirects=False)
    assert attempt.status_code == 303 and "set-cookie" not in attempt.headers
    assert AuthStore(db).check("ferdi", "supersecret1") and not AuthStore(db).check("evil", "hackedhacked")


@pytest.mark.parametrize("user,pw,confirm", [("fe", "supersecret1", "supersecret1"), ("ferdi", "short", "short"),
                                             ("ferdi", "supersecret1", "different11"), ("", "supersecret1", "supersecret1")])
def test_setup_validation(tmp_path, user, pw, confirm):
    c, db = make(tmp_path)
    r = c.post("/setup", data={"username": user, "password": pw, "confirm": confirm})
    assert r.status_code == 400 and not AuthStore(db).has_user()


def test_logged_out_visitors_are_sent_to_the_login_page(tmp_path):
    c, db = make(tmp_path); AuthStore(db).create_user("ferdi", "supersecret1")
    r = c.get("/discover?source=tmdb", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login?next=")
    assert c.post("/seerr/request", data={}).status_code == 401
    assert c.get("/discover/img", params={"path": "artwork/x/poster"}).status_code == 401
    assert c.get("/health").status_code == 200
    assert c.post("/1/delete", data={"confirm": "yes"}, follow_redirects=False).status_code in (303, 401)


def test_login_logout_and_safe_redirect(tmp_path):
    c, db = make(tmp_path); AuthStore(db).create_user("ferdi", "supersecret1")
    bad = c.post("/login", data={"username": "ferdi", "password": "wrong", "next": "/"})
    assert bad.status_code == 401 and "set-cookie" not in bad.headers and "wrong" not in bad.text
    ok = c.post("/login", data={"username": "ferdi", "password": "supersecret1", "next": "/settings"}, follow_redirects=False)
    assert ok.status_code == 303 and ok.headers["location"] == "/settings"
    cookie = ok.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "secure" not in cookie
    assert c.get("/settings").status_code == 200
    for evil in ("//evil.example", "https://evil.example", "javascript:alert(1)"):
        r = c.post("/login", data={"username": "ferdi", "password": "supersecret1", "next": evil}, follow_redirects=False)
        assert r.headers["location"] == "/"
    c.post("/logout")
    assert c.get("/", follow_redirects=False).status_code == 303


def test_cookie_is_secure_behind_https(tmp_path):
    c, db = make(tmp_path); AuthStore(db).create_user("ferdi", "supersecret1")
    r = c.post("/login", data={"username": "ferdi", "password": "supersecret1"},
               headers={"X-Forwarded-Proto": "https"}, follow_redirects=False)
    assert "secure" in r.headers["set-cookie"].lower()


def test_repeated_wrong_passwords_lock_the_login_for_a_while(tmp_path):
    c, db = make(tmp_path); AuthStore(db).create_user("ferdi", "supersecret1")
    for _ in range(5):
        assert c.post("/login", data={"username": "ferdi", "password": "nope"}).status_code == 401
    locked = c.post("/login", data={"username": "ferdi", "password": "supersecret1"})
    assert locked.status_code == 429


def test_change_password_needs_the_current_one_and_ends_other_sessions(tmp_path):
    c, db = make(tmp_path); setup_user(c)
    old_cookie = c.cookies.get("tofarr_session")
    assert c.post("/settings/password", data={"current": "wrong", "password": "brandnew123", "confirm": "brandnew123"}).status_code == 400
    assert c.post("/settings/password", data={"current": "supersecret1", "password": "short", "confirm": "short"}).status_code == 400
    ok = c.post("/settings/password", data={"current": "supersecret1", "password": "brandnew123", "confirm": "brandnew123"})
    assert ok.status_code == 200 and "Password changed" in ok.text
    assert c.get("/", follow_redirects=False).status_code == 200            # this browser stays signed in
    other = TestClient(c.app); other.cookies.set("tofarr_session", old_cookie)
    assert other.get("/", follow_redirects=False).status_code == 303        # an older session does not
    assert AuthStore(db).check("ferdi", "brandnew123") and not AuthStore(db).check("ferdi", "supersecret1")


def test_local_network_bypass_is_off_by_default_and_never_through_a_proxy(tmp_path):
    c, db = make(tmp_path, ("192.168.1.20", 50000)); AuthStore(db).create_user("ferdi", "supersecret1")
    assert c.get("/", follow_redirects=False).status_code == 303                    # off by default
    AuthStore(db).set_local_bypass(True)
    assert c.get("/", follow_redirects=False).status_code == 200                    # private address
    assert c.get("/", headers={"X-Forwarded-For": "8.8.8.8"}, follow_redirects=False).status_code == 303
    outside = TestClient(c.app, client=("8.8.8.8", 50000))
    assert outside.get("/", follow_redirects=False).status_code == 303


def test_local_bypass_never_skips_the_first_time_setup(tmp_path):
    c, db = make(tmp_path, ("192.168.1.20", 50000)); AuthStore(db).set_local_bypass(True)
    assert c.get("/", follow_redirects=False).headers["location"] == "/setup"


def test_security_toggle_in_settings(tmp_path):
    c, db = make(tmp_path); setup_user(c)
    assert "Skip login on the local network" in c.get("/settings").text
    c.post("/settings/security", data={"local_bypass": "on"})
    assert AuthStore(db).local_bypass() is True
    c.post("/settings/security", data={})
    assert AuthStore(db).local_bypass() is False
