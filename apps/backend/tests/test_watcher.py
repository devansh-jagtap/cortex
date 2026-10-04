"""End-to-end: real files, real OS change notifications, the real worker."""

import shutil
import time

import pytest

from app.database import get_connection
from app.indexer import index_folder
from app.jobs import IndexJobManager
from app.watcher import FolderWatcher
from tests.images import make_image


def _wait_for(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return False


def _row(filename):
    conn = get_connection()
    try:
        return conn.execute("SELECT id, status FROM files WHERE filename = ?", (filename,)).fetchone()
    finally:
        conn.close()


def _status(filename):
    row = _row(filename)
    return row["status"] if row else None


@pytest.fixture
def watched(tmp_path):
    root = tmp_path / "photos"
    root.mkdir()
    index_folder(str(root))  # registers the root, like adding a folder does
    jobs = IndexJobManager()
    watcher = FolderWatcher(jobs.submit_changes, debounce_seconds=0.3)
    watcher.start()
    assert watcher.watch(str(root))
    yield root, jobs, tmp_path
    watcher.stop()
    jobs.wait_idle(timeout=10)


def test_new_photo_is_indexed_without_a_rescan(watched):
    root, _, _ = watched
    make_image(root / "new.jpg")

    assert _wait_for(lambda: _status("new.jpg") == "indexed")


def test_deleted_photo_is_marked_missing(watched):
    root, _, _ = watched
    path = make_image(root / "a.jpg")
    assert _wait_for(lambda: _status("a.jpg") == "indexed")

    path.unlink()

    assert _wait_for(lambda: _status("a.jpg") == "missing")


def test_renamed_photo_keeps_its_record(watched):
    root, _, _ = watched
    path = make_image(root / "IMG_0001.jpg")
    assert _wait_for(lambda: _status("IMG_0001.jpg") == "indexed")
    original_id = _row("IMG_0001.jpg")["id"]

    path.rename(root / "beach.jpg")

    assert _wait_for(lambda: _status("beach.jpg") == "indexed")
    assert _row("beach.jpg")["id"] == original_id


def test_folder_moved_in_is_indexed_and_moved_out_is_missing(watched):
    root, _, tmp_path = watched
    outside = tmp_path / "Goa Trip"
    outside.mkdir()
    make_image(outside / "1.jpg")
    make_image(outside / "2.jpg", color=(3, 3, 3))

    shutil.move(str(outside), str(root / "Goa Trip"))
    assert _wait_for(lambda: _status("1.jpg") == "indexed" and _status("2.jpg") == "indexed")

    shutil.move(str(root / "Goa Trip"), str(tmp_path / "Goa Trip"))
    assert _wait_for(lambda: _status("1.jpg") == "missing" and _status("2.jpg") == "missing")


def test_non_images_and_excluded_folders_are_ignored(watched):
    root, jobs, _ = watched
    (root / "notes.txt").write_text("hello")
    (root / "node_modules").mkdir()
    make_image(root / "node_modules" / "icon.png")
    make_image(root / "real.jpg")

    assert _wait_for(lambda: _status("real.jpg") == "indexed")
    assert jobs.wait_idle(timeout=5)
    assert _status("icon.png") is None
