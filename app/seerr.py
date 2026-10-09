from concurrent.futures import ThreadPoolExecutor
import httpx

# mediaInfo.status in Seerr: 1 unknown, 2 pending, 3 processing, 4 partially available, 5 available
STATUS_LABELS = {2: "Requested", 3: "Downloading", 4: "Partly available", 5: "Available"}


class SeerrError(Exception):
    def __init__(self, message: str, fatal: bool = False):
        super().__init__(message)
        self.fatal = fatal  # true when retrying other titles cannot help (wrong key, server down)


class SeerrClient:
    """Minimal Seerr (Overseerr / Jellyseerr compatible) client: request a title and read its status."""

    def __init__(self, url: str, api_key: str, *, transport=None):
        self._http = httpx.Client(base_url=url.rstrip("/") + "/api/v1", headers={"X-Api-Key": api_key},
                                  timeout=20, transport=transport)

    def _send(self, method: str, path: str, **kw) -> httpx.Response:
        try:
            r = self._http.request(method, path, **kw)
        except httpx.TransportError as e:
            raise SeerrError(f"Cannot reach Seerr: {e}", fatal=True)
        if r.status_code == 401:
            raise SeerrError("Seerr rejected the API key (401). Copy it from Seerr, Settings, General.", fatal=True)
        return r

    def request(self, tmdb_id: int, media_type: str) -> str:
        """Ask Seerr to get a title. Returns "requested", or "exists" when it was already requested."""
        body = {"mediaType": media_type, "mediaId": tmdb_id}
        if media_type == "tv":
            body["seasons"] = "all"
        r = self._send("POST", "/request", json=body)
        if r.status_code in (200, 201, 202):
            return "requested"
        if r.status_code == 409:
            return "exists"
        if r.status_code == 404:
            raise SeerrError("Seerr could not find this title")
        try:
            detail = r.json().get("message", "")
        except Exception:
            detail = ""
        if r.status_code == 403:
            raise SeerrError(f"Seerr refused the request: {detail or 'your user lacks the request permission'}")
        raise SeerrError(f"Seerr returned {r.status_code}{': ' + detail if detail else ''}")

    def _status(self, item: tuple[int, str]):
        tmdb_id, media_type = item
        try:
            r = self._http.get(f"/{'tv' if media_type == 'tv' else 'movie'}/{tmdb_id}")
            if r.status_code != 200:
                return item, None
            return item, STATUS_LABELS.get(((r.json().get("mediaInfo") or {}).get("status")))
        except Exception:
            return item, None

    def statuses(self, items: list[tuple[int, str]]) -> dict[tuple[int, str], str]:
        """Seerr's view of titles that are not in the library, such as Requested or Downloading."""
        if not items:
            return {}
        with ThreadPoolExecutor(max_workers=8) as pool:
            return {k: label for k, label in pool.map(self._status, items) if label}

    def test(self) -> str:
        v = self._send("GET", "/status")
        if v.status_code >= 400:
            raise SeerrError(f"Seerr returned {v.status_code} for /status. Is the URL right?", fatal=True)
        me = self._send("GET", "/auth/me")
        if me.status_code >= 400:
            raise SeerrError(f"Seerr returned {me.status_code} for the API key", fatal=True)
        who = me.json()
        name = who.get("displayName") or who.get("username") or who.get("email") or "unknown user"
        return f"Seerr {v.json().get('version', '?')}, signed in as {name}"
