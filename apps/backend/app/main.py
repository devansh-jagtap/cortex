"""Cortex backend — FastAPI application.

Local-only service: filesystem scanning, indexing, and (future) AI search.
Binds to 127.0.0.1 and is only ever talked to by the Electron app on the
same machine.
"""

from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

from app.database import get_connection
from app.embedder import get_embedder
from app.jobs import IndexJobManager, JobAlreadyRunning
from app.scanner import scan_folder
from app.search import SearchService
from app.storage import thumbnails_dir
from app.watcher import FolderWatcher

search_service = SearchService(get_embedder)
jobs = IndexJobManager(search_service)
watcher = FolderWatcher(jobs.submit_changes)


def _root_paths() -> list[str]:
    conn = get_connection()
    try:
        return [r["path"] for r in conn.execute("SELECT path FROM roots ORDER BY added_at")]
    finally:
        conn.close()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    jobs.recover_interrupted()
    watcher.start()
    for path in _root_paths():
        if os.path.isdir(path):
            watcher.watch(path)
            # Catch up on anything that changed while Cortex was closed.
            # Incremental, so unchanged files are not even read.
            jobs.start(path)
    jobs.request_embedding()  # loads existing vectors into the search index
    yield
    watcher.stop()
    jobs.cancel()


app = FastAPI(title="Cortex Backend", version="0.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


class PathRequest(BaseModel):
    path: str

    @field_validator("path")
    @classmethod
    def path_must_not_be_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("path must not be empty")
        return value


def _normalize(path: str) -> str:
    return os.path.abspath(os.path.expanduser(path.strip()))


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/scan")
def scan(request: PathRequest) -> dict:
    """Fast read-only preview: counts files without indexing anything."""
    return scan_folder(_normalize(request.path)).to_dict()


@app.post("/index/start", status_code=202)
def index_start(request: PathRequest) -> dict:
    path = _normalize(request.path)
    if not os.path.isdir(path):
        raise HTTPException(status_code=400, detail=f"Not a folder: {path}")
    try:
        job_id = jobs.start(path)
    except JobAlreadyRunning:
        raise HTTPException(status_code=409, detail="This folder is already being indexed")
    watcher.watch(path)
    return {"job_id": job_id}


@app.get("/index/status")
def index_status() -> dict:
    model = search_service.embedder.status()
    return {
        **jobs.status(),
        "watching": watcher.watched_roots(),
        "model": {"state": model["state"], "device": model["device"], "error": model["error"]},
    }


@app.get("/roots/suggested")
def suggested_roots() -> dict:
    """The folder "Index this computer" starts from: the user's home folder."""
    return {"home": os.path.expanduser("~")}


@app.post("/index/cancel")
def index_cancel() -> dict:
    return {"cancelled": jobs.cancel()}


@app.get("/library")
def library() -> dict:
    conn = get_connection()
    try:
        images = conn.execute("SELECT COUNT(*) FROM files WHERE status = 'indexed'").fetchone()[0]
        with_gps = conn.execute(
            "SELECT COUNT(*) FROM image_metadata m JOIN files f ON f.id = m.file_id "
            "WHERE f.status = 'indexed' AND m.latitude IS NOT NULL"
        ).fetchone()[0]
        roots = [
            {"id": r["id"], "path": r["path"], "last_scanned_at": r["last_scanned_at"]}
            for r in conn.execute("SELECT * FROM roots ORDER BY added_at")
        ]
    finally:
        conn.close()
    return {"images_indexed": images, "images_with_gps": with_gps, "roots": roots}


_IMAGE_COLUMNS = """
    f.id, f.filename, f.path, f.size, m.width, m.height, m.captured_at,
    m.latitude, m.longitude, m.camera_make, m.camera_model
"""


@app.get("/images")
def list_images(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    ids: str | None = Query(None, description="Comma-separated ids; returns just these photos"),
) -> dict:
    where, params = "f.status = 'indexed'", []
    if ids is not None:
        try:
            wanted = [int(i) for i in ids.split(",") if i.strip()][:500]
        except ValueError:
            raise HTTPException(status_code=422, detail="ids must be comma-separated integers")
        where += f" AND f.id IN ({','.join('?' * len(wanted)) or 'NULL'})"
        params = wanted
    conn = get_connection()
    try:
        total = conn.execute(
            f"SELECT COUNT(*) FROM files f WHERE {where}", params
        ).fetchone()[0]
        rows = conn.execute(
            f"""
            SELECT {_IMAGE_COLUMNS} FROM files f JOIN image_metadata m ON m.file_id = f.id
            WHERE {where}
            ORDER BY COALESCE(m.captured_at, f.mtime_ns / 1e9) DESC, f.id DESC
            LIMIT ? OFFSET ?
            """,
            (*params, limit, offset),
        ).fetchall()
    finally:
        conn.close()
    return {"total": total, "items": [dict(r) for r in rows]}


@app.get("/map/points")
def map_points() -> dict:
    """Every indexed photo with a location, as GeoJSON for the map to cluster."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT f.id, m.latitude, m.longitude FROM files f JOIN image_metadata m ON m.file_id = f.id "
            "WHERE f.status = 'indexed' AND m.latitude IS NOT NULL AND m.longitude IS NOT NULL"
        ).fetchall()
    finally:
        conn.close()
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [r["longitude"], r["latitude"]]},
                "properties": {"id": r["id"]},
            }
            for r in rows
        ],
    }


class SearchRequest(BaseModel):
    query: str = Field(max_length=500)
    limit: int = Field(60, ge=1, le=300)

    @field_validator("query")
    @classmethod
    def query_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must not be empty")
        return value.strip()


@app.post("/search")
def search(request: SearchRequest) -> dict:
    started = time.perf_counter()
    if search_service.indexed_vectors() == 0:
        return {"query": request.query, "results": [], "took_ms": 0, "searched": 0}
    try:
        # Over-fetch: a hit can belong to a file that went missing a moment ago.
        hits = search_service.search(request.query, request.limit * 2)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"The AI model is not available: {exc}")

    scores = dict(hits)
    rows = []
    if scores:
        conn = get_connection()
        try:
            marks = ",".join("?" * len(scores))
            rows = conn.execute(
                f"SELECT {_IMAGE_COLUMNS} FROM files f JOIN image_metadata m ON m.file_id = f.id "
                f"WHERE f.status = 'indexed' AND f.id IN ({marks})",
                tuple(scores),
            ).fetchall()
        finally:
            conn.close()
    results = sorted(({**dict(r), "score": round(scores[r["id"]], 4)} for r in rows), key=lambda r: -r["score"])
    return {
        "query": request.query,
        "results": results[: request.limit],
        "took_ms": round((time.perf_counter() - started) * 1000, 1),
        "searched": search_service.indexed_vectors(),
    }


@app.get("/models/status")
def models_status() -> dict:
    return {
        **search_service.embedder.status(),
        "vectors": search_service.indexed_vectors(),
        "pending": search_service.pending_count(),
    }


@app.get("/images/{image_id}/metadata")
def image_metadata(image_id: int) -> dict:
    conn = get_connection()
    try:
        row = conn.execute(
            f"SELECT {_IMAGE_COLUMNS} FROM files f JOIN image_metadata m ON m.file_id = f.id "
            "WHERE f.id = ? AND f.status = 'indexed'",
            (image_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Image not found")
    return dict(row)


_MEDIA_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


@app.get("/images/{image_id}/original")
def image_original(image_id: int) -> FileResponse:
    """The full-size photo, by id only: the renderer never sends a path."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT path FROM files WHERE id = ? AND status = 'indexed'", (image_id,)
        ).fetchone()
    finally:
        conn.close()
    media_type = _MEDIA_TYPES.get(os.path.splitext(row["path"])[1].lower()) if row else None
    if not row or media_type is None or not os.path.isfile(row["path"]):
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(row["path"], media_type=media_type)


@app.get("/images/{image_id}/thumbnail")
def image_thumbnail(image_id: int) -> FileResponse:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT thumbnail_path FROM image_metadata WHERE file_id = ?", (image_id,)
        ).fetchone()
    finally:
        conn.close()
    if not row or not row["thumbnail_path"]:
        raise HTTPException(status_code=404, detail="Thumbnail not found")

    # Only ever serve files from inside Cortex's own thumbnail directory,
    # even if the database were tampered with.
    thumb = Path(row["thumbnail_path"]).resolve()
    if not thumb.is_relative_to(thumbnails_dir().resolve()) or not thumb.is_file():
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    return FileResponse(thumb, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})
