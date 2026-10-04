import os
import time

import pytest

from app.database import get_connection
from app.exif_extractor import extract_image_metadata
from app.indexer import index_folder
from app.storage import thumbnails_dir
from tests.images import make_image


def _statuses():
    conn = get_connection()
    try:
        return {r["filename"]: r["status"] for r in conn.execute("SELECT filename, status FROM files")}
    finally:
        conn.close()


def test_exif_date_gps_and_camera_are_extracted(tmp_path):
    path = make_image(
        tmp_path / "goa.jpg", taken="2024:03:15 10:30:45", gps=(15.2993, -74.124), model="Pixel 8"
    )

    meta = extract_image_metadata(str(path))

    assert meta["error"] is None
    assert (meta["width"], meta["height"]) == (64, 48)
    assert meta["camera_model"] == "Pixel 8"
    assert meta["captured_at"] is not None
    assert time.localtime(meta["captured_at"])[:6] == (2024, 3, 15, 10, 30, 45)
    assert meta["latitude"] == pytest.approx(15.2993, abs=1e-4)
    assert meta["longitude"] == pytest.approx(-74.124, abs=1e-4)


def test_image_without_exif_still_gets_dimensions(tmp_path):
    meta = extract_image_metadata(str(make_image(tmp_path / "plain.png", size=(10, 20))))

    assert meta["error"] is None
    assert (meta["width"], meta["height"]) == (10, 20)
    assert meta["captured_at"] is None and meta["latitude"] is None


def test_first_index_stores_images_and_thumbnails(tmp_path):
    make_image(tmp_path / "a.jpg")
    (tmp_path / "nested").mkdir()
    make_image(tmp_path / "nested" / "b.png")
    (tmp_path / "notes.txt").write_text("not an image")

    stats = index_folder(str(tmp_path))

    assert stats.total_files == 3
    assert stats.supported_images == 2
    assert stats.new_images == 2
    assert stats.failed == 0
    assert _statuses() == {"a.jpg": "indexed", "b.png": "indexed"}
    assert len(list(thumbnails_dir().rglob("*.jpg"))) == 2


def test_reindex_skips_unchanged_files(tmp_path):
    make_image(tmp_path / "a.jpg")
    index_folder(str(tmp_path))

    stats = index_folder(str(tmp_path))

    assert stats.unchanged_images == 1
    assert stats.new_images == 0 and stats.changed_images == 0


def test_modified_file_is_reprocessed(tmp_path):
    path = make_image(tmp_path / "a.jpg", size=(64, 48))
    index_folder(str(tmp_path))

    make_image(path, size=(80, 60), color=(0, 0, 255))
    os.utime(path, ns=(time.time_ns(), time.time_ns() + 5_000_000_000))
    stats = index_folder(str(tmp_path))

    assert stats.changed_images == 1
    conn = get_connection()
    width = conn.execute("SELECT width FROM image_metadata").fetchone()[0]
    conn.close()
    assert width == 80


def test_touched_but_identical_file_is_not_reprocessed(tmp_path):
    path = make_image(tmp_path / "a.jpg")
    index_folder(str(tmp_path))

    os.utime(path, ns=(time.time_ns(), time.time_ns() + 5_000_000_000))
    stats = index_folder(str(tmp_path))

    assert stats.unchanged_images == 1 and stats.changed_images == 0


def test_deleted_file_is_marked_missing(tmp_path):
    make_image(tmp_path / "a.jpg")
    path_b = make_image(tmp_path / "b.jpg", color=(1, 2, 3))
    index_folder(str(tmp_path))

    path_b.unlink()
    stats = index_folder(str(tmp_path))

    assert stats.removed_images == 1
    assert _statuses() == {"a.jpg": "indexed", "b.jpg": "missing"}


def test_moved_file_keeps_its_record(tmp_path):
    path = make_image(tmp_path / "a.jpg")
    index_folder(str(tmp_path))
    conn = get_connection()
    original_id = conn.execute("SELECT id FROM files").fetchone()[0]
    conn.close()

    (tmp_path / "trip").mkdir()
    path.rename(tmp_path / "trip" / "a-renamed.jpg")
    stats = index_folder(str(tmp_path))

    conn = get_connection()
    rows = [tuple(r) for r in conn.execute("SELECT id, filename, status FROM files")]
    conn.close()
    assert rows == [(original_id, "a-renamed.jpg", "indexed")]
    assert stats.moved_images == 1
    assert stats.new_images == 0 and stats.removed_images == 0


def test_corrupt_image_is_recorded_as_failed_without_stopping(tmp_path):
    (tmp_path / "broken.jpg").write_bytes(b"this is not a jpeg")
    make_image(tmp_path / "good.jpg")

    stats = index_folder(str(tmp_path))

    assert stats.failed == 1
    assert stats.new_images == 1
    assert _statuses() == {"broken.jpg": "failed", "good.jpg": "indexed"}


def test_missing_root_raises_and_writes_nothing(tmp_path):
    with pytest.raises(FileNotFoundError):
        index_folder(str(tmp_path / "does-not-exist"))

    conn = get_connection()
    assert conn.execute("SELECT COUNT(*) FROM roots").fetchone()[0] == 0
    conn.close()


def test_cancel_stops_early_and_keeps_committed_work(tmp_path):
    for i in range(5):
        make_image(tmp_path / f"{i}.jpg", color=(i, i, i))

    calls = {"n": 0}

    def cancel_after_two():
        calls["n"] += 1
        return calls["n"] > 2

    from app.indexer import IndexCancelled

    with pytest.raises(IndexCancelled):
        index_folder(str(tmp_path), should_cancel=cancel_after_two)

    assert len(_statuses()) == 2
    stats = index_folder(str(tmp_path))  # resuming processes only the remainder
    assert stats.unchanged_images == 2 and stats.new_images == 3
