"""Index images from a folder into the database."""

import hashlib
import os
import time
from dataclasses import dataclass, field

from app.database import get_connection, init_schema
from app.exif_extractor import extract_image_metadata
from app.thumbnail_generator import generate_thumbnail

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


@dataclass
class IndexResult:
    """Result of an indexing operation."""

    root_path: str
    total_files: int = 0
    supported_images: int = 0
    unsupported_files: int = 0
    new_images: int = 0
    changed_images: int = 0
    unchanged_images: int = 0
    removed_images: int = 0
    thumbnails_created: int = 0
    error_count: int = 0
    errors: list[str] = field(default_factory=list)
    indexed_images: list[dict] = field(default_factory=list)

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
            "error_count": self.error_count,
            "errors": self.errors[:20],
            "indexed_images": self.indexed_images[:50],
        }


def compute_hash(file_path: str) -> str | None:
    """Compute BLAKE2b hash of a file."""
    try:
        hasher = hashlib.blake2b(digest_size=32)
        with open(file_path, "rb") as f:
            while chunk := f.read(8192):
                hasher.update(chunk)
        return hasher.hexdigest()
    except Exception:
        return None


def index_folder(root_path: str) -> IndexResult:
    """Index all images in a folder."""
    result = IndexResult(root_path=root_path)
    init_schema()
    conn = get_connection()
    c = conn.cursor()

    # Get or create root record
    c.execute("SELECT id FROM roots WHERE path = ?", (root_path,))
    root_row = c.fetchone()
    if root_row:
        root_id = root_row[0]
    else:
        c.execute(
            "INSERT INTO roots (path, added_at) VALUES (?, ?)",
            (root_path, time.time()),
        )
        conn.commit()
        root_id = c.lastrowid

    # Scan the folder
    for dirpath, dirnames, filenames in os.walk(root_path):
        for filename in filenames:
            file_path = os.path.join(dirpath, filename)
            result.total_files += 1
            ext = os.path.splitext(filename)[1].lower()

            if ext not in SUPPORTED_EXTENSIONS:
                result.unsupported_files += 1
                continue

            result.supported_images += 1

            try:
                # Get file stats
                stat = os.stat(file_path)
                size = stat.st_size
                mtime_ns = int(stat.st_mtime_ns)

                # Check if already in DB
                c.execute(
                    "SELECT id, content_hash, mtime_ns FROM files WHERE path = ?",
                    (file_path,),
                )
                db_row = c.fetchone()

                if db_row:
                    db_id, db_hash, db_mtime = db_row

                    # Check if file changed (size or mtime different)
                    if size == stat.st_size and mtime_ns == db_mtime:
                        result.unchanged_images += 1
                        continue

                    # File changed, recompute hash
                    file_hash = compute_hash(file_path)
                    if file_hash == db_hash:
                        # Same content, just update mtime
                        c.execute(
                            "UPDATE files SET mtime_ns = ? WHERE id = ?",
                            (mtime_ns, db_id),
                        )
                        result.unchanged_images += 1
                        continue

                    # Content changed, reprocess
                    result.changed_images += 1
                else:
                    # New file
                    file_hash = compute_hash(file_path)
                    result.new_images += 1

                # Extract metadata
                metadata = extract_image_metadata(file_path)
                if metadata.get("error"):
                    result.error_count += 1
                    result.errors.append(f"{filename}: {metadata['error']}")
                    continue

                # Generate thumbnail
                thumbnail_path = generate_thumbnail(file_path, file_hash)
                if thumbnail_path:
                    result.thumbnails_created += 1

                # Save to database
                if db_row:
                    # Update existing
                    c.execute(
                        """
                        UPDATE files SET
                            content_hash = ?, size = ?, mtime_ns = ?, status = ?, indexed_at = ?
                        WHERE id = ?
                        """,
                        (file_hash, size, mtime_ns, "indexed", time.time(), db_id),
                    )
                    file_id = db_id
                else:
                    # Insert new
                    c.execute(
                        """
                        INSERT INTO files
                        (root_id, path, filename, extension, kind, size, mtime_ns, content_hash, status, first_seen_at, indexed_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            root_id,
                            file_path,
                            filename,
                            ext,
                            "image",
                            size,
                            mtime_ns,
                            file_hash,
                            "indexed",
                            time.time(),
                            time.time(),
                        ),
                    )
                    file_id = c.lastrowid

                # Save metadata
                c.execute("DELETE FROM image_metadata WHERE file_id = ?", (file_id,))
                c.execute(
                    """
                    INSERT INTO image_metadata
                    (file_id, width, height, orientation, captured_at, latitude, longitude, camera_make, camera_model, thumbnail_path)
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
                        thumbnail_path,
                    ),
                )

                result.indexed_images.append(
                    {
                        "id": file_id,
                        "path": file_path,
                        "thumbnail_path": thumbnail_path,
                    }
                )

            except Exception as e:
                result.error_count += 1
                result.errors.append(f"{filename}: {str(e)}")

    # Update root's last_scanned_at
    c.execute(
        "UPDATE roots SET last_scanned_at = ? WHERE id = ?",
        (time.time(), root_id),
    )
    conn.commit()
    conn.close()

    return result
