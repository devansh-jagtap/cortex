import pytest

from app.embedder import set_embedder
from tests.fakes import FakeEmbedder


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path_factory, monkeypatch):
    storage = tmp_path_factory.mktemp("cortex-storage")
    monkeypatch.setenv("CORTEX_STORAGE_DIR", str(storage))
    return storage


@pytest.fixture(autouse=True)
def fake_embedder():
    embedder = FakeEmbedder()
    set_embedder(embedder)
    yield embedder
    set_embedder(None)
