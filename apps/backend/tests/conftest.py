import pytest


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path_factory, monkeypatch):
    storage = tmp_path_factory.mktemp("cortex-storage")
    monkeypatch.setenv("CORTEX_STORAGE_DIR", str(storage))
    return storage
