import os
import re
from pathlib import Path
from urllib.parse import quote, urlsplit
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from app.discover.service import CATALOG, DISCOVERABLE, TITLES, cfg_for, load_shelf
from app.seerr import SeerrClient, SeerrError
from app.sources import SOURCE_TYPES, get_source
from app.sources.tmdb import search_collections
from app.sources.trakt import trending_lists
from app.sources.base import SourceError
from app.sync import run_definition
from app.tofa.client import TofaError

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
IMAGE_PATH = re.compile(r"artwork/[0-9a-fA-F-]{36}/(poster|backdrop)")


def poster_src(p: str) -> str:
    """Absolute image URLs load directly; Tofa library images are relative and go through our proxy."""
    if not p:
        return ""
    return p if p.startswith("http") else "/discover/img?path=" + quote(p)


TEMPLATES.env.globals["poster_src"] = poster_src
TEMPLATES.env.globals["app_version"] = lambda: os.environ.get("APP_VERSION", "dev")

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
    if t == "imdb_list":
        return {"list": f.get("imdb_list", "").strip()}
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
            "trakt_slug": c.get("slug", ""), "imdb_list": c.get("list", ""), "interval_minutes": d["interval_minutes"],
            "prune": d["prune"], "enabled": d["enabled"]}

def create_app(settings, db, tofa_factory, scheduler=None, probe_factory=None, seerr_factory=None) -> FastAPI:
    app = FastAPI(title="Tofarr")
    probe_factory = probe_factory or tofa_factory
    get_seerr = seerr_factory or (lambda: SeerrClient(settings.seerr_url, settings.seerr_api_key))

    def seerr_on() -> bool:
        return bool(settings.seerr_url and settings.seerr_api_key)

    def seerr_status(items) -> dict:
        """Seerr's label per title, keyed "<tmdb id>-<type>" so templates can look it up."""
        if not seerr_on() or not items:
            return {}
        try:
            found = get_seerr().statuses([(i.tmdb_id, i.media_type) for i in items])
        except SeerrError:
            return {}
        return {f"{k[0]}-{k[1]}": v for k, v in found.items()}
    previewed: set[int] = set()  # definitions the user has seen a dry run for since startup

    @app.middleware("http")
    async def same_origin_only(request: Request, call_next):
        if request.method == "POST":
            src = request.headers.get("origin") or request.headers.get("referer")
            if src and urlsplit(src).netloc != request.headers.get("host", ""):
                return PlainTextResponse("Cross-site request refused", status_code=403)
        return await call_next(request)

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

    def settings_page(request, status=200, message=None, error=None):
        flags = {k: bool(getattr(settings, k)) for k in ("tofa_api_key", "tmdb_api_key", "trakt_client_id",
                                                           "seerr_api_key")}
        return page(request, "settings.html", status, url=settings.tofa_url, seerr_url=settings.seerr_url or "",
                    is_set=flags, message=message, error=error)

    @app.get("/settings")
    def settings_form(request: Request):
        return settings_page(request)

    @app.post("/settings")
    async def settings_save(request: Request):
        f = {k: v.strip() for k, v in (await request.form()).items() if isinstance(v, str)}
        url = f.get("tofa_url", "").rstrip("/")
        if not url.startswith(("http://", "https://")):
            return settings_page(request, 400, error="Tofa URL must start with http:// or https:// "
                                                     "(example: http://192.168.1.10:33333)")
        seerr_url = f.get("seerr_url", "").rstrip("/")
        if seerr_url and not seerr_url.startswith(("http://", "https://")):
            return settings_page(request, 400, error="Seerr URL must start with http:// or https://")
        values = {"tofa_url": url, "seerr_url": seerr_url}  # an empty Seerr URL switches Seerr off
        for k in ("tofa_api_key", "tmdb_api_key", "trakt_client_id", "seerr_api_key"):
            if f.get(k):  # blank keeps the stored value
                values[k] = f[k]
        settings.save(**values)
        notes, problems = [], []
        try:
            notes.append(f"Connected to Tofa {probe_factory().system_info().get('version', '')}".strip())
        except TofaError as e:
            problems.append(f"Tofa: {e}")
        if seerr_on():
            try:
                notes.append(get_seerr().test())
            except SeerrError as e:
                problems.append(f"Seerr: {e}")
        if problems:
            return settings_page(request, error="Saved, but the connection test failed: " + "; ".join(problems))
        return settings_page(request, message="Saved. " + ". ".join(notes))

    @app.get("/")
    def index(request: Request):
        if not settings.tofa_url:
            info, tofa_error = None, "Tofa is not configured yet"
        else:
            try:
                info, tofa_error = probe_factory().system_info(), None
            except TofaError as e:
                info, tofa_error = None, str(e)
        rows = [{"d": d, "run": db.last_run(d["id"])} for d in db.list_definitions()]
        return page(request, "index.html", rows=rows, info=info, tofa_error=tofa_error)

    @app.get("/new")
    def new_form(request: Request):
        return form_page(request, {"source_type": "manual", "interval_minutes": 1440,
                                   "prune": True, "enabled": False, "discover_media_type": "movie",
                                   "max_pages": 3})

    @app.post("/new")
    async def new_save(request: Request):
        values, ok, err = await parse(request)
        if err:
            return form_page(request, values, err, 400)
        cfg, minutes = ok
        i = db.create_definition(values["name"].strip(), values.get("overview", ""), values["source_type"],
                                 cfg, minutes, values["prune"])
        db.update_definition(i, enabled=False)  # enable only after a preview, via Edit
        return RedirectResponse(f"/{i}/preview", 303)

    # ---------- Discover ----------

    def discover_page(request, source="tofa", status=200, error=None, q="", imdb_ref=""):
        ctx = {"source": source, "error": error, "q": q, "groups": [], "lists": [], "found": [], "need": None,
               "imdb_ref": imdb_ref}
        try:
            if source == "tofa":
                if not settings.tofa_url:
                    ctx["need"] = "Tofa is not configured yet. Open Settings first."
                else:
                    groups: dict[str, list] = {}
                    for sh in tofa_factory().discovery_shelves():
                        groups.setdefault(sh["kind"] or "other", []).append(sh)
                    ctx["groups"] = list(groups.items())
            elif source == "trakt":
                if not settings.trakt_client_id:
                    ctx["need"] = "Add a Trakt client ID in Settings to browse Trakt."
                else:
                    ctx["lists"] = trending_lists(settings.trakt_client_id, "popular")
            elif source == "tmdb":
                if not settings.tmdb_api_key:
                    ctx["need"] = "Add a TMDB API key in Settings to browse TMDB."
                elif q:
                    ctx["found"] = search_collections(settings.tmdb_api_key, q)
            elif source == "imdb":
                if not settings.tmdb_api_key:
                    ctx["need"] = "Add a TMDB API key in Settings. It is used to match IMDb titles."
        except (SourceError, TofaError) as e:
            ctx["error"] = str(e)
        return page(request, "discover.html", status, shelves=CATALOG.get(source, []), **ctx)

    @app.get("/discover")
    def discover(request: Request, source: str = "tofa", q: str = ""):
        return discover_page(request, source if source in ("tofa", "trakt", "tmdb", "imdb") else "tofa", q=q)

    @app.get("/discover/shelf")
    def discover_shelf(request: Request, type: str, arg: str, title: str = "", only: int = 0):
        shown = title or TITLES.get((type, arg)) or arg
        back = {"tofa_shelf": "tofa", "trakt_chart": "trakt", "trakt_list": "trakt", "imdb_chart": "imdb",
                "imdb_list": "imdb"}.get(type, "tmdb")
        if type not in DISCOVERABLE:
            return discover_page(request, "tofa", 400, f"Unknown source type: {type}")
        try:
            view = load_shelf(type, arg, settings, tofa_factory())
        except (SourceError, TofaError) as e:
            return page(request, "shelf.html", 400, type=type, arg=arg, title=shown, view=None, only=0, back=back,
                        error=str(e))
        items = [i for i in view.items if i.in_library] if only else view.items
        return page(request, "shelf.html", type=type, arg=arg, title=shown, view=view, items=items, only=only,
                    back=back, error=None, seerr_on=seerr_on(),
                    seerr_status=seerr_status([i for i in items if not i.in_library]))

    @app.post("/discover/create")
    async def discover_create(request: Request):
        f = dict((await request.form()).items())
        type_, arg, name = f.get("type", ""), f.get("arg", ""), f.get("name", "").strip()
        try:
            if type_ not in DISCOVERABLE:
                raise SourceError(f"Unknown source type: {type_}")
            cfg = cfg_for(type_, arg)
            get_source(type_, cfg, settings)
            if not name:
                raise SourceError("Give the collection a name")
        except SourceError as e:
            return discover_page(request, "tofa", 400, str(e))
        i = db.create_definition(name, f"Created from Discover: {TITLES.get((type_, arg), arg)}", type_, cfg, 1440, True)
        db.update_definition(i, enabled=False)  # enabled only after a preview, as for every new collection
        return RedirectResponse(f"/{i}/preview", 303)

    @app.get("/discover/img")
    def discover_img(path: str):
        if not IMAGE_PATH.fullmatch(path):
            return PlainTextResponse("Bad image path", status_code=400)
        try:
            data, ctype = tofa_factory().fetch_image(path)
        except TofaError:
            return PlainTextResponse("Image unavailable", status_code=502)
        return Response(data, media_type=ctype, headers={"Cache-Control": "private, max-age=86400"})

    # ---------- Seerr requests ----------

    @app.post("/seerr/request")
    async def seerr_request(request: Request):
        f = dict((await request.form()).items())
        media_type = f.get("media_type", "")
        try:
            tmdb_id = int(f.get("tmdb_id", ""))
        except ValueError:
            return JSONResponse({"ok": False, "label": "Bad title id"}, status_code=400)
        if media_type not in ("movie", "tv") or not seerr_on():
            return JSONResponse({"ok": False, "label": "Seerr is not configured" if not seerr_on() else "Bad media type"},
                                status_code=400)
        try:
            outcome = get_seerr().request(tmdb_id, media_type)
        except SeerrError as e:
            return JSONResponse({"ok": False, "label": str(e)}, status_code=502)
        return {"ok": True, "label": "Already requested" if outcome == "exists" else "Requested"}

    @app.post("/{def_id}/request-missing")
    async def request_missing(request: Request, def_id: int):
        d = db.get_definition(def_id)
        if not d:
            return RedirectResponse("/", 303)
        f = dict((await request.form()).items())
        if not seerr_on() or f.get("confirm") != "yes":
            msg = "Seerr is not configured" if not seerr_on() else "Tick the confirmation to request these titles"
            return page(request, "preview.html", 400, d=d, r=None, error=msg, seerr_on=seerr_on(), seerr_status={})
        r = run_definition(def_id, db=db, tofa=tofa_factory(), settings=settings, dry_run=True)
        seerr = get_seerr()
        report, limit = [], 100
        for m in r.missing[:limit]:
            try:
                outcome = seerr.request(m.tmdb_id, m.media_type)
                report.append((m, "Already requested" if outcome == "exists" else "Requested", True))
            except SeerrError as e:
                report.append((m, str(e), False))
                if e.fatal:
                    break
        done = sum(1 for _, _, ok in report if ok)
        summary = f"Requested {done} of {len(r.missing)} missing titles"
        if len(r.missing) > limit:
            summary += f" (at most {limit} per click, click again for the rest)"
        return page(request, "preview.html", d=d, r=r, seerr_on=True, seerr_status={}, report=report,
                    report_summary=summary)

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
        if values["enabled"] and not d["tofa_id"] and def_id not in previewed:
            return form_page(request, values, "Preview this collection before enabling it.", 400, def_id)
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
        if r.status == "preview":
            previewed.add(def_id)
        return page(request, "preview.html", d=db.get_definition(def_id), r=r, seerr_on=seerr_on(),
                    seerr_status=seerr_status(r.missing))

    @app.post("/{def_id}/run")
    def run(def_id: int):
        d = db.get_definition(def_id)
        if d and not d["tofa_id"] and def_id not in previewed:
            return RedirectResponse(f"/{def_id}/preview", 303)
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
