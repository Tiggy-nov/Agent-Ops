from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    host: str
    port: int
    upstream_url: str
    database_path: Path
    dashboard_enabled: bool
    audit_hours: int
    hash_secret: str

    @classmethod
    def from_env(cls) -> "Config":
        data_dir = Path(os.environ.get("HOISTWAY_DATA_DIR", "./data")).resolve()
        data_dir.mkdir(parents=True, exist_ok=True)
        return cls(
            host=os.environ.get("HOISTWAY_HOST", "127.0.0.1"),
            port=int(os.environ.get("HOISTWAY_PORT", "8787")),
            upstream_url=os.environ.get("HOISTWAY_UPSTREAM_URL", "https://api.openai.com").rstrip("/"),
            database_path=data_dir / "audit.db",
            dashboard_enabled=os.environ.get("HOISTWAY_DASHBOARD", "true").lower() == "true",
            audit_hours=int(os.environ.get("HOISTWAY_AUDIT_HOURS", "48")),
            hash_secret=os.environ.get("HOISTWAY_HASH_SECRET", secrets.token_hex(32)),
        )
