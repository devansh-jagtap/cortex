"""Measure the parts of Cortex that grow with the library.

Runs against a throwaway database in a temp folder, never your real index:

    ./venv/Scripts/python.exe scripts/benchmark.py            # vector index only
    ./venv/Scripts/python.exe scripts/benchmark.py --clip     # + real CLIP speed

Reports, for 10k / 50k / 100k photos: how long the search index takes to
load from SQLite at startup, and how long one search takes. With --clip it
also measures embedding throughput and text-query latency on this machine.
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _fill(n: int, dim: int, model: str) -> None:
    from app.database import get_connection

    rng = np.random.default_rng(0)
    conn = get_connection()
    now = time.time()
    conn.execute("INSERT INTO roots (id, path, added_at) VALUES (1, 'bench', ?)", (now,))
    for start in range(0, n, 10_000):
        ids = range(start + 1, min(n, start + 10_000) + 1)
        vectors = rng.standard_normal((len(ids), dim)).astype(np.float32)
        vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
        conn.executemany(
            "INSERT INTO files (id, root_id, path, filename, status, first_seen_at) VALUES (?, 1, ?, ?, 'indexed', ?)",
            [(i, f"bench/{i}.jpg", f"{i}.jpg", now) for i in ids],
        )
        conn.executemany(
            "INSERT INTO embeddings (file_id, model, vector, created_at) VALUES (?, ?, ?, ?)",
            [(i, model, v.tobytes(), now) for i, v in zip(ids, vectors)],
        )
    conn.commit()
    conn.close()


class _BenchEmbedder:
    name = "bench"
    dim = 512

    def embed_text(self, text):
        v = np.random.default_rng(len(text)).standard_normal(self.dim).astype(np.float32)
        return v / np.linalg.norm(v)


def bench_index(sizes: list[int]) -> None:
    from app.search import SearchService

    print(f"{'photos':>8} | {'load at startup':>15} | {'one search (median)':>19}")
    print("-" * 50)
    for n in sizes:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["CORTEX_STORAGE_DIR"] = tmp
            _fill(n, 512, _BenchEmbedder.name)
            service = SearchService(_BenchEmbedder)
            t = time.perf_counter()
            service.sync()
            load = time.perf_counter() - t
            assert service.indexed_vectors() == n
            timings = []
            for q in range(30):
                t = time.perf_counter()
                service.search(f"query {q}", 100)
                timings.append(time.perf_counter() - t)
            print(f"{n:>8,} | {load:>13.2f} s | {statistics.median(timings) * 1000:>16.1f} ms")


def bench_clip() -> None:
    from PIL import Image

    from app.embedder import OpenClipEmbedder

    embedder = OpenClipEmbedder()
    embedder.embed_text("warm up")
    rng = np.random.default_rng(1)
    images = [Image.fromarray(rng.integers(0, 255, (240, 320, 3), dtype=np.uint8)) for _ in range(64)]
    embedder.embed_images(images[:16])  # warm up
    t = time.perf_counter()
    for start in range(0, len(images), embedder.batch_size):
        embedder.embed_images(images[start : start + embedder.batch_size])
    rate = len(images) / (time.perf_counter() - t)
    timings = []
    for q in ["dogs at the beach", "a whiteboard", "sunset over the sea", "a receipt", "mountains"] * 4:
        t = time.perf_counter()
        embedder.embed_text(q)
        timings.append(time.perf_counter() - t)
    print(f"\nCLIP on {embedder.status()['device']}: {rate:.1f} photos/s embedded, "
          f"text query {statistics.median(timings) * 1000:.0f} ms (median)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--clip", action="store_true", help="also measure the real CLIP model")
    parser.add_argument("--sizes", default="10000,50000,100000")
    args = parser.parse_args()
    bench_index([int(s) for s in args.sizes.split(",")])
    if args.clip:
        bench_clip()
