"""Runs one indexing job at a time on a background thread.

Progress lives in memory for fast polling and is mirrored to the `index_jobs`
table so a crash leaves a trace. Resuming is simply re-running the job: the
indexer's change detection skips everything already indexed.
"""

from __future__ import annotations

import threading
import time

from app.database import get_connection
from app.indexer import IndexCancelled, IndexStats, index_folder

PERSIST_EVERY_SECONDS = 1.0


class JobAlreadyRunning(Exception):
    pass


class IndexJobManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._snapshot: dict | None = None

    def recover_interrupted(self) -> None:
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE index_jobs SET state = 'interrupted', finished_at = ? WHERE state = 'running'",
                (time.time(),),
            )
            conn.commit()
        finally:
            conn.close()

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, root_path: str) -> int:
        with self._lock:
            if self.is_running():
                raise JobAlreadyRunning()

            conn = get_connection()
            try:
                row = conn.execute("SELECT id FROM roots WHERE path = ?", (root_path,)).fetchone()
                root_id = row["id"] if row else conn.execute(
                    "INSERT INTO roots (path, added_at) VALUES (?, ?)", (root_path, time.time())
                ).lastrowid
                job_id = conn.execute(
                    "INSERT INTO index_jobs (root_id, state, started_at) VALUES (?, 'running', ?)",
                    (root_id, time.time()),
                ).lastrowid
                conn.commit()
            finally:
                conn.close()

            self._cancel.clear()
            self._snapshot = {
                "job_id": job_id,
                "state": "running",
                "started_at": time.time(),
                "finished_at": None,
                "error": None,
                "stats": IndexStats(root_path=root_path).to_dict(),
            }
            self._thread = threading.Thread(
                target=self._run, args=(job_id, root_path), name=f"index-job-{job_id}", daemon=True
            )
            self._thread.start()
            return job_id

    def cancel(self) -> bool:
        if not self.is_running():
            return False
        self._cancel.set()
        return True

    def wait(self, timeout: float | None = None) -> None:
        thread = self._thread
        if thread:
            thread.join(timeout)

    def status(self) -> dict:
        with self._lock:
            if self._snapshot is not None:
                return dict(self._snapshot)
        return self._last_persisted_job() or {"state": "idle"}

    def _run(self, job_id: int, root_path: str) -> None:
        last_persist = 0.0

        def on_progress(stats: IndexStats) -> None:
            nonlocal last_persist
            with self._lock:
                self._snapshot["stats"] = stats.to_dict()
            now = time.monotonic()
            if now - last_persist >= PERSIST_EVERY_SECONDS:
                last_persist = now
                try:
                    self._persist(job_id, "running", stats)
                except Exception:
                    pass  # progress persistence is best-effort; never kill the job

        state, error, stats = "done", None, None
        try:
            stats = index_folder(root_path, on_progress, self._cancel.is_set)
        except IndexCancelled:
            state = "cancelled"
        except Exception as exc:
            state, error = "failed", f"{type(exc).__name__}: {exc}"

        with self._lock:
            if stats is not None:
                self._snapshot["stats"] = stats.to_dict()
            self._snapshot.update(state=state, error=error, finished_at=time.time())
            final_stats = self._snapshot["stats"]
        self._persist(job_id, state, None, final_stats, error, finished=True)

    def _persist(self, job_id, state, stats=None, stats_dict=None, error=None, finished=False):
        data = stats.to_dict() if stats is not None else stats_dict
        conn = get_connection()
        try:
            conn.execute(
                """
                UPDATE index_jobs SET state = ?, total = ?, processed = ?, failed = ?,
                    current_file = ?, error = ?, finished_at = ?
                WHERE id = ?
                """,
                (
                    state,
                    data["supported_images"],
                    data["processed"],
                    data["failed"],
                    data["current_file"],
                    error,
                    time.time() if finished else None,
                    job_id,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _last_persisted_job(self) -> dict | None:
        conn = get_connection()
        try:
            row = conn.execute(
                """
                SELECT j.*, r.path AS root_path FROM index_jobs j
                JOIN roots r ON r.id = j.root_id ORDER BY j.id DESC LIMIT 1
                """
            ).fetchone()
        finally:
            conn.close()
        if not row:
            return None
        return {
            "job_id": row["id"],
            "state": row["state"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "error": row["error"],
            "stats": {
                "root_path": row["root_path"],
                "supported_images": row["total"],
                "processed": row["processed"],
                "failed": row["failed"],
                "current_file": row["current_file"],
            },
        }
