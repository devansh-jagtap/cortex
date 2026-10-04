"""Watches indexed folders and reports changed image paths in batches.

Uses the OS's native change notifications (ReadDirectoryChangesW on
Windows) through `watchdog`, so nothing is polled. Events are collected and
handed over only after a short quiet period: copying a photo or finishing a
browser download fires several events for the same file, and the indexer
should see the file once, after it has been fully written.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Callable

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from app.exclusions import is_excluded
from app.scanner import SUPPORTED_IMAGE_EXTENSIONS

log = logging.getLogger("cortex.watcher")


class FolderWatcher:
    def __init__(self, on_changes: Callable[[set[str]], None], debounce_seconds: float = 1.0) -> None:
        self._on_changes = on_changes
        self._debounce = debounce_seconds
        self._observer = Observer()
        self._watches: dict[str, object] = {}
        self._pending: set[str] = set()
        self._last_event = 0.0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._flusher = threading.Thread(target=self._flush_loop, name="cortex-watch-flush", daemon=True)
        self._started = False

    def start(self) -> None:
        if not self._started:
            self._observer.start()
            self._flusher.start()
            self._started = True

    def stop(self) -> None:
        self._stop.set()
        if self._started:
            self._observer.stop()
            self._observer.join(timeout=5)

    def watch(self, root_path: str) -> bool:
        key = os.path.normcase(os.path.abspath(root_path))
        with self._lock:
            if key in self._watches or not os.path.isdir(root_path):
                return False
            try:
                self._watches[key] = self._observer.schedule(
                    _Handler(self, root_path), root_path, recursive=True
                )
            except OSError as exc:
                log.warning("cannot watch %s: %s", root_path, exc)
                return False
        return True

    def watched_roots(self) -> int:
        with self._lock:
            return len(self._watches)

    def record(self, path: str) -> None:
        with self._lock:
            self._pending.add(path)
            self._last_event = time.monotonic()

    def _flush_loop(self) -> None:
        while not self._stop.wait(0.2):
            with self._lock:
                if not self._pending or time.monotonic() - self._last_event < self._debounce:
                    continue
                batch, self._pending = self._pending, set()
            try:
                self._on_changes(batch)
            except Exception:
                log.exception("failed to hand over %d changed path(s)", len(batch))


class _Handler(FileSystemEventHandler):
    def __init__(self, watcher: FolderWatcher, root_path: str) -> None:
        self._watcher = watcher
        self._root = root_path

    def on_any_event(self, event: FileSystemEvent) -> None:
        kind = event.event_type
        if kind not in ("created", "modified", "deleted", "moved"):
            return
        if kind == "modified" and event.is_directory:
            return
        if kind == "moved":
            self._consider(os.fsdecode(event.src_path), event.is_directory, gone=True)
            self._consider(os.fsdecode(event.dest_path), event.is_directory, gone=False)
        else:
            self._consider(os.fsdecode(event.src_path), event.is_directory, gone=kind == "deleted")

    def _consider(self, path: str, is_dir: bool, gone: bool) -> None:
        if is_dir:
            if not is_excluded(path, self._root, is_dir=True):
                self._watcher.record(path)
            return
        ext = os.path.splitext(path)[1].lower()
        # Windows can't say whether a deleted path was a folder, so a vanished
        # path without an extension is treated as a possible folder.
        interesting = ext in SUPPORTED_IMAGE_EXTENSIONS or (gone and ext == "")
        if interesting and not is_excluded(path, self._root):
            self._watcher.record(path)
