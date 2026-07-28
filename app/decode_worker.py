#!/usr/local/bin/python
"""Decode one RAW file to a bounded JPEG preview.

This process is intentionally short-lived. The HTTP parent starts it for a
cache miss so a problematic camera file cannot take down the long-running
preview service.
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

from PIL import Image, ImageOps

import rawpy


MAX_EDGE_DEFAULT = 2048
MIN_EMBEDDED_EDGE_DEFAULT = 1024
JPEG_QUALITY = 85
Image.MAX_IMAGE_PIXELS = 120_000_000


def image_from_thumbnail(thumb: rawpy.Thumbnail) -> Image.Image:
    if thumb.format == rawpy.ThumbFormat.JPEG:
        with Image.open(io.BytesIO(thumb.data)) as image:
            image.load()
            return image.copy()
    if thumb.format == rawpy.ThumbFormat.BITMAP:
        return Image.fromarray(thumb.data)
    raise ValueError(f"unsupported embedded thumbnail format: {thumb.format!r}")


def fit_image(image: Image.Image, max_edge: int) -> Image.Image:
    image = ImageOps.exif_transpose(image)
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    if max(image.size) > max_edge:
        image.thumbnail((max_edge, max_edge), Image.Resampling.BILINEAR)
    return image.convert("RGB")


def save_jpeg(image: Image.Image, output: Path, max_edge: int, mode: str) -> str:
    image = fit_image(image, max_edge)
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(
        output,
        format="JPEG",
        quality=JPEG_QUALITY,
        optimize=False,
        progressive=False,
        subsampling=2,
    )
    return mode


def decode(source: Path, output: Path, max_edge: int, min_embedded_edge: int) -> str:
    # Embedded previews are the fast path and normally already contain the
    # camera's intended rendering and orientation metadata.
    try:
        with rawpy.imread(str(source)) as raw:
            thumb = raw.extract_thumb()
            image = image_from_thumbnail(thumb)
            if max(image.size) >= min_embedded_edge:
                return save_jpeg(image, output, max_edge, "embedded-jpeg")
    except (
        rawpy.LibRawNoThumbnailError,
        rawpy.LibRawUnsupportedThumbnailError,
        rawpy.LibRawError,
        OSError,
        ValueError,
    ):
        pass

    # Fallback: four times fewer pixels than the sensor image, with the
    # lowest-cost demosaic algorithm suitable for a quick browser preview.
    with rawpy.imread(str(source)) as raw:
        rgb = raw.postprocess(
            half_size=True,
            demosaic_algorithm=rawpy.DemosaicAlgorithm.LINEAR,
            use_camera_wb=True,
            output_color=rawpy.ColorSpace.sRGB,
            output_bps=8,
            no_auto_bright=False,
        )
    return save_jpeg(Image.fromarray(rgb), output, max_edge, "libraw-linear")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--max-edge", type=int, default=MAX_EDGE_DEFAULT)
    parser.add_argument(
        "--min-embedded-edge",
        type=int,
        default=MIN_EMBEDDED_EDGE_DEFAULT,
    )
    args = parser.parse_args()

    try:
        mode = decode(
            args.source,
            args.output,
            max(256, args.max_edge),
            max(0, args.min_embedded_edge),
        )
        print(mode)
        return 0
    except Exception as exc:  # noqa: BLE001 - sent to the parent as a safe error
        print(f"decode failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
