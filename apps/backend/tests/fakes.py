"""A deterministic stand-in for CLIP, so tests need no model download.

It places images by their average colour and text by the colour words it
contains, in the same space, so "red" really is closest to red images and
ranking can be tested end to end.
"""

import numpy as np

COLOURS = {
    "red": (1.0, 0.0, 0.0),
    "green": (0.0, 1.0, 0.0),
    "blue": (0.0, 0.0, 1.0),
    "white": (1.0, 1.0, 1.0),
    "black": (0.0, 0.0, 0.0),
}


class FakeEmbedder:
    name = "fake-colour"
    dim = 8
    batch_size = 4

    def __init__(self):
        self.images_embedded = 0
        self.texts_embedded = 0

    def embed_images(self, images):
        self.images_embedded += len(images)
        rows = [np.asarray(img.convert("RGB"), dtype=np.float32).mean(axis=(0, 1)) / 255 for img in images]
        return np.stack([_vector(rgb) for rgb in rows])

    def embed_text(self, text):
        self.texts_embedded += 1
        hits = [COLOURS[w] for w in text.lower().split() if w in COLOURS]
        rgb = np.mean(hits, axis=0) if hits else np.array([0.5, 0.5, 0.5])
        return _vector(rgb)

    def status(self):
        return {"name": self.name, "state": "ready", "device": "cpu", "downloaded": True, "error": None}


def _vector(rgb):
    v = np.zeros(8, dtype=np.float32)
    v[:3] = np.asarray(rgb, dtype=np.float32) - 0.5
    v[3] = 0.05
    return v / np.linalg.norm(v)
