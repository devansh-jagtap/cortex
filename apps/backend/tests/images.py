"""Helpers that build small real images (with optional EXIF) for tests."""

from PIL import Image


def make_image(path, size=(64, 48), color=(200, 30, 30), taken=None, gps=None, model=None):
    img = Image.new("RGB", size, color)
    exif = Image.Exif()
    if model:
        exif[0x0110] = model
    # Sub-IFDs must be assigned whole; edits via get_ifd() on a fresh Exif are dropped.
    if taken:
        exif[0x8769] = {0x9003: taken}
    if gps:
        lat, lon = gps
        exif[0x8825] = {
            1: "N" if lat >= 0 else "S",
            2: _dms(abs(lat)),
            3: "E" if lon >= 0 else "W",
            4: _dms(abs(lon)),
        }
    fmt = "PNG" if str(path).lower().endswith(".png") else "JPEG"
    img.save(path, fmt, exif=exif)
    return path


def _dms(value):
    degrees = int(value)
    minutes_full = (value - degrees) * 60
    minutes = int(minutes_full)
    seconds = round((minutes_full - minutes) * 60, 4)
    return (float(degrees), float(minutes), float(seconds))
