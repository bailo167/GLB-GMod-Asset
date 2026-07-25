from __future__ import annotations

import math
import struct
from pathlib import Path
from typing import Iterable, Sequence


def _byte(value: float, *, srgb: bool) -> int:
    value = 0.0 if not math.isfinite(value) else max(0.0, min(1.0, float(value)))
    if srgb:
        value = 12.92 * value if value <= 0.0031308 else 1.055 * (value ** (1.0 / 2.4)) - 0.055
    return max(0, min(255, int(round(value * 255.0))))


def write_uncompressed_tga(
    path: Path,
    width: int,
    height: int,
    rgba: Sequence[float] | Iterable[float],
    *,
    srgb: bool,
    include_alpha: bool = True,
) -> None:
    """Write a deterministic uncompressed TGA for Ember Source texture conversion.

    Opaque colour and normal textures are written as 24 bit BGR. Textures that
    genuinely use transparency are written as 32 bit BGRA. Both variants use
    image type 2 and a bottom left origin.
    """
    width = int(width)
    height = int(height)
    if width < 1 or height < 1 or width > 65535 or height > 65535:
        raise ValueError(f"Invalid TGA dimensions: {width}x{height}")
    values = rgba if isinstance(rgba, Sequence) else list(rgba)
    expected = width * height * 4
    if len(values) != expected:
        raise ValueError(f"Expected {expected} RGBA values, received {len(values)}")

    depth = 32 if include_alpha else 24
    alpha_bits = 8 if include_alpha else 0
    header = struct.pack(
        "<BBBHHBHHHHBB",
        0, 0, 2,
        0, 0, 0,
        0, 0,
        width, height,
        depth, alpha_bits,
    )
    stride = 4 if include_alpha else 3
    pixels = bytearray(width * height * stride)
    for pixel in range(width * height):
        source = pixel * 4
        target = pixel * stride
        red = _byte(values[source], srgb=srgb)
        green = _byte(values[source + 1], srgb=srgb)
        blue = _byte(values[source + 2], srgb=srgb)
        pixels[target:target + 3] = bytes((blue, green, red))
        if include_alpha:
            pixels[target + 3] = _byte(values[source + 3], srgb=False)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + pixels)


def inspect_tga(path: Path) -> dict[str, int | bool]:
    data = path.read_bytes()
    if len(data) < 18:
        raise ValueError("TGA header is truncated")
    image_type = data[2]
    width = int.from_bytes(data[12:14], "little")
    height = int.from_bytes(data[14:16], "little")
    depth = data[16]
    descriptor = data[17]
    expected_size = 18 + width * height * (depth // 8)
    return {
        "image_type": image_type,
        "width": width,
        "height": height,
        "depth": depth,
        "alpha_bits": descriptor & 0x0F,
        "top_origin": bool(descriptor & 0x20),
        "expected_size": expected_size,
        "actual_size": len(data),
        "size_matches": expected_size == len(data),
    }
