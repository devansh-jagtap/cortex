"""The background indexing worker.

Every write to the index goes through one worker thread, one task at a time:
full folder scans (started by the user, or the catch-up scan at startup),
small batches of changed paths from the filesystem watcher, and the AI
embedding pass that follows either of them. Running them in sequence means
a scan, a watcher update, and the embedder can never race each other.

Scan progress lives in memory for fast polling and is mirrored to the
`index_jobs` table, so a crash leaves a trace. Resuming is simply scanning
again: change detection skips everything already indexed.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field

from app.database import get_connection
from app.graph import GraphBuilder
from app.indexer import IndexCancelled, IndexStats, apply_changes, index_folder, remove_root
from app.search import EmbedProgress, SearchService

log = logging.getLogger("cortex.jobs")

PERSIST_EVERY_SECONDS = 1.0


class JobAlreadyRunning(Exception):
    pass


@dataclass
class _Task:
    kind: str  # "scan" | "changes" | "embed" | "graph" | "remove"
    root_path: str | None = None
    root_id: int | None = None
    job_id: int | None = None
    paths: set[str] = field(default_factory=set)


class IndexJobManager:
    def __init__(self, search: SearchService | None = None, graph: GraphBuilder | None = None) -> None:
        self._search = search
        self._graph = graph
        self._embedding: dict = {"state": "idle"}
        self._organizing: dict = {"state": "idle"}
        self._warmed = False
        self._cv = threading.Condition()
        self._queue: deque[_Task] = deque()
        self._current: _Task | None = None
        self._cancel = threading.Event()
        self._snapshot: dict | None = None
        self._last_activity_at: float | None = None
        self._thread: threading.Thread | None = None

    def recover_interrupted(self) -> None:
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE index_jobs SET state = 'interrupted', finished_at = ? "
                "WHERE state IN ('running', 'queued')",
                (time.time(),),
            )
            conn.commit()
        finally:
            conn.close()

    def start(self, root_path: str) -> int:
        """Queue a full scan of `root_path`. Raises if one is already queued or running."""
        with self._cv:
            if any(t.kind == "scan" and t.root_path == root_path for t in self._active_tasks()):
                raise JobAlreadyRunning()
            job_id = self._create_job_row(root_path)
            self._queue.append(_Task("scan", root_path=root_path, job_id=job_id))
            self._ensure_worker()
            self._cv.notify_all()
        return job_id

    def remove(self, root_id: int, root_path: str) -> None:
        """Queue forgetting a folder. Raises if it is being scanned right now."""
        with self._cv:
            if any(t.kind == "scan" and t.root_path == root_path for t in self._active_tasks()):
                raise JobAlreadyRunning()
            self._queue.append(_Task("remove", root_path=root_path, root_id=root_id))
            self._ensure_worker()
            self._cv.notify_all()

    def submit_changes(self, paths: set[str]) -> None:
        """Queue changed paths; merges into a batch that hasn't started yet."""
        if not paths:
            return
        with self._cv:
            pending = next((t for t in self._queue if t.kind == "changes"), None)
            if pending:
                pending.paths |= paths
            else:
                self._queue.append(_Task("changes", paths=set(paths)))
            self._ensure_worker()
            self._cv.notify_all()

    def request_embedding(self) -> None:
        """Queue an embedding pass (also reconciles the vector index)."""
        if self._search is None:
            return
        with self._cv:
            if not any(t.kind == "embed" for t in self._queue):
                self._queue.append(_Task("embed"))
            self._ensure_worker()
            self._cv.notify_all()

    def request_graph(self) -> None:
        """Queue a pass that brings places, scenes, events and relations up to date."""
        if self._graph is None:
            return
        with self._cv:
            if not any(t.kind == "graph" for t in self._queue):
                self._queue.append(_Task("graph"))
            self._ensure_worker()
            self._cv.notify_all()

    def cancel(self) -> bool:
        with self._cv:
            if self._current and self._current.kind in ("scan", "embed", "graph"):
                self._cancel.set()
                return True
        return False

    def is_running(self) -> bool:
        with self._cv:
            return self._current is not None or bool(self._queue)

    def wait_idle(self, timeout: float | None = None) -> bool:
        with self._cv:
            return self._cv.wait_for(lambda: self._current is None and not self._queue, timeout)

    def status(self) -> dict:
        with self._cv:
            snapshot = dict(self._snapshot) if self._snapshot else None
            extra = {
                "busy": self._current is not None or bool(self._queue),
                "updating": self._current is not None and self._current.kind == "changes",
                "queued_scans": sum(1 for t in self._queue if t.kind == "scan"),
                "last_activity_at": self._last_activity_at,
                "embedding": dict(self._embedding),
                "organizing": dict(self._organizing),
            }
        if snapshot is None:
            snapshot = self._last_persisted_job() or {"state": "idle"}
        snapshot.update(extra)
        return snapshot

    def _active_tasks(self) -> list[_Task]:
        return ([self._current] if self._current else []) + list(self._queue)

    def _ensure_worker(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._work, name="cortex-index-worker", daemon=True)
            self._thread.start()

    def _work(self) -> None:
        while True:
            with self._cv:
                self._cv.wait_for(lambda: bool(self._queue))
                task = self._queue.popleft()
                self._current = task
                if task.kind in ("scan", "embed", "graph"):
                    self._cancel.clear()
                if task.kind == "scan":
                    self._snapshot = {
                        "job_id": task.job_id,
                        "state": "running",
                        "started_at": time.time(),
                        "finished_at": None,
                        "error": None,
                        "stats": IndexStats(root_path=task.root_path).to_dict(),
                    }
            try:
                if task.kind == "scan":
                    self._run_scan(task)
                    self.request_embedding()
                elif task.kind == "changes":
                    stats = apply_changes(task.paths)
                    log.info("applied %d changed path(s): %s", len(task.paths), stats.to_dict())
                    self.request_embedding()
                elif task.kind == "remove":
                    removed = remove_root(task.root_id)
                    log.info("removed %s from the library (%d photo records)", task.root_path, removed)
                    with self._cv:
                        # Don't keep offering "Resume" for a folder that is gone.
                        if self._snapshot and self._snapshot["stats"].get("root_path") == task.root_path:
                            self._snapshot = None
                    self.request_embedding()  # drops their vectors from the search index
                elif task.kind == "embed":
                    self._run_embed()
                    self.request_graph()
                else:
                    self._run_graph()
            except Exception:
                log.exception("index task failed: %s", task.kind)
            finally:
                with self._cv:
                    self._current = None
                    self._last_activity_at = time.time()
                    self._cv.notify_all()

    def _run_scan(self, task: _Task) -> None:
        job_id, root_path = task.job_id, task.root_path
        last_persist = 0.0
        self._persist(job_id, "running", IndexStats(root_path=root_path).to_dict())

        def on_progress(stats: IndexStats) -> None:
            nonlocal last_persist
            data = stats.to_dict()
            with self._cv:
                self._snapshot["stats"] = data
            now = time.monotonic()
            if now - last_persist >= PERSIST_EVERY_SECONDS:
                last_persist = now
                try:
                    self._persist(job_id, "running", data)
                except Exception:
                    pass  # progress persistence is best-effort; never kill the job

        state, error, stats = "done", None, None
        try:
            stats = index_folder(root_path, on_progress, self._cancel.is_set)
        except IndexCancelled:
            state = "cancelled"
        except Exception as exc:
            state, error = "failed", f"{type(exc).__name__}: {exc}"

        with self._cv:
            if stats is not None:
                self._snapshot["stats"] = stats.to_dict()
            self._snapshot.update(state=state, error=error, finished_at=time.time())
            final = self._snapshot["stats"]
        self._persist(job_id, state, final, error, finished=True)

    def _run_embed(self) -> None:
        def on_progress(progress: EmbedProgress) -> None:
            with self._cv:
                self._embedding = {"state": "running", **progress.to_dict()}

        try:
            progress = self._search.embed_pending(on_progress, self._cancel.is_set)
            state = "cancelled" if self._cancel.is_set() else "done"
            with self._cv:
                self._embedding = {"state": state, **progress.to_dict()}
        except Exception as exc:
            with self._cv:
                self._embedding = {**self._embedding, "state": "error", "error": f"{type(exc).__name__}: {exc}"}
            raise
        finally:
            self._search.sync()
        if self._search.indexed_vectors() and not self._warmed:
            # Load the model and run one throwaway query now: together they
            # take ~15 s, which would otherwise land on the user's first search.
            try:
                self._search.embedder.embed_text("a photo")
                self._warmed = True
            except Exception:
                log.exception("could not load the AI model")

    def _run_graph(self) -> None:
        with self._cv:
            self._organizing = {"state": "running"}
        stats = self._graph.run(self._cancel.is_set)
        with self._cv:
            self._organizing = {
                "state": "error" if stats.errors else "done",
                "events": stats.events,
                "relations": stats.relations,
                "errors": stats.errors,
            }
        log.info("graph updated: %s", stats)

    def _create_job_row(self, root_path: str) -> int:
        conn = get_connection()
        try:
            row = conn.execute("SELECT id FROM roots WHERE path = ?", (root_path,)).fetchone()
            root_id = row["id"] if row else conn.execute(
                "INSERT INTO roots (path, added_at) VALUES (?, ?)", (root_path, time.time())
            ).lastrowid
            job_id = conn.execute(
                "INSERT INTO index_jobs (root_id, state, started_at) VALUES (?, 'queued', ?)",
                (root_id, time.time()),
            ).lastrowid
            conn.commit()
            return job_id
        finally:
            conn.close()

    def _persist(self, job_id: int, state: str, data: dict, error: str | None = None, finished: bool = False) -> None:
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
