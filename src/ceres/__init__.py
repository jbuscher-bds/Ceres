"""Ceres: 100% local Windows voice dictation, rewriting, and voice-editing app."""

from __future__ import annotations

import logging
import os
from pathlib import Path

os.environ.setdefault("OPENVINO_TELEMETRY_OPT_IN", "0")
logging.getLogger("openvino_telemetry").setLevel(logging.CRITICAL)

__version__ = "0.2.0"

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parents[1]
ASSETS_DIR = PROJECT_ROOT / "assets"
APP_ICON_PNG = ASSETS_DIR / "ceres.png"
APP_ICON_ICO = ASSETS_DIR / "ceres.ico"
DATA_DIR = PROJECT_ROOT / "data"
MODELS_DIR = PROJECT_ROOT / "models"
OV_CACHE_DIR = MODELS_DIR / ".ov_cache"
SETTINGS_PATH = DATA_DIR / "settings.json"
HISTORY_DB_PATH = DATA_DIR / "history.db"


def ensure_directories() -> None:
    """Ensure local data, model, and OpenVINO cache directories exist."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    OV_CACHE_DIR.mkdir(parents=True, exist_ok=True)
