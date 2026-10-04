"""Turns images and text into vectors in the same space (CLIP).

A photo and a sentence describing it land close together, which is what
makes "dogs at the beach" find a photo named IMG_4821.jpg. The model is
loaded lazily on first use, exactly once, behind a lock; the first load
downloads the weights (~600 MB) into `models/`.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Protocol

import numpy as np
from PIL import Image

from app.storage import models_dir

log = logging.getLogger("cortex.embedder")

MODEL_ARCH = "ViT-B-32"
MODEL_PRETRAINED = "laion2b_s34b_b79k"


class Embedder(Protocol):
    name: str
    dim: int

    def embed_images(self, images: list[Image.Image]) -> np.ndarray:
        """(n, dim) float32, each row L2-normalised."""

    def embed_text(self, text: str) -> np.ndarray:
        """(dim,) float32, L2-normalised."""

    def status(self) -> dict: ...


class OpenClipEmbedder:
    name = f"openclip-{MODEL_ARCH}-{MODEL_PRETRAINED}".lower()
    dim = 512

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._model = None
        self._preprocess = None
        self._tokenizer = None
        self._device = None
        self._state = "not_loaded"
        self._error: str | None = None

    @property
    def batch_size(self) -> int:
        return 32 if self._device == "cuda" else 16

    def status(self) -> dict:
        return {
            "name": self.name,
            "state": self._state,
            "device": self._device,
            "downloaded": self._state == "ready" or self._weights_present(),
            "error": self._error,
        }

    def embed_images(self, images: list[Image.Image]) -> np.ndarray:
        import torch

        self._ensure_loaded()
        batch = torch.stack([self._preprocess(img) for img in images]).to(self._device)
        with torch.inference_mode():
            if self._device == "cuda":
                with torch.autocast("cuda", dtype=torch.float16):
                    features = self._model.encode_image(batch)
            else:
                features = self._model.encode_image(batch)
        return _normalise(features.float().cpu().numpy())

    def embed_text(self, text: str) -> np.ndarray:
        import torch

        self._ensure_loaded()
        tokens = self._tokenizer([text]).to(self._device)
        with torch.inference_mode():
            features = self._model.encode_text(tokens)
        return _normalise(features.float().cpu().numpy())[0]

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:
                return
            import open_clip
            import torch

            self._state = "downloading" if not self._weights_present() else "loading"
            self._error = None
            try:
                device = "cuda" if torch.cuda.is_available() else "cpu"
                if device == "cpu":
                    # Leave half the cores for the rest of the computer.
                    torch.set_num_threads(max(2, (os.cpu_count() or 4) // 2))
                model, _, preprocess = open_clip.create_model_and_transforms(
                    MODEL_ARCH,
                    pretrained=MODEL_PRETRAINED,
                    cache_dir=str(models_dir()),
                    device=device,
                )
                model.eval()
                self._tokenizer = open_clip.get_tokenizer(MODEL_ARCH)
                self._preprocess = preprocess
                self._device = device
                self._model = model
                self._state = "ready"
                log.info("loaded %s on %s", self.name, device)
            except Exception as exc:
                self._state = "error"
                self._error = f"{type(exc).__name__}: {exc}"
                raise

    def _weights_present(self) -> bool:
        root = models_dir()
        return root.exists() and any(
            p.name.startswith("open_clip") and p.stat().st_size > 1_000_000
            for p in root.rglob("open_clip*")
        )


def _normalise(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return (vectors / np.maximum(norms, 1e-12)).astype(np.float32)


_embedder: Embedder | None = None
_embedder_lock = threading.Lock()


def get_embedder() -> Embedder:
    global _embedder
    with _embedder_lock:
        if _embedder is None:
            _embedder = OpenClipEmbedder()
        return _embedder


def set_embedder(embedder: Embedder | None) -> None:
    global _embedder
    with _embedder_lock:
        _embedder = embedder
