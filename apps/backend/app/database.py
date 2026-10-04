"""SQLite connection and schema.

SQLite is the source of truth for everything Cortex knows about the user's
files. Schema changes are appended to MIGRATIONS; each connection brings the
database up to date using SQLite's built-in `user_version` counter.
"""

from __future__ import annotations

import sqlite3

from app.storage import database_path

MIGRATIONS: list[str] = [
    """
    CREATE TABLE roots (
        id INTEGER PRIMARY KEY,
        path TEXT UNIQUE NOT NULL,
        added_at REAL NOT NULL,
        last_scanned_at REAL
    );

    CREATE TABLE files (
        id INTEGER PRIMARY KEY,
        root_id INTEGER NOT NULL REFERENCES roots(id),
        path TEXT UNIQUE NOT NULL,
        filename TEXT NOT NULL,
        extension TEXT,
        kind TEXT NOT NULL DEFAULT 'image',
        size INTEGER,
        mtime_ns INTEGER,
        content_hash TEXT,
        status TEXT NOT NULL DEFAULT 'discovered',
        error TEXT,
        first_seen_at REAL NOT NULL,
        indexed_at REAL
    );

    CREATE TABLE image_metadata (
        file_id INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
        width INTEGER,
        height INTEGER,
        orientation INTEGER,
        captured_at REAL,
        latitude REAL,
        longitude REAL,
        camera_make TEXT,
        camera_model TEXT,
        thumbnail_path TEXT
    );

    CREATE TABLE index_jobs (
        id INTEGER PRIMARY KEY,
        root_id INTEGER NOT NULL REFERENCES roots(id),
        state TEXT NOT NULL DEFAULT 'running',
        total INTEGER NOT NULL DEFAULT 0,
        processed INTEGER NOT NULL DEFAULT 0,
        failed INTEGER NOT NULL DEFAULT 0,
        current_file TEXT,
        started_at REAL NOT NULL,
        finished_at REAL,
        error TEXT
    );

    CREATE INDEX idx_files_root_id ON files(root_id);
    CREATE INDEX idx_files_status ON files(status);
    CREATE INDEX idx_files_hash ON files(content_hash);
    """,
]


def get_connection() -> sqlite3.Connection:
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for number, script in enumerate(MIGRATIONS[version:], start=version + 1):
        conn.executescript(f"BEGIN; {script} PRAGMA user_version = {number}; COMMIT;")
