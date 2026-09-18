"""Generate thumbnail images."""

from pathlib import Path

from PIL import Image

THUMBNAIL_SIZE = (320, 320)
THUMBNAIL_DIR = Path(__file__).parent.parent.parent.parent / "storage" / "thumbnails"


def generate_thumbnail(file_path: str, content_hash: str) -> str | None:
    """Generate a thumbnail for an image.

    Args:
        file_path: Path to the original image
        content_hash: Hash of file contents (used as thumbnail id)

    Returns:
        Path to the saved thumbnail, or None if failed
    """
    THUMBNAIL_DIR.mkdir(parents=True, exist_ok=True)

    # Use first 2 chars of hash as subdirectory for better organization
    subdir = THUMBNAIL_DIR / content_hash[:2]
    subdir.mkdir(parents=True, exist_ok=True)
    thumbnail_path = subdir / f"{content_hash}.jpg"

    # If already exists, return it
    if thumbnail_path.exists():
        return str(thumbnail_path)

    try:
        # Use draft() mode to downscale during decode (memory efficient)
        with Image.open(file_path) as img:
            # Draft mode: optimize JPEG decoding to specified size
            if img.format == "JPEG":
                img.draft("RGB", THUMBNAIL_SIZE)

            # Convert to RGB if needed (handle RGBA, etc)
            if img.mode != "RGB":
                img = img.convert("RGB")

            # Resize with high-quality downsampling
            img.thumbnail(THUMBNAIL_SIZE, Image.Resampling.LANCZOS)

            # Save thumbnail
            img.save(str(thumbnail_path), "JPEG", quality=85)
            return str(thumbnail_path)

    except Exception:
        return None
