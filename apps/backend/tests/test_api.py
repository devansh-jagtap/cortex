import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.embedder import get_embedder
from app.jobs import IndexJobManager
from app.main import app
from app.search import SearchService

client = TestClient(app)


@pytest.fixture(autouse=True)
def fresh_services(monkeypatch):
    """Each test gets its own search index and worker, like a fresh app start."""
    search = SearchService(get_embedder)
    monkeypatch.setattr(main, "search_service", search)
    monkeypatch.setattr(main, "jobs", IndexJobManager(search))


def test_health_returns_ok():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_scan_endpoint_returns_counts_for_real_folder(tmp_path):
    (tmp_path / "a.jpg").write_bytes(b"fake")
    (tmp_path / "b.txt").write_text("not an image")

    response = client.post("/scan", json={"path": str(tmp_path)})

    assert response.status_code == 200
    body = response.json()
    assert body["total_files"] == 2
    assert body["supported_images"] == 1
    assert body["unsupported_files"] == 1


def test_scan_endpoint_rejects_empty_path():
    response = client.post("/scan", json={"path": ""})

    assert response.status_code == 422


def test_scan_endpoint_handles_missing_folder_gracefully():
    response = client.post("/scan", json={"path": "Z:/no/such/folder/here"})

    assert response.status_code == 200
    body = response.json()
    assert body["error_count"] == 1


def _run_index(path):
    response = client.post("/index/start", json={"path": str(path)})
    assert response.status_code == 202
    assert main.jobs.wait_idle(timeout=30)
    return client.get("/index/status").json()


def test_index_job_runs_in_background_and_reports_done(tmp_path):
    from tests.images import make_image

    make_image(tmp_path / "a.jpg", gps=(12.97, 77.59))
    make_image(tmp_path / "b.jpg", color=(0, 90, 0))

    status = _run_index(tmp_path)

    assert status["state"] == "done"
    assert status["stats"]["processed"] == 2
    assert status["stats"]["new_images"] == 2

    library = client.get("/library").json()
    assert library["images_indexed"] == 2
    assert library["images_with_gps"] == 1


def test_index_start_rejects_missing_folder():
    response = client.post("/index/start", json={"path": "Z:/no/such/folder/here"})

    assert response.status_code == 400


def test_images_list_metadata_and_thumbnail(tmp_path):
    from tests.images import make_image

    make_image(tmp_path / "a.jpg", size=(640, 480), model="Pixel 8")
    _run_index(tmp_path)

    listing = client.get("/images").json()
    assert listing["total"] == 1
    image = listing["items"][0]
    assert (image["width"], image["height"]) == (640, 480)

    meta = client.get(f"/images/{image['id']}/metadata").json()
    assert meta["camera_model"] == "Pixel 8"

    thumb = client.get(f"/images/{image['id']}/thumbnail")
    assert thumb.status_code == 200
    assert thumb.headers["content-type"] == "image/jpeg"
    assert thumb.content[:2] == b"\xff\xd8"


def test_unknown_image_returns_404():
    assert client.get("/images/999999/thumbnail").status_code == 404
    assert client.get("/images/999999/metadata").status_code == 404


def test_thumbnail_outside_storage_is_never_served(tmp_path):
    from app.database import get_connection
    from tests.images import make_image

    make_image(tmp_path / "a.jpg")
    _run_index(tmp_path)
    image_id = client.get("/images").json()["items"][0]["id"]

    secret = tmp_path / "secret.jpg"
    secret.write_bytes(b"\xff\xd8 not for the renderer")
    conn = get_connection()
    conn.execute("UPDATE image_metadata SET thumbnail_path = ? WHERE file_id = ?", (str(secret), image_id))
    conn.commit()
    conn.close()

    assert client.get(f"/images/{image_id}/thumbnail").status_code == 404


def test_progress_saved_on_every_file_does_not_deadlock(tmp_path, monkeypatch):
    import app.jobs
    from tests.images import make_image

    monkeypatch.setattr(app.jobs, "PERSIST_EVERY_SECONDS", 0)
    for i in range(5):
        make_image(tmp_path / f"{i}.jpg", color=(i * 40, 0, 0))

    status = _run_index(tmp_path)

    assert status["state"] == "done", status["error"]
    assert client.get("/library").json()["images_indexed"] == 5


def test_job_left_running_by_a_crash_is_reported_interrupted(tmp_path):
    import time

    from app.database import get_connection
    from app.jobs import IndexJobManager

    conn = get_connection()
    root_id = conn.execute(
        "INSERT INTO roots (path, added_at) VALUES (?, ?)", (str(tmp_path), time.time())
    ).lastrowid
    conn.execute(
        "INSERT INTO index_jobs (root_id, state, total, processed, started_at) VALUES (?, 'running', 10, 4, ?)",
        (root_id, time.time()),
    )
    conn.commit()
    conn.close()

    fresh = IndexJobManager()  # what a restarted backend sees
    fresh.recover_interrupted()
    status = fresh.status()

    assert status["state"] == "interrupted"
    assert status["stats"]["root_path"] == str(tmp_path)
    assert status["stats"]["processed"] == 4


def test_original_is_served_by_id_and_404s_once_deleted(tmp_path):
    from tests.images import make_image

    path = make_image(tmp_path / "a.jpg", size=(640, 480))
    _run_index(tmp_path)
    image_id = client.get("/images").json()["items"][0]["id"]

    response = client.get(f"/images/{image_id}/original")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content == path.read_bytes()

    path.unlink()
    assert client.get(f"/images/{image_id}/original").status_code == 404


def test_status_includes_model_state():
    body = client.get("/index/status").json()

    assert body["model"]["state"] == "ready"


def test_map_points_include_only_located_present_photos(tmp_path):
    from tests.images import make_image

    make_image(tmp_path / "goa.jpg", gps=(15.5, 73.8))
    gone = make_image(tmp_path / "delhi.jpg", gps=(28.6, 77.2), color=(1, 2, 3))
    make_image(tmp_path / "no_gps.jpg", color=(4, 5, 6))
    _run_index(tmp_path)
    gone.unlink()
    from app.indexer import apply_changes

    apply_changes({str(gone)})

    body = client.get("/map/points").json()

    assert body["type"] == "FeatureCollection"
    assert len(body["features"]) == 1
    lon, lat = body["features"][0]["geometry"]["coordinates"]
    assert (round(lat, 1), round(lon, 1)) == (15.5, 73.8)


def test_images_can_be_fetched_by_ids(tmp_path):
    from tests.images import make_image

    for i in range(4):
        make_image(tmp_path / f"{i}.jpg", color=(i * 50, 0, 0))
    _run_index(tmp_path)
    all_ids = [item["id"] for item in client.get("/images").json()["items"]]

    body = client.get(f"/images?ids={all_ids[0]},{all_ids[2]}").json()

    assert body["total"] == 2
    assert sorted(item["id"] for item in body["items"]) == sorted([all_ids[0], all_ids[2]])
    assert client.get("/images?ids=1,abc").status_code == 422
    assert client.get("/images?ids=").json()["total"] == 0


def test_removing_a_folder_takes_its_photos_out_of_library_and_search(tmp_path):
    from tests.images import make_image

    keep, gone = tmp_path / "keep", tmp_path / "gone"
    keep.mkdir()
    gone.mkdir()
    make_image(keep / "red.jpg", color=(220, 20, 20))
    make_image(gone / "blue.jpg", color=(20, 20, 220))
    _run_index(keep)
    _run_index(gone)
    roots = {r["path"]: r["id"] for r in client.get("/library").json()["roots"]}

    response = client.delete(f"/roots/{roots[str(gone)]}")
    assert response.status_code == 202
    assert main.jobs.wait_idle(timeout=30)

    library = client.get("/library").json()
    assert [r["path"] for r in library["roots"]] == [str(keep)]
    assert library["images_indexed"] == 1
    results = client.post("/search", json={"query": "blue"}).json()["results"]
    assert [r["filename"] for r in results] == ["red.jpg"]
    assert (gone / "blue.jpg").exists()
    assert client.delete("/roots/999999").status_code == 404
