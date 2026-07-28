#!/usr/bin/env python
"""Extract a small, JSON-safe subset of RAW EXIF metadata.

This process is intentionally short-lived.  ExifRead handles the common EXIF
and GPS fields without modifying the source file; rawpy is used only as a
fallback for values that are not exposed by the EXIF parser.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Iterable, Optional

import exifread
import rawpy


MAX_TEXT = 256
METADATA_VERSION = "1"


def _tag(tags: dict[str, Any], *names: str) -> Any:
    for name in names:
        value = tags.get(name)
        if value is not None:
            return value
    return None


def _tag_value(value: Any) -> Any:
    return getattr(value, "values", value)


def _text(value: Any) -> Optional[str]:
    if value is None:
        return None
    printable = getattr(value, "printable", value)
    if isinstance(printable, bytes):
        text = printable.decode("utf-8", errors="replace")
    elif isinstance(printable, (list, tuple)):
        text = ", ".join(str(item) for item in printable)
    else:
        text = str(printable)
    text = text.replace("\x00", "").strip()
    if not text or text.lower() in {"none", "unknown", "undefined"}:
        return None
    return text[:MAX_TEXT]


def _numbers(value: Any) -> list[float]:
    value = _tag_value(value)
    if isinstance(value, (list, tuple)):
        values: Iterable[Any] = value
    else:
        values = (value,)
    result: list[float] = []
    for item in values:
        try:
            if hasattr(item, "decimal"):
                number = float(item.decimal())
            else:
                number = float(item)
        except (TypeError, ValueError, ZeroDivisionError):
            continue
        if math.isfinite(number):
            result.append(number)
    return result


def _number(value: Any) -> Optional[float]:
    values = _numbers(value)
    return values[0] if values else None


def _decimal(value: Optional[float], digits: int = 3) -> Optional[str]:
    if value is None or not math.isfinite(value):
        return None
    text = f"{value:.{digits}f}".rstrip("0").rstrip(".")
    return text or "0"


def _integer(value: Any) -> Optional[int]:
    number = _number(value)
    if number is None:
        return None
    return int(round(number))


def _exposure(value: Any) -> Optional[str]:
    number = _number(value)
    if number is not None and 0 < number < 1:
        denominator = round(1 / number)
        if denominator > 0:
            return f"1/{denominator}"
    return _text(value)


def _gps(tags: dict[str, Any]) -> Optional[dict[str, float]]:
    latitude = _numbers(_tag(tags, "GPS GPSLatitude"))
    longitude = _numbers(_tag(tags, "GPS GPSLongitude"))
    if len(latitude) < 3 or len(longitude) < 3:
        return None
    lat = latitude[0] + latitude[1] / 60 + latitude[2] / 3600
    lon = longitude[0] + longitude[1] / 60 + longitude[2] / 3600
    lat_ref = (_text(_tag(tags, "GPS GPSLatitudeRef")) or "").upper()
    lon_ref = (_text(_tag(tags, "GPS GPSLongitudeRef")) or "").upper()
    if lat_ref.startswith("S"):
        lat = -abs(lat)
    if lon_ref.startswith("W"):
        lon = -abs(lon)

    result: dict[str, float] = {"latitude": round(lat, 7), "longitude": round(lon, 7)}
    altitude = _number(_tag(tags, "GPS GPSAltitude"))
    if altitude is not None:
        altitude_ref = (_text(_tag(tags, "GPS GPSAltitudeRef")) or "").strip()
        if altitude_ref in {"1", "Below sea level"}:
            altitude = -abs(altitude)
        result["altitude_m"] = round(altitude, 2)
    return result


def _raw_value(obj: Any, *names: str) -> Any:
    for name in names:
        value = getattr(obj, name, None)
        if value not in (None, ""):
            return value
    return None


def _extract_exif(tags: dict[str, Any]) -> dict[str, Any]:
    width = _integer(_tag(tags, "EXIF ExifImageWidth", "Image ImageWidth"))
    height = _integer(_tag(tags, "EXIF ExifImageLength", "Image ImageLength"))
    f_number = _number(_tag(tags, "EXIF FNumber"))
    focal_length = _number(_tag(tags, "EXIF FocalLength"))
    focal_35mm = _integer(
        _tag(tags, "EXIF FocalLengthIn35mmFilm", "EXIF FocalLengthIn35mmFormat")
    )
    iso = _integer(
        _tag(tags, "EXIF ISOSpeedRatings", "EXIF PhotographicSensitivity")
    )

    metadata: dict[str, Any] = {
        "camera_make": _text(_tag(tags, "Image Make")),
        "camera_model": _text(_tag(tags, "Image Model")),
        "lens_make": _text(_tag(tags, "EXIF LensMake", "MakerNote LensMake")),
        "lens_model": _text(
            _tag(tags, "EXIF LensModel", "MakerNote LensModel", "MakerNote Lens")
        ),
        "date_time_original": _text(
            _tag(tags, "EXIF DateTimeOriginal", "EXIF DateTimeDigitized", "Image DateTime")
        ),
        "width": width,
        "height": height,
        "exposure_time": _exposure(_tag(tags, "EXIF ExposureTime")),
        "f_number": _decimal(f_number),
        "iso": iso,
        "focal_length_mm": _decimal(focal_length),
        "focal_length_35mm": focal_35mm,
        "gps": _gps(tags),
    }
    return {key: value for key, value in metadata.items() if value is not None}


def _merge_rawpy(metadata: dict[str, Any], source: Path) -> None:
    """Fill gaps from rawpy's small metadata namedtuples when possible."""
    needed = {
        "width",
        "height",
        "exposure_time",
        "f_number",
        "iso",
        "focal_length_mm",
        "focal_length_35mm",
    }
    if not (needed - metadata.keys()) and "lens_model" in metadata:
        return
    try:
        with rawpy.imread(str(source)) as raw:
            other = raw.other
            lens = raw.lens
            sizes = raw.sizes
            if "width" not in metadata:
                metadata["width"] = _raw_value(sizes, "width", "iwidth")
            if "height" not in metadata:
                metadata["height"] = _raw_value(sizes, "height", "iheight")
            if "exposure_time" not in metadata:
                value = _raw_value(other, "shutter")
                metadata["exposure_time"] = _text(value)
            if "f_number" not in metadata:
                value = _raw_value(other, "aperture")
                number = _number(value)
                metadata["f_number"] = _decimal(number) or _text(value)
            if "iso" not in metadata:
                metadata["iso"] = _integer(_raw_value(other, "iso_speed"))
            if "focal_length_mm" not in metadata:
                value = _raw_value(other, "focal_length")
                metadata["focal_length_mm"] = _decimal(_number(value)) or _text(value)
            if "focal_length_35mm" not in metadata:
                metadata["focal_length_35mm"] = _integer(
                    _raw_value(other, "focal_length_35mm")
                )
            if "lens_model" not in metadata:
                metadata["lens_model"] = _text(
                    _raw_value(lens, "model", "name")
                )
            if "lens_make" not in metadata:
                metadata["lens_make"] = _text(_raw_value(lens, "make"))
    except Exception:
        # A RAW can still have useful EXIF even when rawpy cannot unpack it.
        return

    for key in ("width", "height", "iso", "focal_length_35mm"):
        if key in metadata:
            try:
                metadata[key] = int(metadata[key])
            except (TypeError, ValueError):
                metadata.pop(key, None)
    for key in list(metadata):
        if metadata[key] in (None, ""):
            metadata.pop(key, None)


def extract(source: Path) -> dict[str, Any]:
    tags: dict[str, Any] = {}
    try:
        with source.open("rb") as handle:
            tags = exifread.process_file(
                handle,
                details=False,
                extract_thumbnail=False,
                truncate_tags=True,
                builtin_types=False,
            )
    except Exception:
        tags = {}
    metadata = _extract_exif(tags)
    _merge_rawpy(metadata, source)
    metadata["metadata_version"] = METADATA_VERSION
    return metadata


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    try:
        payload = extract(args.source)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
        print(f"metadata:{len(payload)}")
        return 0
    except Exception as exc:  # noqa: BLE001 - parent process remains alive
        print(f"metadata failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
