from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


APP_ROOT = Path(__file__).resolve().parent.parent


def _expand(value: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(value))).resolve()


def _load_json(path: Path, fallback: Path) -> dict[str, Any]:
    selected = path if path.exists() else fallback
    with selected.open("r", encoding="utf-8") as handle:
        return json.load(handle)


@dataclass(frozen=True)
class Settings:
    lm_base_url: str
    db_path: Path
    state_dir: Path
    models: dict[str, Any]
    repos: dict[str, Any]
    access_token: str
    cookie_secure: bool
    piper_model: Path | None
    upload_dir: Path


def load_settings() -> Settings:
    config_dir = _expand(os.getenv("JARVIS_CONFIG_DIR", "~/.config/jarvis"))
    state_dir = _expand(os.getenv("JARVIS_STATE_DIR", "~/.local/state/jarvis"))
    state_dir.mkdir(parents=True, exist_ok=True)
    models = _load_json(config_dir / "models.json", APP_ROOT / "config" / "models.json")
    repos = _load_json(config_dir / "repositories.json", APP_ROOT / "config" / "repositories.json")
    piper_value = os.getenv("JARVIS_PIPER_MODEL", "").strip()
    return Settings(
        lm_base_url=os.getenv("LM_STUDIO_BASE_URL", "http://127.0.0.1:1234/v1").rstrip("/"),
        db_path=_expand(os.getenv("JARVIS_DB_PATH", str(state_dir / "jarvis.db"))),
        state_dir=state_dir,
        models=models,
        repos=repos,
        access_token=os.getenv("JARVIS_ACCESS_TOKEN", ""),
        cookie_secure=os.getenv("JARVIS_COOKIE_SECURE", "0") == "1",
        piper_model=_expand(piper_value) if piper_value else None,
        upload_dir=_expand(os.getenv("JARVIS_UPLOAD_DIR", "~/Desktop/JARVIS_UPLOADS")),
    )
