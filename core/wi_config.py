"""Single source of truth reader for wealthincome.toml.

Usage:
    from core.wi_config import config
    port = config.api_port
    health_url = config.api_health_url

Every Python module that needs a port, path, or health URL reads from here.
Do not hardcode 8000/8501/8502 anywhere else.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore


_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CONFIG_PATH = _PROJECT_ROOT / "wealthincome.toml"


@dataclass(frozen=True)
class _Config:
    api_port: int
    dashboard_port: int
    dashboard_preview_port: int
    project: Path
    log_dir: Path
    data_dir: Path
    db_path: Path
    api_health_path: str
    api_timeout_sec: int
    retry_count: int
    retry_delay_sec: int

    @property
    def api_base_url(self) -> str:
        return f"http://localhost:{self.api_port}"

    @property
    def api_health_url(self) -> str:
        return f"http://127.0.0.1:{self.api_port}{self.api_health_path}"

    @property
    def dashboard_url(self) -> str:
        return f"http://127.0.0.1:{self.dashboard_port}"

    @property
    def dashboard_preview_url(self) -> str:
        return f"http://127.0.0.1:{self.dashboard_preview_port}"


def _load() -> _Config:
    if not _CONFIG_PATH.exists():
        raise FileNotFoundError(f"missing config at {_CONFIG_PATH}")
    raw = tomllib.loads(_CONFIG_PATH.read_text())

    project = Path(raw["paths"]["project"])
    return _Config(
        api_port=int(raw["ports"]["api"]),
        dashboard_port=int(raw["ports"]["dashboard"]),
        dashboard_preview_port=int(raw["ports"]["dashboard_preview"]),
        project=project,
        log_dir=project / raw["paths"]["log_dir"],
        data_dir=project / raw["paths"]["data_dir"],
        db_path=Path(os.environ.get("WEALTHINCOME_DB", project / raw["paths"]["db"])),
        api_health_path=str(raw["health"]["api_path"]),
        api_timeout_sec=int(raw["health"]["api_timeout_sec"]),
        retry_count=int(raw["health"]["retry_count"]),
        retry_delay_sec=int(raw["health"]["retry_delay_sec"]),
    )


config = _load()
