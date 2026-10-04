"""Where Cortex keeps its own data.

Everything here is Cortex-owned: the SQLite index, generated thumbnails, the
vector index, and downloaded model weights. The user's original files are
never written to. Locations are read at call time (not import time) so tests
can redirect them with CORTEX_STORAGE_DIR / CORTEX_MODELS_DIR.
"""

from __future__ import annotations

import os
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]


def storage_dir() -> Path:
    override = os.environ.get("CORTEX_STORAGE_DIR")
    return Path(override) if override else _REPO / "storage"


def database_path() -> Path:
    return storage_dir() / "database" / "cortex.db"


def thumbnails_dir() -> Path:
    return storage_dir() / "thumbnails"


def vectors_dir() -> Path:
    return storage_dir() / "vectors"


def models_dir() -> Path:
    override = os.environ.get("CORTEX_MODELS_DIR")
    return Path(override) if override else _REPO / "models"
