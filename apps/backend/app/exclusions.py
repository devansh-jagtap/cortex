"""Folders Cortex never looks inside.

Matters once Cortex watches whole home folders or drives: system folders,
application data, developer tooling, and Cortex's own storage would
otherwise flood the index with icons, caches, and its own thumbnails.
Rules apply only *below* a root the user chose, never to the root itself.
"""

from __future__ import annotations

import os

from app.storage import models_dir, storage_dir

EXCLUDED_ANYWHERE = {
    "appdata",
    "node_modules",
    "__pycache__",
    "site-packages",
    "venv",
    "$recycle.bin",
    "system volume information",
}

EXCLUDED_AT_DRIVE_ROOT = {
    "windows",
    "program files",
    "program files (x86)",
    "programdata",
    "recovery",
    "perflogs",
}


def skip_dir(parent: str, name: str) -> bool:
    lower = name.lower()
    if lower.startswith(".") or lower in EXCLUDED_ANYWHERE:
        return True
    if lower in EXCLUDED_AT_DRIVE_ROOT and _is_drive_root(parent):
        return True
    return _is_cortex_data(os.path.join(parent, name))


def is_excluded(path: str, root: str, is_dir: bool = False) -> bool:
    """True if any folder between `root` and `path` is skipped, or `path` is outside `root`."""
    target = path if is_dir else os.path.dirname(path)
    try:
        rel = os.path.relpath(target, root)
    except ValueError:  # different drive
        return True
    if rel == ".":
        return False
    parent = root
    for part in rel.split(os.sep):
        if part == "..":
            return True
        if skip_dir(parent, part):
            return True
        parent = os.path.join(parent, part)
    return False


def _is_drive_root(path: str) -> bool:
    drive, rest = os.path.splitdrive(os.path.abspath(path))
    return bool(drive) and rest in ("\\", "/", "")


def _is_cortex_data(path: str) -> bool:
    candidate = os.path.normcase(os.path.abspath(path))
    return candidate in {
        os.path.normcase(os.path.abspath(storage_dir())),
        os.path.normcase(os.path.abspath(models_dir())),
    }
