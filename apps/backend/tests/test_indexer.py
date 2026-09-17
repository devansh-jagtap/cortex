import sqlite3

from PIL import Image

from app.indexer import index_folder


def test_index_folder_persists_and_skips_unchanged_images(tmp_path):
    image_path = tmp_path / "photo.jpg"
    image_path.write_bytes(b"fake-jpeg")
    database_path = tmp_path / "db" / "cortex.sqlite3"
    thumbnail_dir = tmp_path / "thumbs"

    first = index_folder(
        str(tmp_path),
        database_path=database_path,
        thumbnail_dir=thumbnail_dir,
    )
    second = index_folder(
        str(tmp_path),
        database_path=database_path,
        thumbnail_dir=thumbnail_dir,
    )

    assert first.supported_images == 1
    assert first.new_images == 1
    assert second.supported_images == 1
    assert second.new_images == 0
    assert second.unchanged_images == 1

    with sqlite3.connect(database_path) as connection:
        rows = connection.execute("SELECT path, status FROM files").fetchall()

    assert rows == [(str(image_path), "active")]


def test_index_folder_extracts_dimensions_and_creates_thumbnail(tmp_path):
    image_path = tmp_path / "photo.png"
    Image.new("RGB", (640, 480), "red").save(image_path)
    database_path = tmp_path / "db" / "cortex.sqlite3"
    thumbnail_dir = tmp_path / "thumbs"

    result = index_folder(
        str(tmp_path),
        database_path=database_path,
        thumbnail_dir=thumbnail_dir,
    )

    assert result.thumbnails_created == 1
    assert result.indexed_images[0].thumbnail_path is not None

    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            "SELECT width, height FROM image_metadata"
        ).fetchone()

    assert row == (640, 480)


def test_index_folder_marks_missing_images(tmp_path):
    image_path = tmp_path / "photo.jpg"
    image_path.write_bytes(b"fake-jpeg")
    database_path = tmp_path / "db" / "cortex.sqlite3"
    thumbnail_dir = tmp_path / "thumbs"

    index_folder(str(tmp_path), database_path=database_path, thumbnail_dir=thumbnail_dir)
    image_path.unlink()
    result = index_folder(
        str(tmp_path),
        database_path=database_path,
        thumbnail_dir=thumbnail_dir,
    )

    assert result.removed_images == 1

    with sqlite3.connect(database_path) as connection:
        row = connection.execute("SELECT status FROM files").fetchone()

    assert row == ("missing",)
