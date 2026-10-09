from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from app.sources import SOURCE_TYPES, get_source
from app.sources.base import SourceError
from app.sync import run_definition
from app.tofa.client import TofaError

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

def _cfg_from_form(f: dict) -> dict:
    t = f["source_type"]
    if t == "manual":
        return {"text": f.get("manual_text", "")}
    if t in ("tmdb_collection", "tmdb_list"):
        return {"id": int(f.get("tmdb_id", ""))}
    if t == "tmdb_discover":
        params = {}
        for line in f.get("discover_params", "").splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                params[k.strip()] = v.strip()
        return {"media_type": f.get("discover_media_type", "movie"), "params": params,
                "max_pages": int(f.get("max_pages") or 3)}
    if t == "trakt_list":
        return {"user": f.get("trakt_user", "").strip(), "slug": f.get("trakt_slug", "").strip()}
    raise SourceError(f"Unknown source type: {t}")

def _values_from_def(d: dict) -> dict:
    c = d["source_config"]
    return {"name": d["name"], "overview": d["overview"] or "", "source_type": d["source_type"],
            "manual_text": c.get("text", ""), "tmdb_id": c.get("id", ""),
            "discover_media_type": c.get("media_type", "movie"),
            "discover_params": "\n".join(f"{k}={v}" for k, v in c.get("params", {}).items()),
            "max_pages": c.get("max_pages", 3), "trakt_user": c.get("user", ""),
            "trakt_slug": c.get("slug", ""), "interval_minutes": d["interval_minutes"],
            "prune": d["prune"], "enabled": d["enabled"]}

def create_app(settings, db, tofa_factory, scheduler=None) -> FastAPI:
    app = FastAPI(title="Tofa Collection Creator")

    def page(request, name, status=200, **ctx):
        return TEMPLATES.TemplateResponse(request, name, ctx, status_code=status)

    def form_page(request, values, error=None, status=200, def_id=None):
        return page(request, "edit.html", status, v=values, error=error, def_id=def_id, types=SOURCE_TYPES)

    async def parse(request):
        f = dict((await request.form()).items())
        values = {**f, "prune": "prune" in f, "enabled": "enabled" in f}
        try:
            cfg = _cfg_from_form(f)
            get_source(f["source_type"], cfg, settings)
            minutes = int(f.get("interval_minutes") or 1440)
            if minutes < 5 or not f.get("name", "").strip():
                raise ValueError("Name is required and interval must be at least 5 minutes")
        except (SourceError, ValueError, KeyError) as e:
            return values, None, str(e)
        return values, (cfg, minutes), None

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/")
    def index(request: Request):
        try:
            info, tofa_error = tofa_factory().system_info(), None
        except TofaError as e:
            info, tofa_error = None, str(e)
        rows = [{"d": d, "run": db.last_run(d["id"])} for d in db.list_definitions()]
        return page(request, "index.html", rows=rows, info=info, tofa_error=tofa_error)

    @app.get("/new")
    def new_form(request: Request):
        return form_page(request, {"source_type": "manual", "interval_minutes": 1440,
                                   "prune": True, "enabled": True, "discover_media_type": "movie",
                                   "max_pages": 3})

    @app.post("/new")
    async def new_save(request: Request):
        values, ok, err = await parse(request)
        if err:
            return form_page(request, values, err, 400)
        cfg, minutes = ok
        i = db.create_definition(values["name"].strip(), values.get("overview", ""), values["source_type"],
                                 cfg, minutes, values["prune"])
        db.update_definition(i, enabled=values["enabled"])
        if scheduler:
            scheduler.reschedule(i)
        return RedirectResponse("/", 303)

    @app.get("/{def_id}/edit")
    def edit_form(request: Request, def_id: int):
        d = db.get_definition(def_id)
        if not d:
            return RedirectResponse("/", 303)
        return form_page(request, _values_from_def(d), def_id=def_id)

    @app.post("/{def_id}/edit")
    async def edit_save(request: Request, def_id: int):
        d = db.get_definition(def_id)
        if not d:
            return RedirectResponse("/", 303)
        values, ok, err = await parse(request)
        if err:
            return form_page(request, values, err, 400, def_id)
        cfg, minutes = ok
        db.update_definition(def_id, name=values["name"].strip(), overview=values.get("overview", ""),
                             source_type=values["source_type"], source_config=cfg,
                             interval_minutes=minutes, prune=values["prune"], enabled=values["enabled"])
        if d["tofa_id"]:
            try:
                tofa_factory().update_collection(d["tofa_id"], name=values["name"].strip(),
                                                 overview=values.get("overview") or None)
            except TofaError:
                pass  # rename retried on next edit; content sync unaffected
        if scheduler:
            scheduler.reschedule(def_id)
        return RedirectResponse("/", 303)

    @app.get("/{def_id}/preview")
    def preview(request: Request, def_id: int):
        r = run_definition(def_id, db=db, tofa=tofa_factory(), settings=settings, dry_run=True)
        return page(request, "preview.html", d=db.get_definition(def_id), r=r)

    @app.post("/{def_id}/run")
    def run(def_id: int):
        if scheduler:
            scheduler.run_now(def_id)
        else:
            run_definition(def_id, db=db, tofa=tofa_factory(), settings=settings)
        return RedirectResponse("/", 303)

    @app.post("/{def_id}/delete")
    async def delete(request: Request, def_id: int):
        f = dict((await request.form()).items())
        d = db.get_definition(def_id)
        if f.get("confirm") != "yes":
            return page(request, "preview.html", 400, d=d, r=None, error="Delete needs confirm=yes")
        if d and f.get("delete_in_tofa") == "yes" and d["tofa_id"]:
            try:
                tofa_factory().delete_collection(d["tofa_id"])
            except TofaError:
                pass
        db.delete_definition(def_id)
        if scheduler:
            scheduler.remove(def_id)
        return RedirectResponse("/", 303)

    return app
