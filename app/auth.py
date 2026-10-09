"""Login for the web UI: salted scrypt password hash, signed session cookie, and a small login rate limit."""
import base64
import hashlib
import hmac
import ipaddress
import secrets
import time

N, R, P = 2 ** 14, 8, 1
COOKIE = "tofarr_session"
SESSION_SECONDS = 30 * 24 * 3600
PROXY_HEADERS = ("x-forwarded-for", "forwarded", "x-real-ip", "cf-connecting-ip", "true-client-ip")


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=N, r=R, p=P, dklen=32)
    return f"scrypt${N}${R}${P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        kind, n, r, p, salt, digest = stored.split("$")
        if kind != "scrypt":
            return False
        want = _unb64(digest)
        got = hashlib.scrypt(password.encode(), salt=_unb64(salt), n=int(n), r=int(r), p=int(p), dklen=len(want))
        return hmac.compare_digest(got, want)
    except Exception:
        return False


def is_local_request(request) -> bool:
    """True for a direct connection from a private or loopback address.

    A request that carries proxy headers has been forwarded, so its apparent address is the proxy's
    and says nothing about the real client. Those never count as local.
    """
    if any(h in request.headers for h in PROXY_HEADERS):
        return False
    try:
        ip = ipaddress.ip_address(getattr(request.client, "host", "") or "")
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback


class AuthStore:
    """The one account, kept in the settings table (the password only as a hash)."""

    def __init__(self, db):
        self.db = db

    def has_user(self) -> bool:
        return bool(self.db.get_setting("auth_user") and self.db.get_setting("auth_hash"))

    def username(self) -> str | None:
        return self.db.get_setting("auth_user")

    def create_user(self, username: str, password: str) -> None:
        self.db.set_setting("auth_user", username)
        self.set_password(password)

    def set_password(self, password: str) -> None:
        self.db.set_setting("auth_hash", hash_password(password))
        self.db.set_setting("auth_secret", secrets.token_hex(32))   # a new secret ends every older session

    def check(self, username: str, password: str) -> bool:
        stored = self.db.get_setting("auth_hash") or hash_password("no such user")
        password_ok = verify_password(password, stored)              # always hashed, so timing does not reveal the name
        name_ok = hmac.compare_digest((self.username() or "").encode(), username.encode())
        return password_ok and name_ok and self.has_user()

    def clear(self) -> None:
        for key in ("auth_user", "auth_hash", "auth_secret", "auth_local_bypass"):
            self.db.set_setting(key, "")

    def local_bypass(self) -> bool:
        return bool(self.db.get_setting("auth_local_bypass"))

    def set_local_bypass(self, on: bool) -> None:
        self.db.set_setting("auth_local_bypass", "1" if on else "")

    def _sign(self, body: str) -> str:
        secret = (self.db.get_setting("auth_secret") or "").encode()
        return _b64(hmac.new(secret, body.encode(), hashlib.sha256).digest())

    def make_token(self, username: str, lifetime: int = SESSION_SECONDS) -> str:
        body = f"{_b64(username.encode())}.{int(time.time()) + lifetime}"
        return f"{body}.{self._sign(body)}"

    def read_token(self, token: str) -> str | None:
        try:
            name_b64, expires, sig = token.split(".")
            if not self.has_user() or not hmac.compare_digest(sig, self._sign(f"{name_b64}.{expires}")):
                return None
            if int(expires) < time.time():
                return None
            name = _unb64(name_b64).decode()
            return name if name == self.username() else None
        except Exception:
            return None


class LoginLimiter:
    """Locks a client address out of the login form after repeated failures."""

    def __init__(self, max_failures: int = 5, window: int = 900):
        self.max, self.window, self._fails = max_failures, window, {}

    def _recent(self, key: str) -> list[float]:
        now = time.time()
        self._fails[key] = [t for t in self._fails.get(key, []) if now - t < self.window]
        return self._fails[key]

    def blocked(self, key: str) -> bool:
        return len(self._recent(key)) >= self.max

    def fail(self, key: str) -> None:
        self._recent(key).append(time.time())

    def reset(self, key: str) -> None:
        self._fails.pop(key, None)
