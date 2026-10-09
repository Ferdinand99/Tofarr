import httpx, respx, pytest
from app.tofa.client import TofaClient, TofaError

BASE = "http://t:33333"

@respx.mock
def test_resolve_chunks_by_200_and_omits_missing():
    calls = []
    def handler(request):
        import json
        items = json.loads(request.content)["items"]
        calls.append(len(items))
        return httpx.Response(200, json={"results": [
            {"tmdb_id": i["tmdb_id"], "media_type": "movie", "media_id": f"m{i['tmdb_id']}", "files": [{}]}
            for i in items if i["tmdb_id"] != 5]})
    respx.post(f"{BASE}/api/v1/media/by-tmdb/batch").mock(side_effect=handler)
    c = TofaClient(BASE, "k")
    out = c.resolve_tmdb([(i, "movie") for i in range(1, 202)])
    assert calls == [200, 1]
    assert (5, "movie") not in out and out[(6, "movie")] == "m6"

@respx.mock
def test_results_without_files_count_as_missing():
    respx.post(f"{BASE}/api/v1/media/by-tmdb/batch").mock(return_value=httpx.Response(
        200, json={"results": [{"tmdb_id": 1, "media_type": "movie", "media_id": "m1", "files": []}]}))
    assert TofaClient(BASE, "k").resolve_tmdb([(1, "movie")]) == {}

@respx.mock
def test_get_collection_404_returns_none():
    respx.get(f"{BASE}/api/v1/collections/custom/x").mock(return_value=httpx.Response(404))
    assert TofaClient(BASE, "k").get_collection("x") is None

@respx.mock
def test_401_raises_readable_error():
    respx.get(f"{BASE}/api/v1/system/info").mock(return_value=httpx.Response(401))
    with pytest.raises(TofaError) as e:
        TofaClient(BASE, "bad").system_info()
    assert "API key" in str(e.value)

@respx.mock
def test_retries_on_429_then_succeeds(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    route = respx.get(f"{BASE}/api/v1/system/info").mock(side_effect=[
        httpx.Response(429), httpx.Response(200, json={"version": "1"})])
    assert TofaClient(BASE, "k").system_info()["version"] == "1"
    assert route.call_count == 2
