"""Semantic search: embeddings in SQLite, nearest-neighbour search in FAISS.

SQLite is the source of truth: every image vector is stored as a BLOB in the
`embeddings` table. The FAISS index is a cache rebuilt from those rows: after
every change the index is reconciled against SQLite, so it can never drift
(a vector whose image went missing is dropped; a re-embedded image is
replaced). FAISS ids are the SQLite file ids, never array positions.

Search is exact (inner product on unit vectors = cosine similarity). At
100k photos that is a few milliseconds; approximate indexes only pay off
around a million vectors.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import asdict, dataclass
from typing import Callable

import faiss
import numpy as np
from PIL import Image, ImageOps

from app.database import get_connection
from app.embedder import Embedder

log = logging.getLogger("cortex.search")


class VectorIndex:
    """Exact cosine search over unit vectors, keyed by SQLite file id."""

    def __init__(self, dim: int) -> None:
        self.dim = dim
        self._index = faiss.IndexIDMap2(faiss.IndexFlatIP(dim))
        self._stamps: dict[int, float] = {}
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return self._index.ntotal

    def sync(self, rows: dict[int, float], load: Callable[[list[int]], dict[int, np.ndarray]]) -> tuple[int, int]:
        """Make the index hold exactly `rows` (file id -> embedding timestamp).

        Returns (added, removed). Only vectors that are new or re-embedded
        are loaded from SQLite.
        """
        with self._lock:
            stale = [fid for fid, stamp in self._stamps.items() if rows.get(fid) != stamp]
            fresh = [fid for fid, stamp in rows.items() if self._stamps.get(fid) != stamp]
            if stale:
                self._index.remove_ids(np.array(stale, dtype=np.int64))
                for fid in stale:
                    del self._stamps[fid]
            for start in range(0, len(fresh), 1000):
                chunk = fresh[start : start + 1000]
                vectors = load(chunk)
                ids = [fid for fid in chunk if fid in vectors]
                if ids:
                    self._index.add_with_ids(
                        np.stack([vectors[fid] for fid in ids]), np.array(ids, dtype=np.int64)
                    )
                    for fid in ids:
                        self._stamps[fid] = rows[fid]
            return len(fresh), len(stale)

    def search(self, query: np.ndarray, k: int) -> list[tuple[int, float]]:
        with self._lock:
            if self._index.ntotal == 0:
                return []
            scores, ids = self._index.search(query.reshape(1, -1).astype(np.float32), min(k, self._index.ntotal))
        return [(int(i), float(s)) for i, s in zip(ids[0], scores[0]) if i != -1]


@dataclass
class EmbedProgress:
    total: int = 0
    processed: int = 0
    failed: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


class SearchService:
    def __init__(self, embedder_factory: Callable[[], Embedder]) -> None:
        self._embedder_factory = embedder_factory
        self._index: VectorIndex | None = None
        self._lock = threading.Lock()

    @property
    def embedder(self) -> Embedder:
        return self._embedder_factory()

    def _vector_index(self) -> VectorIndex:
        with self._lock:
            if self._index is None or self._index.dim != self.embedder.dim:
                self._index = VectorIndex(self.embedder.dim)
            return self._index

    def pending_count(self) -> int:
        conn = get_connection()
        try:
            return conn.execute("SELECT COUNT(*)" + _PENDING, (self.embedder.name,)).fetchone()[0]
        finally:
            conn.close()

    def embed_pending(
        self,
        on_progress: Callable[[EmbedProgress], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> EmbedProgress:
        """Embed every indexed image that has no vector yet for the current model."""
        embedder = self.embedder
        conn = get_connection()
        try:
            todo = conn.execute(
                "SELECT f.id, f.path, m.thumbnail_path" + _PENDING + " ORDER BY f.id", (embedder.name,)
            ).fetchall()
        finally:
            conn.close()

        progress = EmbedProgress(total=len(todo))
        if not todo:
            return progress
        if on_progress:
            on_progress(progress)

        batch_size = getattr(embedder, "batch_size", 16)
        for start in range(0, len(todo), batch_size):
            if should_cancel and should_cancel():
                break
            batch = todo[start : start + batch_size]
            images, ids = [], []
            for row in batch:
                img = _load_for_embedding(row["thumbnail_path"], row["path"])
                if img is None:
                    progress.failed += 1
                else:
                    images.append(img)
                    ids.append(row["id"])
            if images:
                vectors = embedder.embed_images(images)
                now = time.time()
                conn = get_connection()
                try:
                    conn.executemany(
                        "INSERT OR REPLACE INTO embeddings (file_id, model, vector, created_at) VALUES (?, ?, ?, ?)",
                        [(fid, embedder.name, vec.astype(np.float32).tobytes(), now) for fid, vec in zip(ids, vectors)],
                    )
                    conn.commit()
                finally:
                    conn.close()
            progress.processed += len(batch)
            if on_progress:
                on_progress(progress)
        return progress

    def sync(self) -> tuple[int, int]:
        """Reconcile the in-memory index with SQLite (the source of truth)."""
        embedder = self.embedder
        conn = get_connection()
        try:
            rows = {
                r["file_id"]: r["created_at"]
                for r in conn.execute(
                    "SELECT e.file_id, e.created_at FROM embeddings e JOIN files f ON f.id = e.file_id "
                    "WHERE f.status = 'indexed' AND e.model = ?",
                    (embedder.name,),
                )
            }
        finally:
            conn.close()

        def load(ids: list[int]) -> dict[int, np.ndarray]:
            conn = get_connection()
            try:
                marks = ",".join("?" * len(ids))
                return {
                    r["file_id"]: np.frombuffer(r["vector"], dtype=np.float32)
                    for r in conn.execute(
                        f"SELECT file_id, vector FROM embeddings WHERE model = ? AND file_id IN ({marks})",
                        (embedder.name, *ids),
                    )
                }
            finally:
                conn.close()

        added, removed = self._vector_index().sync(rows, load)
        if added or removed:
            log.info("vector index synced: +%d -%d (%d total)", added, removed, len(self._vector_index()))
        return added, removed

    def indexed_vectors(self) -> int:
        return len(self._vector_index())

    def search(self, query: str, limit: int) -> list[tuple[int, float]]:
        vector = self.embedder.embed_text(query)
        return self._vector_index().search(vector, limit)


_PENDING = """
    FROM files f JOIN image_metadata m ON m.file_id = f.id
    WHERE f.status = 'indexed'
      AND NOT EXISTS (SELECT 1 FROM embeddings e WHERE e.file_id = f.id AND e.model = ?)
"""


def _load_for_embedding(thumbnail_path: str | None, original_path: str) -> Image.Image | None:
    """CLIP looks at 224px, so the 320px thumbnail (already rotated upright) is
    enough and far cheaper than decoding the original again."""
    for path in (thumbnail_path, original_path):
        if not path:
            continue
        try:
            with Image.open(path) as img:
                if img.format == "JPEG":
                    img.draft("RGB", (448, 448))
                img = ImageOps.exif_transpose(img)
                return img.convert("RGB")
        except Exception:
            continue
    return None
