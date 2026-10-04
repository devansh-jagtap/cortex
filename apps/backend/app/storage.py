"""Where Cortex keeps its own data.

Everything here is Cortex-owned: the SQLite index and generated thumbnails.
The user's original files are never written to. The location is read at call
time (not import time) so tests can redirect it with CORTEX_STORAGE_DIR.
"""

from __future__ import annotations

import os
from pathlib import Path

_REPO_STORAGE = Path(__file__).resolve().parents[3] / "storage"


def storage_dir() -> Path:
    override = os.environ.get("CORTEX_STORAGE_DIR")
    return Path(override) if override else _REPO_STORAGE


def database_path() -> Path:
    return storage_dir() / "database" / "cortex.db"


def thumbnails_dir() -> Path:
    return storage_dir() / "thumbnails"
