"""Shared project-runtime configuration loading.

This module intentionally exposes configuration presence only.  It never
returns or logs secret values.  Entry points and direct stage replays use the
same project-root ``.env`` contract through this module.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


LLM_ENV_KEYS = (
    "FEATURE_CRAWLER_LLM_API_KEY",
    "FEATURE_CRAWLER_LLM_BASE_URL",
    "FEATURE_CRAWLER_LLM_MODEL",
)


def project_root() -> Path:
    """Return the repository root for the installed source tree."""

    return Path(__file__).resolve().parents[1]


def load_project_env(root: str | Path | None = None, *, override: bool = False) -> dict[str, Any]:
    """Load the project-root ``.env`` and return non-secret diagnostics.

    ``override=False`` preserves explicitly supplied process environment
    values.  This keeps CI/container configuration authoritative while making
    direct offline stage replays behave like the CLI entry points.
    """

    root_path = Path(root).resolve() if root is not None else project_root()
    env_path = root_path / ".env"
    found = env_path.is_file()
    loaded = bool(load_dotenv(dotenv_path=env_path, override=override)) if found else False
    return {
        "env_path": str(env_path),
        "env_found": found,
        "env_loaded": loaded,
        "api_key_present": bool(os.environ.get("FEATURE_CRAWLER_LLM_API_KEY", "").strip()),
        "base_url_present": bool(os.environ.get("FEATURE_CRAWLER_LLM_BASE_URL", "").strip()),
        "model_present": bool(os.environ.get("FEATURE_CRAWLER_LLM_MODEL", "").strip()),
    }


def llm_config_status(root: str | Path | None = None) -> dict[str, Any]:
    """Load the shared environment and return safe provider diagnostics."""

    status = load_project_env(root)
    status["model"] = os.environ.get("FEATURE_CRAWLER_LLM_MODEL", "").strip()
    status["provider_ready"] = bool(
        status["api_key_present"] and status["base_url_present"] and status["model_present"]
    )
    return status
