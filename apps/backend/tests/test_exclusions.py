import os

from app.exclusions import is_excluded, skip_dir


def test_dev_and_app_data_folders_are_skipped_anywhere():
    for name in ["node_modules", ".git", "AppData", "__pycache__", "venv", ".cache", "site-packages"]:
        assert skip_dir(r"C:\Users\me\projects", name), name


def test_normal_photo_folders_are_kept():
    for name in ["Pictures", "Goa Trip 2024", "DCIM", "Camera Roll", "Windows Photos"]:
        assert not skip_dir(r"C:\Users\me", name), name


def test_system_folders_are_skipped_only_at_a_drive_root():
    assert skip_dir("C:\\", "Windows")
    assert skip_dir("D:\\", "Program Files")
    assert not skip_dir(r"C:\Users\me\Pictures", "Windows")


def test_cortex_never_indexes_its_own_storage(isolated_storage):
    parent, name = os.path.split(str(isolated_storage))
    assert skip_dir(parent, name)


def test_is_excluded_checks_only_folders_below_the_root():
    root = r"C:\Users\me\AppData\Local\Temp\photos"
    assert not is_excluded(root + r"\a.jpg", root)
    assert not is_excluded(root + r"\trip\a.jpg", root)
    assert is_excluded(root + r"\node_modules\pkg\icon.png", root)
    assert is_excluded(root + r"\.thumbs\a.jpg", root)


def test_paths_outside_the_root_are_excluded():
    assert is_excluded(r"C:\elsewhere\a.jpg", r"C:\Users\me\Pictures")
