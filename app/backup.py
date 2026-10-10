"""Export and import of collections and settings as one JSON file.

The login (user, password hash, session secret) is never exported. API keys are only exported when asked for.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.sources import SOURCE_TYPES

FORMAT = 1
ADDRESS_SETTINGS = ("tofa_url", "seerr_url")
KEY_SETTINGS = ("tofa_api_key", "tmdb_api_key", "trakt_client_id", "seerr_api_key")
MIN_INTERVAL = 5


class BackupError(Exception):
    pass


@dataclass
class ImportReport:
    added: int = 0
    skipped: int = 0
    settings: int = 0
    errors: list[str] = field(default_factory=list)


def export_data(db, include_keys: bool = False) -> dict:
    names = ADDRESS_SETTINGS + (KEY_SETTINGS if include_keys else ())
    return {
        "app": "tofarr", "version": FORMAT,
        "exported": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "settings": {k: v for k in names if (v := db.get_setting(k))},
        "definitions": [{"name": d["name"], "overview": d["overview"] or "", "source_type": d["source_type"],
                         "source_config": d["source_config"], "interval_minutes": d["interval_minutes"],
                         "prune": d["prune"], "enabled": d["enabled"], "tofa_id": d["tofa_id"]}
                        for d in db.list_definitions()],
    }


def _check(entry, position: int) -> tuple[dict | None, str | None]:
    if not isinstance(entry, dict):
        return None, f"Entry {position}: not a collection"
    name = entry.get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > 200:
        return None, f"Entry {position}: missing or invalid name"
    if entry.get("source_type") not in SOURCE_TYPES:
        return None, f"{name}: unknown source type {entry.get('source_type')!r}"
    if not isinstance(entry.get("source_config"), dict):
        return None, f"{name}: the source settings are missing"
    interval = entry.get("interval_minutes")
    if not isinstance(interval, int) or isinstance(interval, bool) or not MIN_INTERVAL <= interval <= 525600:
        return None, f"{name}: the update interval must be at least {MIN_INTERVAL} minutes"
    tofa_id = entry.get("tofa_id")
    return {"name": name.strip(), "overview": str(entry.get("overview") or ""), "type": entry["source_type"],
            "config": entry["source_config"], "interval": interval, "prune": bool(entry.get("prune", True)),
            "tofa_id": tofa_id if isinstance(tofa_id, str) and tofa_id else None,
            "enabled": bool(entry.get("enabled", False))}, None


def import_data(db, data) -> ImportReport:
    """Add the collections of a backup. Same-named collections and existing settings are left as they are."""
    if not isinstance(data, dict) or data.get("app") != "tofarr":
        raise BackupError("This is not a Tofarr backup file.")
    if data.get("version") != FORMAT:
        raise BackupError(f"This backup has version {data.get('version')!r}, but this Tofarr reads version {FORMAT}.")
    if not isinstance(data.get("definitions"), list):
        raise BackupError("The backup has no list of collections.")
    report = ImportReport()
    saved = data.get("settings") if isinstance(data.get("settings"), dict) else {}
    for key in ADDRESS_SETTINGS + KEY_SETTINGS:  # only known settings, only into empty places
        value = saved.get(key)
        if isinstance(value, str) and value.strip() and not db.get_setting(key):
            db.set_setting(key, value.strip())
            report.settings += 1
    have = {d["name"] for d in db.list_definitions()}
    for position, entry in enumerate(data["definitions"], 1):
        clean, problem = _check(entry, position)
        if problem:
            report.errors.append(problem)
        elif clean["name"] in have:
            report.skipped += 1
        else:
            new_id = db.create_definition(clean["name"], clean["overview"], clean["type"], clean["config"],
                                          clean["interval"], clean["prune"])
            if clean["tofa_id"]:
                db.set_tofa_id(new_id, clean["tofa_id"])
            # only a collection that already exists in Tofa may come back switched on; a new one must be previewed first
            db.update_definition(new_id, enabled=bool(clean["enabled"] and clean["tofa_id"]))
            have.add(clean["name"])
            report.added += 1
    return report
