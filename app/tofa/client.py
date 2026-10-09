import time
import httpx

class TofaError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status

class TofaClient:
    BATCH = 200
    RETRIES = 3

    def __init__(self, base_url: str, api_key: str, *, transport=None):
        self._http = httpx.Client(
            base_url=base_url.rstrip("/") + "/api/v1",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30, transport=transport)

    def _req(self, method: str, path: str, **kw) -> httpx.Response:
        last: Exception | None = None
        for attempt in range(self.RETRIES):
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
            time.sleep(2 ** attempt)
        raise last  # type: ignore[misc]

    def _ok(self, r: httpx.Response) -> httpx.Response:
        if r.status_code >= 400:
            raise TofaError(f"Tofa returned {r.status_code}: {r.text[:200]}", r.status_code)
        return r

    def system_info(self) -> dict:
        return self._ok(self._req("GET", "/system/info")).json()

    def resolve_tmdb(self, items: list[tuple[int, str]]) -> dict[tuple[int, str], str]:
        out: dict[tuple[int, str], str] = {}
        for i in range(0, len(items), self.BATCH):
            chunk = items[i:i + self.BATCH]
            body = {"items": [{"tmdb_id": t, "media_type": m} for t, m in chunk]}
            data = self._ok(self._req("POST", "/media/by-tmdb/batch", json=body)).json()
            for res in data["results"]:
                if res.get("media_id") and res.get("files"):
                    out[(res["tmdb_id"], res["media_type"])] = res["media_id"]
        return out

    def create_collection(self, name: str, overview: str | None) -> str:
        r = self._ok(self._req("POST", "/collections/custom", json={"name": name, "overview": overview}))
        return r.json()["id"]

    def get_collection(self, cid: str) -> dict | None:
        r = self._req("GET", f"/collections/custom/{cid}")
        if r.status_code == 404:
            return None
        return self._ok(r).json()

    def collection_item_ids(self, cid: str) -> set[str] | None:
        c = self.get_collection(cid)
        return None if c is None else {i["id"] for i in c["items"]}

    def update_collection(self, cid: str, *, name: str | None = None, overview: str | None = None) -> None:
        body = {k: v for k, v in (("name", name), ("overview", overview)) if v is not None}
        self._ok(self._req("PATCH", f"/collections/custom/{cid}", json=body))

    def add_item(self, cid: str, media_id: str) -> None:
        self._ok(self._req("PUT", f"/collections/custom/{cid}/items/{media_id}"))

    def remove_item(self, cid: str, media_id: str) -> None:
        self._ok(self._req("DELETE", f"/collections/custom/{cid}/items/{media_id}"))

    def delete_collection(self, cid: str) -> None:
        self._ok(self._req("DELETE", f"/collections/custom/{cid}"))
