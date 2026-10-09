import time
import httpx

class TofaError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status

class TofaClient:
    BATCH = 200

    def __init__(self, base_url: str, api_key: str, *, transport=None, retries: int = 3, timeout: float = 30):
        self.retries = retries
        self._http = httpx.Client(
            base_url=base_url.rstrip("/") + "/api/v1",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout, transport=transport)

    def _req(self, method: str, path: str, **kw) -> httpx.Response:
        last: Exception | None = None
        for attempt in range(self.retries):
            try:
                r = self._http.request(method, path, **kw)
            except httpx.TransportError as e:
                last = TofaError(f"Cannot reach Tofa server: {e}")
            else:
                if r.status_code in (401, 403):
                    raise TofaError("Tofa rejected the API key (401/403). Create an admin API key "
                                    "under Server > Settings > API keys.", r.status_code)
                if r.status_code == 429 or r.status_code >= 500:
                    last = TofaError(f"Tofa returned {r.status_code}", r.status_code)
                else:
                    return r
            if attempt < self.retries - 1:
                time.sleep(2 ** attempt)
        raise last  # type: ignore[misc]

    def _ok(self, r: httpx.Response) -> httpx.Response:
        if r.status_code >= 400:
            raise TofaError(f"Tofa returned {r.status_code}: {r.text[:200]}", r.status_code)
        return r

    def system_info(self) -> dict:
        return self._ok(self._req("GET", "/system/info")).json()

    def resolve_tmdb(self, items: list[tuple[int, str]]) -> dict[tuple[int, str], str]:
        """Map (tmdb_id, "movie"|"tv") to Tofa media ids; items not in the library are omitted.

        Movies go through /media/by-tmdb/batch. That endpoint only matches files, so a whole
        series never resolves there; series are looked up in the library listing instead.
        """
        out: dict[tuple[int, str], str] = {}
        movies = [t for t, m in items if m != "tv"]
        for i in range(0, len(movies), self.BATCH):
            body = {"items": [{"tmdb_id": t, "media_type": "movie"} for t in movies[i:i + self.BATCH]]}
            data = self._ok(self._req("POST", "/media/by-tmdb/batch", json=body)).json()
            for res in data["results"]:
                if res.get("media_id") and res.get("files"):
                    out[(res["tmdb_id"], "movie")] = res["media_id"]
        wanted = {t for t, m in items if m == "tv"}
        if wanted:
            for tmdb_id, media_id in self._library_series().items():
                if tmdb_id in wanted:
                    out[(tmdb_id, "tv")] = media_id
        return out

    def _library_series(self) -> dict[int, str]:
        series: dict[int, str] = {}
        page = 1
        while True:
            r = self._ok(self._req("GET", "/media", params={"media_type": "tv", "per_page": 100, "page": page}))
            data = r.json()
            for it in data["items"]:
                if it.get("tmdb_id") and it.get("available", True):
                    series[it["tmdb_id"]] = it["id"]
            if page >= data.get("total_pages", 1):
                return series
            page += 1

    def discovery_shelves(self) -> list[dict]:
        data = self._ok(self._req("GET", "/discovery/page")).json()
        return [{"key": sh["key"], "title": sh.get("title", sh["key"]), "subtitle": sh.get("subtitle", ""),
                 "kind": sh.get("kind", ""), "count": len(sh.get("items", [])), "missing": sh.get("missing_count", 0)}
                for sh in data.get("shelves", [])]

    def discovery_shelf(self, key: str) -> list[dict]:
        return self._ok(self._req("GET", f"/discovery/shelf/{key}")).json().get("items", [])

    def fetch_image(self, path: str) -> tuple[bytes, str]:
        """Download a library image (a relative path such as images/posters/x.jpg) with an image token."""
        token = self._ok(self._req("GET", "/auth/image-token")).json()["token"]
        base = str(self._http.base_url).rsplit("/api/v1", 1)[0]
        r = self._ok(self._http.get(f"{base}/{path.lstrip('/')}", params={"st": token}))
        return r.content, r.headers.get("content-type", "image/jpeg")

    def create_collection(self, name: str, overview: str | None) -> str:
        r = self._ok(self._req("POST", "/collections/custom", json={"name": name, "overview": overview}))
        return r.json()["id"]

    def get_collection(self, cid: str) -> dict | None:
        r = self._req("GET", f"/collections/custom/{cid}")
        if r.status_code == 404:
            return None
        return self._ok(r).json()

    def collection_item_order(self, cid: str) -> list[str] | None:
        """Media ids in the order Tofa shows them (the order they were added), or None if gone."""
        c = self.get_collection(cid)
        return None if c is None else [i["id"] for i in c["items"]]

    def update_collection(self, cid: str, *, name: str | None = None, overview: str | None = None) -> None:
        body = {k: v for k, v in (("name", name), ("overview", overview)) if v is not None}
        self._ok(self._req("PATCH", f"/collections/custom/{cid}", json=body))

    def add_item(self, cid: str, media_id: str) -> None:
        self._ok(self._req("PUT", f"/collections/custom/{cid}/items/{media_id}"))

    def remove_item(self, cid: str, media_id: str) -> None:
        self._ok(self._req("DELETE", f"/collections/custom/{cid}/items/{media_id}"))

    def delete_collection(self, cid: str) -> None:
        self._ok(self._req("DELETE", f"/collections/custom/{cid}"))
