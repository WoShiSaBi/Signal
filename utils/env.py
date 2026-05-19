from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv


def load_project_env() -> bool:
    project_root = Path(__file__).resolve().parent.parent
    primary_env = project_root / ".env"
    legacy_env = project_root / "Signal-main" / ".env"

    loaded = load_dotenv(primary_env)
    if not loaded and legacy_env.exists():
        loaded = load_dotenv(legacy_env)

    return loaded
