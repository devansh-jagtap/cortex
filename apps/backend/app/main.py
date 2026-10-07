"""Cortex backend — FastAPI application.

Local-only service: filesystem scanning, indexing, and (future) AI search.
Binds to 127.0.0.1 and is only ever talked to by the Electron app on the
same machine.
"""

from __future__ import annotations

import hmac
import json
import os
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
import numpy as np
from pydantic import BaseModel, Field, field_validator

from app.database import get_connection
from app.embedder import get_embedder
from app.graph import GraphBuilder
from app.jobs import IndexJobManager, JobAlreadyRunning
from app.scanner import scan_folder
from app.search import SearchService
from app.storage import thumbnails_dir
from app.watcher import FolderWatcher

search_service = SearchService(get_embedder)
jobs = IndexJobManager(search_service, GraphBuilder(get_embedder))
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

# When the desktop app starts the engine it passes a fresh random secret in
# CORTEX_TOKEN and adds it to every request it makes. Anything else on the
# machine (including web pages open in a browser) then gets 401. Without the
# variable (plain development), the engine is open on 127.0.0.1 as before.
_TOKEN = os.environ.get("CORTEX_TOKEN")


@app.middleware("http")
async def require_token(request: Request, call_next):
    if _TOKEN and request.method != "OPTIONS":
        sent = request.headers.get("x-cortex-token", "")
        if not hmac.compare_digest(sent, _TOKEN):
            return JSONResponse({"detail": "Not allowed"}, status_code=401)
    return await call_next(request)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000", "cortex://app"],
    allow_methods=["GET", "POST", "DELETE"],
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


@app.delete("/roots/{root_id}", status_code=202)
def remove_folder(root_id: int) -> dict:
    """Take a folder out of Cortex. The folder and its photos are not touched."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT path FROM roots WHERE id = ?", (root_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Folder not found")
    try:
        jobs.remove(root_id, row["path"])
    except JobAlreadyRunning:
        raise HTTPException(status_code=409, detail="This folder is being indexed. Cancel that first.")
    watcher.unwatch(row["path"])
    return {"removing": row["path"]}


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


class SearchRequest(BaseModel):
    query: str = Field(max_length=500)
    limit: int = Field(60, ge=1, le=300)

    @field_validator("query")
    @classmethod
    def query_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must not be empty")
        return value.strip()


# Words that carry no visual meaning once a place has been recognised:
# "photos from Goa" is just "Goa".
_FILLER = {
    "photo", "photos", "picture", "pictures", "pic", "pics", "image", "images", "from",
    "in", "at", "of", "taken", "my", "the", "near", "around", "show", "me", "all", "trip", "a",
}
_WORD = re.compile(r"[\w']+")


def _find_place(conn, query: str):
    """The longest known place name in the query, and what's left of it."""
    words = _WORD.findall(query.lower())
    best = None
    for entity in conn.execute("SELECT id, name FROM entities WHERE type = 'place'"):
        name = _WORD.findall(entity["name"].lower())
        for i in range(len(words) - len(name) + 1):
            if name and words[i : i + len(name)] == name and (best is None or len(name) > len(best[1])):
                best = (entity, name, i)
    if best is None:
        return None, query
    entity, name, i = best
    rest = [w for w in words[:i] + words[i + len(name) :] if w not in _FILLER]
    return entity, " ".join(rest)


def _place_search(conn, place, residual: str, limit: int) -> tuple[list[dict], int]:
    ids = [
        r[0]
        for r in conn.execute(
            "SELECT fe.file_id FROM file_entities fe JOIN files f ON f.id = fe.file_id "
            "WHERE fe.entity_id = ? AND f.status = 'indexed'",
            (place["id"],),
        )
    ]
    searched = len(ids)
    scores: dict[int, float] = {}
    if residual and ids:
        query_vector = search_service.embedder.embed_text(residual)
        for start in range(0, len(ids), 900):
            chunk = ids[start : start + 900]
            for r in conn.execute(
                f"SELECT file_id, vector FROM embeddings WHERE model = ? AND file_id IN ({','.join('?' * len(chunk))})",
                (search_service.embedder.name, *chunk),
            ):
                scores[r["file_id"]] = float(np.frombuffer(r["vector"], dtype=np.float32) @ query_vector)
        ids = sorted(scores, key=lambda i: -scores[i])
    ids = ids[:limit]
    if not ids:
        return [], searched
    rows = conn.execute(
        f"SELECT {_IMAGE_COLUMNS} FROM files f JOIN image_metadata m ON m.file_id = f.id "
        f"WHERE f.id IN ({','.join('?' * len(ids))}) "
        "ORDER BY COALESCE(m.captured_at, f.mtime_ns / 1e9) DESC",
        ids,
    ).fetchall()
    items = [dict(r) for r in rows]
    if scores:
        for item in items:
            item["score"] = round(scores.get(item["id"], 0.0), 4)
        items.sort(key=lambda item: -item["score"])
    return items, searched


@app.post("/search")
def search(request: SearchRequest) -> dict:
    started = time.perf_counter()
    conn = get_connection()
    try:
        place, residual = _find_place(conn, request.query)
        if place is not None:
            try:
                results, searched = _place_search(conn, place, residual, request.limit)
            except Exception as exc:
                raise HTTPException(status_code=503, detail=f"The AI model is not available: {exc}")
            return {
                "query": request.query,
                "place": {"id": place["id"], "name": place["name"]},
                "refined_by": residual or None,
                "results": results,
                "took_ms": round((time.perf_counter() - started) * 1000, 1),
                "searched": searched,
            }
    finally:
        conn.close()

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


def _entity_summary(row) -> dict:
    data = json.loads(row["data"] or "{}")
    return {"id": row["id"], "type": row["type"], "name": row["name"], "level": data.get("level"), "data": data}


@app.get("/images/{image_id}/metadata")
def image_metadata(image_id: int) -> dict:
    conn = get_connection()
    try:
        row = conn.execute(
            f"SELECT {_IMAGE_COLUMNS} FROM files f JOIN image_metadata m ON m.file_id = f.id "
            "WHERE f.id = ? AND f.status = 'indexed'",
            (image_id,),
        ).fetchone()
        entities = conn.execute(
            """
            SELECT e.id, e.type, e.name, e.data FROM file_entities fe JOIN entities e ON e.id = fe.entity_id
            WHERE fe.file_id = ?
            ORDER BY CASE e.type WHEN 'place' THEN 0 WHEN 'event' THEN 1 ELSE 2 END, fe.score DESC
            """,
            (image_id,),
        ).fetchall()
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Image not found")
    return {**dict(row), "entities": [_entity_summary(e) for e in entities]}


_ENTITY_COUNT_SQL = """
    SELECT e.id, e.type, e.name, e.data, COUNT(f.id) AS photos
    FROM entities e
    JOIN file_entities fe ON fe.entity_id = e.id
    JOIN files f ON f.id = fe.file_id AND f.status = 'indexed'
"""


@app.get("/entities")
def list_entities(
    type: str | None = Query(None, pattern="^(place|scene|event|person)$"),
    limit: int = Query(200, ge=1, le=2000),
) -> dict:
    conn = get_connection()
    try:
        where = "WHERE e.type = ?" if type else ""
        rows = conn.execute(
            f"{_ENTITY_COUNT_SQL} {where} GROUP BY e.id ORDER BY photos DESC, e.name LIMIT ?",
            (*([type] if type else []), limit),
        ).fetchall()
    finally:
        conn.close()
    return {"items": [{**_entity_summary(r), "photos": r["photos"]} for r in rows]}


# Two photos are linked when they look alike. Measured with ViT-B/32 on a real
# library: unrelated photos score ~0.30, the top 5% of pairs above 0.68, and
# near-duplicates 0.86+. So: link to the closest few at >= 0.70, and always
# to the single closest one at >= 0.55, so a photo joins its group unless it
# is unlike anything else (then it floats alone, its own little galaxy).
_STRONG_LINK = 0.70
_WEAK_LINK = 0.55
_NEIGHBOURS = 4


@app.get("/graph/photos")
def photo_graph(
    limit: int = Query(400, ge=1, le=1000),
    focus: int | None = Query(None, description="This photo and the most similar photos in the library"),
) -> dict:
    """The Galaxy: photos as nodes, joined when they look alike.

    Without `focus`: the newest `limit` photos. With it: one photo and its
    nearest look-alikes from the whole library, so the view can grow around
    whatever the user is exploring instead of drawing everything at once.
    """
    model = search_service.embedder.name
    conn = get_connection()
    try:
        columns = "f.id, f.filename, m.width, m.height, e.vector"
        joins = (
            "FROM files f JOIN image_metadata m ON m.file_id = f.id "
            "JOIN embeddings e ON e.file_id = f.id AND e.model = ? WHERE f.status = 'indexed'"
        )
        total = conn.execute(f"SELECT COUNT(*) {joins}", (model,)).fetchone()[0]
        if focus is None:
            rows = conn.execute(
                f"SELECT {columns} {joins} ORDER BY COALESCE(m.captured_at, f.mtime_ns / 1e9) DESC, f.id DESC LIMIT ?",
                (model, limit),
            ).fetchall()
        else:
            seed = conn.execute(
                "SELECT vector FROM embeddings WHERE file_id = ? AND model = ?", (focus, model)
            ).fetchone()
            if seed is None:
                raise HTTPException(status_code=404, detail="Photo not found")
            hits = search_service.similar(np.frombuffer(seed["vector"], dtype=np.float32), 13)
            ids = list(dict.fromkeys([focus] + [i for i, _ in hits]))
            rows = conn.execute(
                f"SELECT {columns} {joins} AND f.id IN ({','.join('?' * len(ids))})", (model, *ids)
            ).fetchall()
            rows.sort(key=lambda r: ids.index(r["id"]))
        if not rows:
            return {"nodes": [], "edges": [], "clusters": [], "total": total}

        vectors = np.stack([np.frombuffer(r["vector"], dtype=np.float32) for r in rows])
        similarity = vectors @ vectors.T
        np.fill_diagonal(similarity, -1.0)
        links: dict[tuple[int, int], float] = {}
        for i in range(len(rows)):
            for rank, j in enumerate(np.argsort(-similarity[i])[:_NEIGHBOURS]):
                s = float(similarity[i, j])
                if s >= _STRONG_LINK or (rank == 0 and s >= _WEAK_LINK):
                    links[(min(i, int(j)), max(i, int(j)))] = s

        # Groups = connected components, named after their most common scene.
        parent = list(range(len(rows)))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for a, b in links:
            parent[find(a)] = find(b)
        ids = [r["id"] for r in rows]
        scenes = {}
        for chunk_start in range(0, len(ids), 900):
            chunk = ids[chunk_start : chunk_start + 900]
            for r in conn.execute(
                "SELECT fe.file_id, e.name FROM file_entities fe JOIN entities e ON e.id = fe.entity_id "
                f"WHERE e.type = 'scene' AND fe.file_id IN ({','.join('?' * len(chunk))}) ORDER BY fe.score",
                chunk,
            ):
                scenes[r["file_id"]] = r["name"]
    finally:
        conn.close()

    members: dict[int, list[int]] = {}
    for i in range(len(rows)):
        members.setdefault(find(i), []).append(i)
    clusters, cluster_of = [], {}
    for n, (_, group) in enumerate(sorted(members.items(), key=lambda kv: -len(kv[1]))):
        names = [scenes[ids[i]] for i in group if ids[i] in scenes]
        top = max(set(names), key=names.count) if names else None
        label = top if top and len(group) >= 2 and names.count(top) * 2 >= len(group) else None
        clusters.append({"id": n, "size": len(group), "label": label})
        for i in group:
            cluster_of[i] = n
    return {
        "nodes": [
            {"id": r["id"], "filename": r["filename"], "width": r["width"], "height": r["height"], "cluster": cluster_of[i]}
            for i, r in enumerate(rows)
        ],
        "edges": [{"source": ids[a], "target": ids[b], "similarity": round(s, 4)} for (a, b), s in links.items()],
        "clusters": clusters,
        "total": total,
    }


@app.get("/entities/{entity_id}")
def entity_detail(entity_id: int, limit: int = Query(200, ge=1, le=500)) -> dict:
    conn = get_connection()
    try:
        row = conn.execute(f"{_ENTITY_COUNT_SQL} WHERE e.id = ? GROUP BY e.id", (entity_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Not found")
        photos = conn.execute(
            f"""
            SELECT {_IMAGE_COLUMNS} FROM file_entities fe
            JOIN files f ON f.id = fe.file_id AND f.status = 'indexed'
            JOIN image_metadata m ON m.file_id = f.id
            WHERE fe.entity_id = ?
            ORDER BY COALESCE(m.captured_at, f.mtime_ns / 1e9) DESC
            LIMIT ?
            """,
            (entity_id, limit),
        ).fetchall()
        related = conn.execute(
            """
            SELECT e.id, e.type, e.name, e.data, r.kind, r.weight FROM entity_relations r
            JOIN entities e ON e.id = CASE WHEN r.source_id = ? THEN r.target_id ELSE r.source_id END
            WHERE r.source_id = ? OR r.target_id = ?
            ORDER BY r.weight DESC LIMIT 40
            """,
            (entity_id, entity_id, entity_id),
        ).fetchall()
    finally:
        conn.close()
    return {
        **_entity_summary(row),
        "photos_total": row["photos"],
        "photos": [dict(p) for p in photos],
        "related": [{**_entity_summary(r), "kind": r["kind"], "weight": r["weight"]} for r in related],
    }


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
