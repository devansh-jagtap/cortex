from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


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
