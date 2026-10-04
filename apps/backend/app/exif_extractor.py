"""Read image dimensions and EXIF metadata. Read-only."""

from __future__ import annotations

import time

from PIL import Image

# EXIF tag ids (see the EXIF 2.3 spec)
_ORIENTATION = 0x0112
_MAKE = 0x010F
_MODEL = 0x0110
_DATETIME = 0x0132
_EXIF_IFD = 0x8769
_GPS_IFD = 0x8825
_DATETIME_ORIGINAL = 0x9003
_GPS_LAT_REF, _GPS_LAT, _GPS_LON_REF, _GPS_LON = 1, 2, 3, 4


def extract_image_metadata(file_path: str) -> dict:
    result = {
        "width": None,
        "height": None,
        "orientation": 1,
        "captured_at": None,
        "latitude": None,
        "longitude": None,
        "camera_make": None,
        "camera_model": None,
        "error": None,
    }

    try:
        with Image.open(file_path) as img:
            result["width"], result["height"] = img.size
            exif = img.getexif()
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result

    try:
        result["orientation"] = int(exif.get(_ORIENTATION, 1))
        result["camera_make"] = _clean_text(exif.get(_MAKE))
        result["camera_model"] = _clean_text(exif.get(_MODEL))

        exif_ifd = exif.get_ifd(_EXIF_IFD)
        result["captured_at"] = _parse_exif_datetime(
            exif_ifd.get(_DATETIME_ORIGINAL) or exif.get(_DATETIME)
        )

        gps = exif.get_ifd(_GPS_IFD)
        if _GPS_LAT in gps and _GPS_LON in gps:
            lat = _dms_to_decimal(gps[_GPS_LAT])
            lon = _dms_to_decimal(gps[_GPS_LON])
            if lat is not None and lon is not None:
                if str(gps.get(_GPS_LAT_REF, "N")).upper().startswith("S"):
                    lat = -lat
                if str(gps.get(_GPS_LON_REF, "E")).upper().startswith("W"):
                    lon = -lon
                if -90 <= lat <= 90 and -180 <= lon <= 180 and (lat, lon) != (0.0, 0.0):
                    result["latitude"], result["longitude"] = lat, lon
    except Exception:
        # Malformed EXIF is common; keep the dimensions we already have.
        pass

    return result


def _clean_text(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="ignore")
    value = str(value).strip("\x00 ").strip()
    return value or None


def _parse_exif_datetime(value) -> float | None:
    text = _clean_text(value)
    if not text:
        return None
    try:
        # EXIF timestamps carry no timezone; they are camera-local time.
        return time.mktime(time.strptime(text[:19], "%Y:%m:%d %H:%M:%S"))
    except (ValueError, OverflowError):
        return None


def _dms_to_decimal(dms) -> float | None:
    try:
        degrees, minutes, seconds = (float(part) for part in dms)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return degrees + minutes / 60 + seconds / 3600
