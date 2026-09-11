"""Recursive filesystem scanning for image discovery.

Read-only: this module never renames, moves, deletes, or modifies
anything on disk. It only walks directories and reports what it finds.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

SUPPORTED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


@dataclass
class ScanResult:
    root_path: str
    total_files: int = 0
    supported_images: int = 0
    unsupported_files: int = 0
    errors: list[str] = field(default_factory=list)
    sample_images: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "root_path": self.root_path,
            "total_files": self.total_files,
            "supported_images": self.supported_images,
            "unsupported_files": self.unsupported_files,
            "error_count": len(self.errors),
            "errors": self.errors[:20],
            "sample_images": self.sample_images[:20],
        }


def scan_folder(root_path: str, max_samples: int = 20) -> ScanResult:
    """Recursively walk `root_path`, classifying files by extension.

    A single unreadable file or directory must not abort the whole scan:
    errors are collected and scanning continues (see rule #15).
    """
    result = ScanResult(root_path=root_path)

    if not os.path.exists(root_path):
        result.errors.append(f"Path does not exist: {root_path}")
        return result

    if not os.path.isdir(root_path):
        result.errors.append(f"Path is not a directory: {root_path}")
        return result

    def on_walk_error(exc: OSError) -> None:
        result.errors.append(f"{exc.filename}: {exc.strerror}")

    for dirpath, dirnames, filenames in os.walk(root_path, onerror=on_walk_error):
        for filename in filenames:
            result.total_files += 1
            ext = os.path.splitext(filename)[1].lower()
            if ext in SUPPORTED_IMAGE_EXTENSIONS:
                result.supported_images += 1
                if len(result.sample_images) < max_samples:
                    result.sample_images.append(os.path.join(dirpath, filename))
            else:
                result.unsupported_files += 1

    return result
