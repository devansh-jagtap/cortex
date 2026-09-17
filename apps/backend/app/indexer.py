"""Persistent photo indexing for Cortex.

The index is local-only and non-destructive: it stores metadata and derived
thumbnails under ``storage/`` while leaving the user's originals untouched.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable

from PIL import Image, ExifTags

from app.scanner import SUPPORTED_IMAGE_EXTENSIONS

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATABASE_PATH = REPO_ROOT / "storage" / "database" / "cortex.sqlite3"
DEFAULT_THUMBNAIL_DIR = REPO_ROOT / "storage" / "thumbnails"
THUMBNAIL_SIZE = (384, 384)


@dataclass
class IndexedImage:
    id: int
    path: str
    thumbnail_path: str | None


@dataclass
class IndexResult:
    root_path: str
    total_files: int = 0
    supported_images: int = 0
    unsupported_files: int = 0
    new_images: int = 0
    changed_images: int = 0
    unchanged_images: int = 0
    removed_images: int = 0
    thumbnails_created: int = 0
    errors: list[str] = field(default_factory=list)
    sample_images: list[str] = field(default_factory=list)
    indexed_images: list[IndexedImage] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "root_path": self.root_path,
            "total_files": self.total_files,
            "supported_images": self.supported_images,
            "unsupported_files": self.unsupported_files,
            "new_images": self.new_images,
            "changed_images": self.changed_images,
            "unchanged_images": self.unchanged_images,
            "removed_images": self.removed_images,
            "thumbnails_created": self.thumbnails_created,
            "error_count": len(self.errors),
            "errors": self.errors[:20],
            "sample_images": self.sample_images[:20],
            "indexed_images": [
                {
                    "id": image.id,
                    "path": image.path,
                    "thumbnail_path": image.thumbnail_path,
                }
                for image in self.indexed_images[:40]
            ],
        }


def connect_database(database_path: Path = DEFAULT_DATABASE_PATH) -> sqlite3.Connection:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    migrate(connection)
    return connection


def migrate(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS roots (
            id INTEGER PRIMARY KEY,
            path TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            last_scanned_at TEXT
        );

        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY,
            root_id INTEGER NOT NULL REFERENCES roots(id) ON DELETE CASCADE,
            path TEXT NOT NULL UNIQUE,
            size_bytes INTEGER NOT NULL,
            modified_ns INTEGER NOT NULL,
            content_hash TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            thumbnail_path TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS image_metadata (
            file_id INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
            width INTEGER,
            height INTEGER,
            captured_at TEXT,
            camera_make TEXT,
            camera_model TEXT,
            gps_latitude REAL,
            gps_longitude REAL,
            orientation INTEGER,
            extracted_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS index_jobs (
            id INTEGER PRIMARY KEY,
            root_id INTEGER NOT NULL REFERENCES roots(id) ON DELETE CASCADE,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            processed INTEGER NOT NULL DEFAULT 0,
            total INTEGER NOT NULL DEFAULT 0,
            current_path TEXT,
            error TEXT
        );
        """
    )
    connection.commit()


def index_folder(
    root_path: str,
    database_path: Path = DEFAULT_DATABASE_PATH,
    thumbnail_dir: Path = DEFAULT_THUMBNAIL_DIR,
    max_samples: int = 20,
) -> IndexResult:
    result = IndexResult(root_path=root_path)

    if not os.path.exists(root_path):
        result.errors.append(f"Path does not exist: {root_path}")
        return result

    if not os.path.isdir(root_path):
        result.errors.append(f"Path is not a directory: {root_path}")
        return result

    thumbnail_dir.mkdir(parents=True, exist_ok=True)

    with connect_database(database_path) as connection:
        now = _now()
        root_id = _upsert_root(connection, root_path, now)
        job_id = _start_job(connection, root_id, now)
        seen_paths: set[str] = set()

        try:
            excluded_dirs = {database_path.parent.resolve(), thumbnail_dir.resolve()}
            for path in _walk_files(root_path, result, excluded_dirs):
                result.total_files += 1
                ext = os.path.splitext(path)[1].lower()
                if ext not in SUPPORTED_IMAGE_EXTENSIONS:
                    result.unsupported_files += 1
                    continue

                result.supported_images += 1
                seen_paths.add(path)
                if len(result.sample_images) < max_samples:
                    result.sample_images.append(path)

                try:
                    indexed = _index_image(connection, root_id, path, thumbnail_dir, result)
                    result.indexed_images.append(indexed)
                except OSError as exc:
                    result.errors.append(f"{path}: {exc}")
                finally:
                    _update_job_progress(connection, job_id, result.supported_images, path)

            result.removed_images = _mark_missing_files(connection, root_id, seen_paths, now)
            connection.execute(
                "UPDATE roots SET last_scanned_at = ? WHERE id = ?",
                (now, root_id),
            )
            _finish_job(connection, job_id, "completed", result.supported_images, None)
            connection.commit()
        except Exception as exc:
            _finish_job(connection, job_id, "failed", result.supported_images, str(exc))
            connection.commit()
            raise

    return result


def _walk_files(
    root_path: str,
    result: IndexResult,
    excluded_dirs: set[Path],
) -> Iterable[str]:
    def on_walk_error(exc: OSError) -> None:
        result.errors.append(f"{exc.filename}: {exc.strerror}")

    for dirpath, dirnames, filenames in os.walk(root_path, onerror=on_walk_error):
        dirnames[:] = [
            dirname
            for dirname in dirnames
            if (Path(dirpath) / dirname).resolve() not in excluded_dirs
        ]
        for filename in filenames:
            yield os.path.join(dirpath, filename)


def _index_image(
    connection: sqlite3.Connection,
    root_id: int,
    path: str,
    thumbnail_dir: Path,
    result: IndexResult,
) -> IndexedImage:
    stat = os.stat(path)
    previous = connection.execute(
        "SELECT * FROM files WHERE path = ?",
        (path,),
    ).fetchone()

    if (
        previous
        and previous["size_bytes"] == stat.st_size
        and previous["modified_ns"] == stat.st_mtime_ns
        and previous["status"] == "active"
    ):
        result.unchanged_images += 1
        return IndexedImage(
            id=previous["id"],
            path=path,
            thumbnail_path=previous["thumbnail_path"],
        )

    content_hash = _hash_file(path)
    now = _now()
    metadata = _extract_metadata(path)

    file_id: int
    if previous:
        file_id = previous["id"]
        result.changed_images += 1
        connection.execute(
            """
            UPDATE files
            SET root_id = ?, size_bytes = ?, modified_ns = ?, content_hash = ?,
                status = 'active', updated_at = ?
            WHERE id = ?
            """,
            (root_id, stat.st_size, stat.st_mtime_ns, content_hash, now, file_id),
        )
    else:
        result.new_images += 1
        cursor = connection.execute(
            """
            INSERT INTO files (
                root_id, path, size_bytes, modified_ns, content_hash,
                status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, 'active', ?, ?)
            """,
            (root_id, path, stat.st_size, stat.st_mtime_ns, content_hash, now, now),
        )
        file_id = int(cursor.lastrowid)

    thumbnail_path = _thumbnail_path(thumbnail_dir, file_id)
    if _create_thumbnail(path, thumbnail_path):
        result.thumbnails_created += 1
        connection.execute(
            "UPDATE files SET thumbnail_path = ?, updated_at = ? WHERE id = ?",
            (str(thumbnail_path), now, file_id),
        )

    connection.execute(
        """
        INSERT INTO image_metadata (
            file_id, width, height, captured_at, camera_make, camera_model,
            gps_latitude, gps_longitude, orientation, extracted_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(file_id) DO UPDATE SET
            width = excluded.width,
            height = excluded.height,
            captured_at = excluded.captured_at,
            camera_make = excluded.camera_make,
            camera_model = excluded.camera_model,
            gps_latitude = excluded.gps_latitude,
            gps_longitude = excluded.gps_longitude,
            orientation = excluded.orientation,
            extracted_at = excluded.extracted_at
        """,
        (
            file_id,
            metadata.get("width"),
            metadata.get("height"),
            metadata.get("captured_at"),
            metadata.get("camera_make"),
            metadata.get("camera_model"),
            metadata.get("gps_latitude"),
            metadata.get("gps_longitude"),
            metadata.get("orientation"),
            now,
        ),
    )

    row = connection.execute(
        "SELECT thumbnail_path FROM files WHERE id = ?",
        (file_id,),
    ).fetchone()
    return IndexedImage(id=file_id, path=path, thumbnail_path=row["thumbnail_path"])


def _extract_metadata(path: str) -> dict:
    metadata: dict = {}
    try:
        with Image.open(path) as image:
            metadata["width"], metadata["height"] = image.size
            exif = image.getexif()
            if not exif:
                return metadata

            tags = {ExifTags.TAGS.get(key, key): value for key, value in exif.items()}
            metadata["camera_make"] = _clean_text(tags.get("Make"))
            metadata["camera_model"] = _clean_text(tags.get("Model"))
            metadata["orientation"] = tags.get("Orientation")
            metadata["captured_at"] = _parse_exif_datetime(
                tags.get("DateTimeOriginal") or tags.get("DateTime")
            )
            gps = _extract_gps(tags.get("GPSInfo"))
            metadata.update(gps)
    except Exception:
        # Corrupt or fake images should still be indexed by path/mtime so a later
        # replacement with a valid image is detected and processed.
        return metadata
    return metadata


def _create_thumbnail(path: str, thumbnail_path: Path) -> bool:
    try:
        with Image.open(path) as image:
            image.thumbnail(THUMBNAIL_SIZE)
            if image.mode not in {"RGB", "L"}:
                image = image.convert("RGB")
            image.save(thumbnail_path, format="JPEG", quality=82, optimize=True)
            return True
    except Exception:
        return False


def _thumbnail_path(thumbnail_dir: Path, file_id: int) -> Path:
    return thumbnail_dir / f"{file_id}.jpg"


def _hash_file(path: str) -> str:
    digest = hashlib.blake2b(digest_size=32)
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _mark_missing_files(
    connection: sqlite3.Connection,
    root_id: int,
    seen_paths: set[str],
    now: str,
) -> int:
    rows = connection.execute(
        "SELECT id, path FROM files WHERE root_id = ? AND status = 'active'",
        (root_id,),
    ).fetchall()
    missing_ids = [row["id"] for row in rows if row["path"] not in seen_paths]
    if not missing_ids:
        return 0
    connection.executemany(
        "UPDATE files SET status = 'missing', updated_at = ? WHERE id = ?",
        [(now, file_id) for file_id in missing_ids],
    )
    return len(missing_ids)


def _upsert_root(connection: sqlite3.Connection, root_path: str, now: str) -> int:
    connection.execute(
        "INSERT OR IGNORE INTO roots (path, created_at) VALUES (?, ?)",
        (root_path, now),
    )
    row = connection.execute("SELECT id FROM roots WHERE path = ?", (root_path,)).fetchone()
    return int(row["id"])


def _start_job(connection: sqlite3.Connection, root_id: int, now: str) -> int:
    cursor = connection.execute(
        "INSERT INTO index_jobs (root_id, status, started_at) VALUES (?, 'running', ?)",
        (root_id, now),
    )
    return int(cursor.lastrowid)


def _update_job_progress(
    connection: sqlite3.Connection,
    job_id: int,
    processed: int,
    current_path: str,
) -> None:
    connection.execute(
        "UPDATE index_jobs SET processed = ?, current_path = ? WHERE id = ?",
        (processed, current_path, job_id),
    )


def _finish_job(
    connection: sqlite3.Connection,
    job_id: int,
    status: str,
    processed: int,
    error: str | None,
) -> None:
    connection.execute(
        """
        UPDATE index_jobs
        SET status = ?, finished_at = ?, processed = ?, total = ?, error = ?
        WHERE id = ?
        """,
        (status, _now(), processed, processed, error, job_id),
    )


def _parse_exif_datetime(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%Y:%m:%d %H:%M:%S").isoformat()
    except ValueError:
        return None


def _extract_gps(value: object) -> dict:
    if not isinstance(value, dict):
        return {}
    gps = {ExifTags.GPSTAGS.get(key, key): item for key, item in value.items()}
    latitude = _gps_coordinate(gps.get("GPSLatitude"), gps.get("GPSLatitudeRef"))
    longitude = _gps_coordinate(gps.get("GPSLongitude"), gps.get("GPSLongitudeRef"))
    if latitude is None or longitude is None:
        return {}
    return {"gps_latitude": latitude, "gps_longitude": longitude}


def _gps_coordinate(value: object, ref: object) -> float | None:
    if not isinstance(value, tuple) or len(value) != 3:
        return None
    degrees, minutes, seconds = (float(part) for part in value)
    coordinate = degrees + minutes / 60 + seconds / 3600
    if ref in {"S", "W"}:
        coordinate *= -1
    return coordinate


def _clean_text(value: object) -> str | None:
    if value is None:
        return None
    return str(value).strip() or None


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")
