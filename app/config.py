import os
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class Settings:
    tofa_url: str
    tofa_api_key: str
    tmdb_api_key: str | None
    trakt_client_id: str | None
    config_dir: Path
    seerr_url: str | None = None
    seerr_api_key: str | None = None

    @classmethod
    def from_env(cls) -> "Settings":  # noqa: D401
        return cls(
            tofa_url=os.environ.get("TOFA_URL", "").rstrip("/"),
            tofa_api_key=os.environ.get("TOFA_API_KEY", ""),
            tmdb_api_key=os.environ.get("TMDB_API_KEY") or None,
            trakt_client_id=os.environ.get("TRAKT_CLIENT_ID") or None,
            config_dir=Path(os.environ.get("CONFIG_DIR", "/config")),
            seerr_url=(os.environ.get("SEERR_URL") or "").rstrip("/") or None,
            seerr_api_key=os.environ.get("SEERR_API_KEY") or None,
        )


class LiveSettings:
    """Settings saved in the web UI (stored in the DB) override the environment defaults."""
    KEYS = ("tofa_url", "tofa_api_key", "tmdb_api_key", "trakt_client_id", "seerr_url", "seerr_api_key")

    def __init__(self, env: Settings, db):
        self._env, self._db = env, db

    def _get(self, key: str):
        return self._db.get_setting(key) or getattr(self._env, key) or None

    @property
    def tofa_url(self) -> str:
        return (self._get("tofa_url") or "").rstrip("/")

    @property
    def tofa_api_key(self) -> str:
        return self._get("tofa_api_key") or ""

    @property
    def tmdb_api_key(self):
        return self._get("tmdb_api_key")

    @property
    def trakt_client_id(self):
        return self._get("trakt_client_id")

    @property
    def seerr_url(self):
        return (self._get("seerr_url") or "").rstrip("/") or None

    @property
    def seerr_api_key(self):
        return self._get("seerr_api_key")

    @property
    def config_dir(self):
        return self._env.config_dir

    def save(self, **values: str) -> None:
        for k, v in values.items():
            if k in self.KEYS:
                self._db.set_setting(k, (v or "").strip())
