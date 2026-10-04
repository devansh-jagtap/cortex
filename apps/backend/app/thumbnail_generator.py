"""Generate small JPEG previews. Writes only inside Cortex's storage dir."""

from __future__ import annotations

from PIL import Image, ImageOps

from app.storage import thumbnails_dir

THUMBNAIL_SIZE = (320, 320)


def thumbnail_path_for(content_hash: str):
    return thumbnails_dir() / content_hash[:2] / f"{content_hash}.jpg"


def generate_thumbnail(file_path: str, content_hash: str) -> str | None:
    target = thumbnail_path_for(content_hash)
    if target.exists():
        return str(target)

    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with Image.open(file_path) as img:
            if img.format == "JPEG":
                img.draft("RGB", (THUMBNAIL_SIZE[0] * 2, THUMBNAIL_SIZE[1] * 2))
            img = ImageOps.exif_transpose(img)
            if img.mode != "RGB":
                img = img.convert("RGB")
            img.thumbnail(THUMBNAIL_SIZE, Image.Resampling.LANCZOS)
            tmp = target.with_suffix(".tmp")
            img.save(tmp, "JPEG", quality=85)
            tmp.replace(target)
        return str(target)
    except Exception:
        return None
