import os
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.database import get_connection
from app.embedder import get_embedder
from app.indexer import apply_changes, index_folder
from app.jobs import IndexJobManager
from app.search import SearchService, VectorIndex
from tests.images import make_image

client = TestClient(main.app)

RED, GREEN, BLUE = (220, 20, 20), (20, 200, 20), (20, 20, 220)


@pytest.fixture
def service(monkeypatch):
    search = SearchService(get_embedder)
    monkeypatch.setattr(main, "search_service", search)
    monkeypatch.setattr(main, "jobs", IndexJobManager(search))
    return search


def _unit(*xs):
    v = np.array(xs, dtype=np.float32)
    return v / np.linalg.norm(v)


def _embedding_count():
    conn = get_connection()
    try:
        return conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
    finally:
        conn.close()


def test_vector_index_returns_nearest_ids_not_positions():
    index = VectorIndex(dim=3)
    vectors = {40: _unit(1, 0, 0), 7: _unit(0, 1, 0), 99: _unit(0.9, 0.1, 0)}
    index.sync({40: 1.0, 7: 1.0, 99: 1.0}, lambda ids: {i: vectors[i] for i in ids})

    hits = index.search(_unit(1, 0, 0), k=2)

    assert [fid for fid, _ in hits] == [40, 99]
    assert hits[0][1] == pytest.approx(1.0, abs=1e-5)


def test_vector_index_sync_replaces_reembedded_and_drops_removed():
    index = VectorIndex(dim=3)
    store = {1: _unit(1, 0, 0), 2: _unit(0, 1, 0)}
    index.sync({1: 1.0, 2: 1.0}, lambda ids: {i: store[i] for i in ids})

    store[1] = _unit(0, 0, 1)  # file 1 re-embedded with a new timestamp
    added, removed = index.sync({1: 2.0}, lambda ids: {i: store[i] for i in ids})

    assert (added, removed) == (1, 2)
    assert len(index) == 1
    assert index.search(_unit(0, 0, 1), k=5) == [(1, pytest.approx(1.0, abs=1e-5))]


def test_embed_pending_embeds_each_image_once(tmp_path, service, fake_embedder):
    make_image(tmp_path / "a.jpg", color=RED)
    make_image(tmp_path / "b.jpg", color=BLUE)
    index_folder(str(tmp_path))

    first = service.embed_pending()
    second = service.embed_pending()

    assert (first.total, first.processed, first.failed) == (2, 2, 0)
    assert second.total == 0
    assert fake_embedder.images_embedded == 2
    assert _embedding_count() == 2


def test_changed_photo_is_re_embedded(tmp_path, service):
    path = make_image(tmp_path / "a.jpg", color=RED)
    index_folder(str(tmp_path))
    service.embed_pending()
    service.sync()
    assert service.search("red", 1)[0][1] > 0.9

    make_image(path, color=BLUE)
    os.utime(path, ns=(time.time_ns(), time.time_ns() + 5_000_000_000))
    index_folder(str(tmp_path))
    assert service.pending_count() == 1
    service.embed_pending()
    service.sync()

    assert service.search("blue", 1)[0][1] > 0.9


def test_missing_photo_drops_out_of_search(tmp_path, service):
    make_image(tmp_path / "a.jpg", color=RED)
    gone = make_image(tmp_path / "b.jpg", color=GREEN)
    index_folder(str(tmp_path))
    service.embed_pending()
    service.sync()
    assert service.indexed_vectors() == 2

    gone.unlink()
    apply_changes({str(gone)})
    service.sync()

    assert service.indexed_vectors() == 1


def _index_via_api(path):
    assert client.post("/index/start", json={"path": str(path)}).status_code == 202
    assert main.jobs.wait_idle(timeout=30)


def test_search_ranks_by_meaning_end_to_end(tmp_path, service):
    make_image(tmp_path / "IMG_0001.jpg", color=RED)
    make_image(tmp_path / "IMG_0002.jpg", color=GREEN)
    make_image(tmp_path / "IMG_0003.jpg", color=BLUE)
    _index_via_api(tmp_path)

    body = client.post("/search", json={"query": "blue", "limit": 3}).json()

    names = [r["filename"] for r in body["results"]]
    assert names[0] == "IMG_0003.jpg"
    scores = [r["score"] for r in body["results"]]
    assert scores == sorted(scores, reverse=True)
    assert body["searched"] == 3
    assert body["results"][0]["path"].endswith("IMG_0003.jpg")


def test_search_limit_is_respected(tmp_path, service):
    for i in range(6):
        make_image(tmp_path / f"{i}.jpg", color=(i * 40, 10, 10))
    _index_via_api(tmp_path)

    body = client.post("/search", json={"query": "red", "limit": 4}).json()

    assert len(body["results"]) == 4


def test_search_on_empty_library_does_not_load_the_model(service, fake_embedder):
    body = client.post("/search", json={"query": "dogs at the beach"}).json()

    assert body["results"] == []
    assert fake_embedder.texts_embedded == 0


def test_search_rejects_blank_query(service):
    assert client.post("/search", json={"query": "   "}).status_code == 422


def test_models_status_reports_vectors_and_pending(tmp_path, service):
    make_image(tmp_path / "a.jpg", color=RED)
    _index_via_api(tmp_path)

    body = client.get("/models/status").json()

    assert body["state"] == "ready"
    assert body["vectors"] == 1
    assert body["pending"] == 0


def test_watcher_style_changes_are_embedded_automatically(tmp_path, service):
    _index_via_api(tmp_path)
    new = make_image(tmp_path / "new.jpg", color=GREEN)

    main.jobs.submit_changes({str(new)})
    assert main.jobs.wait_idle(timeout=30)

    assert service.indexed_vectors() == 1
    assert client.post("/search", json={"query": "green"}).json()["results"][0]["filename"] == "new.jpg"


@pytest.mark.skipif(not os.environ.get("CORTEX_SLOW_TESTS"), reason="downloads/loads the real CLIP model")
def test_real_clip_model_matches_text_to_images(tmp_path):
    from app.embedder import OpenClipEmbedder, set_embedder

    set_embedder(OpenClipEmbedder())
    make_image(tmp_path / "red.jpg", size=(320, 320), color=RED)
    make_image(tmp_path / "blue.jpg", size=(320, 320), color=BLUE)
    index_folder(str(tmp_path))
    search = SearchService(get_embedder)
    search.embed_pending()
    search.sync()

    conn = get_connection()
    ids = {r["filename"]: r["id"] for r in conn.execute("SELECT id, filename FROM files")}
    conn.close()
    assert search.search("a red square", 1)[0][0] == ids["red.jpg"]
    assert search.search("a blue square", 1)[0][0] == ids["blue.jpg"]
