"""Cortex backend — FastAPI application.

Local-only service: filesystem scanning, (future) AI embeddings, FAISS
search, and SQLite metadata. Binds to 127.0.0.1 and is only ever talked
to by the trusted Electron app running on the same machine.
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator

from app.scanner import scan_folder

app = FastAPI(title="Cortex Backend", version="0.1.0")

# The renderer (Next.js dev server / packaged app) talks to this API
# directly over HTTP on localhost. Restrict to the known dev origins.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


class ScanRequest(BaseModel):
    path: str

    @field_validator("path")
    @classmethod
    def path_must_not_be_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("path must not be empty")
        return value


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/scan")
def scan(request: ScanRequest) -> dict:
    normalized_path = os.path.abspath(os.path.expanduser(request.path))
    result = scan_folder(normalized_path)
    return result.to_dict()
