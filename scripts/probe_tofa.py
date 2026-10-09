"""Usage: TOFA_URL=... TOFA_API_KEY=... python scripts/probe_tofa.py <tmdb_id>"""
import os, sys, json, httpx

base, key = os.environ["TOFA_URL"].rstrip("/"), os.environ["TOFA_API_KEY"]
h = {"Authorization": f"Bearer {key}"}
c = httpx.Client(base_url=base + "/api/v1", headers=h, timeout=20)

def show(label, r):
    print(f"--- {label}: {r.status_code}\n{r.text[:800]}")

show("system/info", c.get("/system/info"))
tmdb = int(sys.argv[1])
look = c.post("/media/by-tmdb/batch", json={"items": [{"tmdb_id": tmdb, "media_type": "movie"}]})
show("by-tmdb", look)
media_id = look.json()["results"][0]["media_id"]
created = c.post("/collections/custom", json={"name": "probe-delete-me", "overview": "x"})
show("create", created)
cid = created.json()["id"]
show("PUT item", c.put(f"/collections/custom/{cid}/items/{media_id}"))
show("GET coll", c.get(f"/collections/custom/{cid}"))
show("PATCH", c.patch(f"/collections/custom/{cid}", json={"overview": "y"}))
show("DELETE item", c.delete(f"/collections/custom/{cid}/items/{media_id}"))
show("DELETE coll", c.delete(f"/collections/custom/{cid}"))
