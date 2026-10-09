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

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            tofa_url=os.environ.get("TOFA_URL", "").rstrip("/"),
            tofa_api_key=os.environ.get("TOFA_API_KEY", ""),
            tmdb_api_key=os.environ.get("TMDB_API_KEY") or None,
            trakt_client_id=os.environ.get("TRAKT_CLIENT_ID") or None,
            config_dir=Path(os.environ.get("CONFIG_DIR", "/config")),
        )
