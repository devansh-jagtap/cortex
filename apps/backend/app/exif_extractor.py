"""Extract EXIF metadata from images."""

import time
from pathlib import Path

from PIL import Image
from PIL.ExifTags import TAGS

try:
    import piexif
except ImportError:
    piexif = None


def extract_image_metadata(file_path: str) -> dict:
    """Extract metadata from an image file.

    Returns a dict with keys:
    - width, height, orientation: image dimensions and rotation
    - captured_at: Unix timestamp when photo was taken (or None)
    - latitude, longitude: GPS location (or None)
    - camera_make, camera_model: camera info (or None)
    - error: error message if extraction failed (or None)
    """
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
        # Get image dimensions and orientation
        with Image.open(file_path) as img:
            result["width"] = img.width
            result["height"] = img.height

            # Check for EXIF orientation
            try:
                exif = img._getexif()
                if exif:
                    for tag_id, value in exif.items():
                        tag_name = TAGS.get(tag_id, tag_id)
                        if tag_name == "Orientation":
                            result["orientation"] = value
            except (AttributeError, IndexError):
                pass

        # Extract EXIF with piexif if available
        if piexif:
            try:
                exif_dict = piexif.load(file_path)

                # Date taken
                if "0th" in exif_dict:
                    for tag, value in exif_dict["0th"].items():
                        tag_name = piexif.TAGS["0th"][tag]["name"].decode()
                        if tag_name == "DateTime":
                            try:
                                dt_str = value.decode() if isinstance(value, bytes) else value
                                # Format: "2024:03:15 10:30:45"
                                dt_obj = time.strptime(dt_str, "%Y:%m:%d %H:%M:%S")
                                result["captured_at"] = time.mktime(dt_obj)
                            except (ValueError, AttributeError):
                                pass
                        elif tag_name in ("Make", "Model"):
                            val = value.decode() if isinstance(value, bytes) else str(value)
                            if tag_name == "Make":
                                result["camera_make"] = val.strip()
                            elif tag_name == "Model":
                                result["camera_model"] = val.strip()

                # GPS coordinates
                if "GPS" in exif_dict and piexif.GPS.GPSLatitude in exif_dict["GPS"]:
                    try:
                        lat = piexif.GPS.GPSLatitude
                        lon = piexif.GPS.GPSLongitude
                        lat_ref = piexif.GPS.GPSLatitudeRef
                        lon_ref = piexif.GPS.GPSLongitudeRef

                        if lat in exif_dict["GPS"] and lon in exif_dict["GPS"]:
                            lat_val = _dms_to_decimal(exif_dict["GPS"][lat])
                            lon_val = _dms_to_decimal(exif_dict["GPS"][lon])

                            if exif_dict["GPS"][lat_ref][0] == ord("S"):
                                lat_val = -lat_val
                            if exif_dict["GPS"][lon_ref][0] == ord("W"):
                                lon_val = -lon_val

                            result["latitude"] = lat_val
                            result["longitude"] = lon_val
                    except (KeyError, IndexError, TypeError):
                        pass
            except Exception:
                pass

    except Exception as e:
        result["error"] = str(e)

    return result


def _dms_to_decimal(dms_tuple):
    """Convert degrees/minutes/seconds to decimal."""
    if len(dms_tuple) != 3:
        return 0.0
    degrees = dms_tuple[0][0] / dms_tuple[0][1] if dms_tuple[0][1] != 0 else 0
    minutes = dms_tuple[1][0] / dms_tuple[1][1] if dms_tuple[1][1] != 0 else 0
    seconds = dms_tuple[2][0] / dms_tuple[2][1] if dms_tuple[2][1] != 0 else 0
    return degrees + minutes / 60 + seconds / 3600
