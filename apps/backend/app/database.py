"""SQLite database connection and schema management."""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent.parent.parent / "storage" / "database" / "cortex.db"


def get_connection():
    """Get a database connection."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_schema():
    """Create all tables if they don't exist."""
    conn = get_connection()
    c = conn.cursor()

    # Folders the user indexed
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS roots (
            id INTEGER PRIMARY KEY,
            path TEXT UNIQUE NOT NULL,
            added_at REAL NOT NULL,
            last_scanned_at REAL
        )
    """
    )

    # All discovered files
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY,
            root_id INTEGER NOT NULL,
            path TEXT UNIQUE NOT NULL,
            filename TEXT NOT NULL,
            extension TEXT,
            kind TEXT DEFAULT 'unknown',
            size INTEGER,
            mtime_ns INTEGER,
            content_hash TEXT,
            status TEXT DEFAULT 'discovered',
            error TEXT,
            first_seen_at REAL NOT NULL,
            indexed_at REAL,
            FOREIGN KEY (root_id) REFERENCES roots(id)
        )
    """
    )

    # Image-specific metadata
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS image_metadata (
            file_id INTEGER PRIMARY KEY,
            width INTEGER,
            height INTEGER,
            orientation INTEGER,
            captured_at REAL,
            latitude REAL,
            longitude REAL,
            camera_make TEXT,
            camera_model TEXT,
            thumbnail_path TEXT,
            FOREIGN KEY (file_id) REFERENCES files(id)
        )
    """
    )

    # Indexing job tracking (for resumability)
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS index_jobs (
            id INTEGER PRIMARY KEY,
            root_id INTEGER NOT NULL,
            state TEXT DEFAULT 'running',
            total INTEGER DEFAULT 0,
            processed INTEGER DEFAULT 0,
            failed INTEGER DEFAULT 0,
            current_file TEXT,
            started_at REAL NOT NULL,
            finished_at REAL,
            error TEXT,
            FOREIGN KEY (root_id) REFERENCES roots(id)
        )
    """
    )

    # Create indexes for fast lookups
    c.execute("CREATE INDEX IF NOT EXISTS idx_files_root_id ON files(root_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_files_status ON files(status)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_files_kind ON files(kind)")

    conn.commit()
    conn.close()
