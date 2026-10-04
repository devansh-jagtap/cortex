"""Incremental image indexer.

Walks a root folder, and for each supported image decides — cheaply first —
whether it is new, unchanged, modified, or moved, then extracts metadata and a
thumbnail only when needed. Read-only with respect to the user's files.

Change detection order (cheapest first):
  1. same path + same size + same mtime   -> unchanged, no file read at all
  2. same path + same content hash        -> unchanged, just refresh size/mtime
  3. new path whose hash matches a file that went missing -> moved
  4. otherwise                             -> (re)extract metadata + thumbnail
"""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Callable

from app.database import get_connection
from app.exif_extractor import extract_image_metadata
from app.scanner import SUPPORTED_IMAGE_EXTENSIONS
from app.thumbnail_generator import generate_thumbnail

# Windows cloud-file attributes. A file with these set is a OneDrive
# "online-only" placeholder: reading it would silently trigger a download.
_PLACEHOLDER_MASK = 0x00001000 | 0x00040000 | 0x00400000


@dataclass
class IndexStats:
    root_path: str
    total_files: int = 0
    supported_images: int = 0
    unsupported_files: int = 0
    processed: int = 0
    new_images: int = 0
    changed_images: int = 0
    unchanged_images: int = 0
    moved_images: int = 0
    removed_images: int = 0
    placeholders: int = 0
    failed: int = 0
    current_file: str | None = None
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["errors"] = self.errors[:20]
        data["error_count"] = len(self.errors)
        return data


class IndexCancelled(Exception):
    pass


def compute_hash(file_path: str) -> str:
    hasher = hashlib.blake2b(digest_size=20)
    with open(file_path, "rb") as f:
        while chunk := f.read(1024 * 1024):
            hasher.update(chunk)
    return hasher.hexdigest()


def is_cloud_placeholder(st: os.stat_result) -> bool:
    return bool(getattr(st, "st_file_attributes", 0) & _PLACEHOLDER_MASK)


def index_folder(
    root_path: str,
    on_progress: Callable[[IndexStats], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> IndexStats:
    root_path = os.path.abspath(root_path)
    if not os.path.exists(root_path):
        raise FileNotFoundError(f"Path does not exist: {root_path}")
    if not os.path.isdir(root_path):
        raise NotADirectoryError(f"Path is not a directory: {root_path}")

    stats = IndexStats(root_path=root_path)
    images = _discover(root_path, stats)
    if on_progress:
        on_progress(stats)

    conn = get_connection()
    try:
        root_id = _get_or_create_root(conn, root_path)
        seen: set[str] = set()

        for file_path in images:
            if should_cancel and should_cancel():
                raise IndexCancelled()

            stats.current_file = file_path
            seen.add(file_path)
            try:
                _index_one(conn, root_id, file_path, stats)
            except Exception as exc:
                stats.failed += 1
                stats.errors.append(f"{file_path}: {type(exc).__name__}: {exc}")
            stats.processed += 1

            # Commit per file: a crash loses nothing, and the write lock is
            # never held while other connections (progress, API reads) need it.
            conn.commit()
            if on_progress:
                on_progress(stats)

        stats.removed_images = _mark_missing(conn, root_id, seen)
        conn.execute("UPDATE roots SET last_scanned_at = ? WHERE id = ?", (time.time(), root_id))
        conn.commit()
    finally:
        conn.close()

    stats.current_file = None
    if on_progress:
        on_progress(stats)
    return stats


def _discover(root_path: str, stats: IndexStats) -> list[str]:
    def on_error(exc: OSError) -> None:
        stats.errors.append(f"{exc.filename}: {exc.strerror}")

    images: list[str] = []
    for dirpath, _dirnames, filenames in os.walk(root_path, onerror=on_error):
        for filename in filenames:
            stats.total_files += 1
            if os.path.splitext(filename)[1].lower() in SUPPORTED_IMAGE_EXTENSIONS:
                images.append(os.path.join(dirpath, filename))
            else:
                stats.unsupported_files += 1
    stats.supported_images = len(images)
    return images


def _get_or_create_root(conn, root_path: str) -> int:
    row = conn.execute("SELECT id FROM roots WHERE path = ?", (root_path,)).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO roots (path, added_at) VALUES (?, ?)", (root_path, time.time())
    )
    conn.commit()
    return cur.lastrowid


def _index_one(conn, root_id: int, file_path: str, stats: IndexStats) -> None:
    st = os.stat(file_path)
    if is_cloud_placeholder(st):
        stats.placeholders += 1
        return

    size, mtime_ns = st.st_size, st.st_mtime_ns
    row = conn.execute(
        "SELECT id, size, mtime_ns, content_hash, status FROM files WHERE path = ?",
        (file_path,),
    ).fetchone()

    if row and row["status"] == "indexed" and row["size"] == size and row["mtime_ns"] == mtime_ns:
        stats.unchanged_images += 1
        return

    content_hash = compute_hash(file_path)

    if row and row["status"] == "indexed" and row["content_hash"] == content_hash:
        conn.execute(
            "UPDATE files SET size = ?, mtime_ns = ? WHERE id = ?", (size, mtime_ns, row["id"])
        )
        stats.unchanged_images += 1
        return

    if not row:
        candidates = conn.execute(
            "SELECT id, path FROM files WHERE root_id = ? AND content_hash = ? AND status IN ('indexed', 'missing')",
            (root_id, content_hash),
        ).fetchall()
        moved = next((c for c in candidates if not os.path.exists(c["path"])), None)
        if moved:
            conn.execute(
                "UPDATE files SET path = ?, filename = ?, size = ?, mtime_ns = ?, status = 'indexed' WHERE id = ?",
                (file_path, os.path.basename(file_path), size, mtime_ns, moved["id"]),
            )
            stats.moved_images += 1
            return

    metadata = extract_image_metadata(file_path)
    now = time.time()
    filename = os.path.basename(file_path)
    extension = os.path.splitext(filename)[1].lower()

    if metadata["error"]:
        conn.execute(
            """
            INSERT INTO files (root_id, path, filename, extension, size, mtime_ns,
                               content_hash, status, error, first_seen_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'failed', ?, ?)
            ON CONFLICT(path) DO UPDATE SET size = excluded.size, mtime_ns = excluded.mtime_ns,
                content_hash = excluded.content_hash, status = 'failed', error = excluded.error
            """,
            (root_id, file_path, filename, extension, size, mtime_ns, content_hash, metadata["error"], now),
        )
        stats.failed += 1
        stats.errors.append(f"{file_path}: {metadata['error']}")
        return

    thumbnail = generate_thumbnail(file_path, content_hash)

    file_id = conn.execute(
        """
        INSERT INTO files (root_id, path, filename, extension, size, mtime_ns,
                           content_hash, status, error, first_seen_at, indexed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'indexed', NULL, ?, ?)
        ON CONFLICT(path) DO UPDATE SET size = excluded.size, mtime_ns = excluded.mtime_ns,
            content_hash = excluded.content_hash, status = 'indexed', error = NULL,
            indexed_at = excluded.indexed_at
        RETURNING id
        """,
        (root_id, file_path, filename, extension, size, mtime_ns, content_hash, now, now),
    ).fetchone()["id"]

    conn.execute(
        """
        INSERT OR REPLACE INTO image_metadata
            (file_id, width, height, orientation, captured_at, latitude, longitude,
             camera_make, camera_model, thumbnail_path)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            file_id,
            metadata["width"],
            metadata["height"],
            metadata["orientation"],
            metadata["captured_at"],
            metadata["latitude"],
            metadata["longitude"],
            metadata["camera_make"],
            metadata["camera_model"],
            thumbnail,
        ),
    )

    if row:
        stats.changed_images += 1
    else:
        stats.new_images += 1


def _mark_missing(conn, root_id: int, seen: set[str]) -> int:
    rows = conn.execute(
        "SELECT id, path FROM files WHERE root_id = ? AND status != 'missing'", (root_id,)
    ).fetchall()
    gone = [(r["id"],) for r in rows if r["path"] not in seen]
    conn.executemany("UPDATE files SET status = 'missing' WHERE id = ?", gone)
    return len(gone)
