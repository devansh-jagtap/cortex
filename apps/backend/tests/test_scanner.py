from app.scanner import SUPPORTED_IMAGE_EXTENSIONS, scan_folder


def test_supported_extensions_are_exactly_the_mvp_set():
    assert SUPPORTED_IMAGE_EXTENSIONS == {".jpg", ".jpeg", ".png", ".webp"}


def test_scan_counts_supported_and_unsupported_files(tmp_path):
    (tmp_path / "photo1.jpg").write_bytes(b"fake-jpeg")
    (tmp_path / "photo2.PNG").write_bytes(b"fake-png")  # extension matching is case-insensitive
    (tmp_path / "notes.txt").write_text("hello")
    (tmp_path / "video.mp4").write_bytes(b"fake-video")

    result = scan_folder(str(tmp_path))

    assert result.total_files == 4
    assert result.supported_images == 2
    assert result.unsupported_files == 2
    assert result.errors == []


def test_scan_is_recursive(tmp_path):
    nested = tmp_path / "2024" / "goa"
    nested.mkdir(parents=True)
    (nested / "beach.jpeg").write_bytes(b"fake")
    (tmp_path / "root.webp").write_bytes(b"fake")

    result = scan_folder(str(tmp_path))

    assert result.total_files == 2
    assert result.supported_images == 2


def test_scan_nonexistent_path_reports_error_without_raising():
    result = scan_folder(str("Z:/definitely/does/not/exist/on/this/machine"))

    assert result.total_files == 0
    assert result.supported_images == 0
    assert len(result.errors) == 1
    assert "does not exist" in result.errors[0]


def test_scan_path_that_is_a_file_reports_error(tmp_path):
    file_path = tmp_path / "just_a_file.jpg"
    file_path.write_bytes(b"fake")

    result = scan_folder(str(file_path))

    assert result.total_files == 0
    assert len(result.errors) == 1
    assert "not a directory" in result.errors[0]


def test_scan_one_bad_entry_does_not_abort_the_whole_scan(tmp_path, monkeypatch):
    import os

    (tmp_path / "good.jpg").write_bytes(b"fake")

    real_walk = os.walk

    def flaky_walk(top, onerror=None, **kwargs):
        if onerror:
            onerror(OSError(13, "Permission denied", str(tmp_path / "locked")))
        return real_walk(top, onerror=onerror, **kwargs)

    monkeypatch.setattr(os, "walk", flaky_walk)

    result = scan_folder(str(tmp_path))

    assert result.total_files == 1
    assert result.supported_images == 1
    assert len(result.errors) == 1
