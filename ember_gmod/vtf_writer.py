from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path


VTF_SIGNATURE = b"VTF\x00"
VTF_MAJOR = 7
VTF_MINOR = 2
VTF_HEADER_SIZE = 80
IMAGE_FORMAT_BGRA8888 = 12
IMAGE_FORMAT_DXT1 = 13
TEXTUREFLAGS_SRGB = 0x00000040
TEXTUREFLAGS_NORMAL = 0x00000080
TEXTUREFLAGS_NOMIP = 0x00000100
TEXTUREFLAGS_NOLOD = 0x00000200
TEXTUREFLAGS_EIGHTBITALPHA = 0x00002000


@dataclass(frozen=True)
class TgaImage:
    width: int
    height: int
    pixels_bgra: bytes
    has_alpha: bool


@dataclass(frozen=True)
class VtfInfo:
    width: int
    height: int
    flags: int
    image_format: int
    mip_count: int
    low_res_format: int
    low_res_width: int
    low_res_height: int
    depth: int
    image_offset: int
    image_size: int


def _is_power_of_two(value: int) -> bool:
    return value > 0 and (value & (value - 1)) == 0


def read_uncompressed_tga(path: Path) -> TgaImage:
    data = path.read_bytes()
    if len(data) < 18:
        raise ValueError(f"{path.name}: TGA header is truncated")

    id_length = data[0]
    color_map_type = data[1]
    image_type = data[2]
    width = int.from_bytes(data[12:14], "little")
    height = int.from_bytes(data[14:16], "little")
    pixel_depth = data[16]
    descriptor = data[17]

    if color_map_type != 0:
        raise ValueError(f"{path.name}: colour mapped TGA files are unsupported")
    if image_type != 2:
        raise ValueError(f"{path.name}: expected uncompressed true colour TGA type 2, got {image_type}")
    if pixel_depth not in (24, 32):
        raise ValueError(f"{path.name}: expected 24 or 32 bit TGA, got {pixel_depth}")
    if not (_is_power_of_two(width) and _is_power_of_two(height)):
        raise ValueError(f"{path.name}: dimensions must be powers of two, got {width}x{height}")
    if width > 2048 or height > 2048:
        raise ValueError(f"{path.name}: dimensions exceed 2048, got {width}x{height}")

    bytes_per_pixel = pixel_depth // 8
    pixel_offset = 18 + id_length
    pixel_size = width * height * bytes_per_pixel
    if len(data) < pixel_offset + pixel_size:
        raise ValueError(
            f"{path.name}: pixel data is truncated, expected {pixel_size} bytes from offset {pixel_offset}"
        )
    raw = memoryview(data)[pixel_offset : pixel_offset + pixel_size]

    top_origin = bool(descriptor & 0x20)
    right_origin = bool(descriptor & 0x10)
    row_stride = width * bytes_per_pixel
    out = bytearray(width * height * 4)
    any_transparency = False

    for output_y in range(height):
        source_y = output_y if top_origin else height - 1 - output_y
        row = raw[source_y * row_stride : (source_y + 1) * row_stride]
        for output_x in range(width):
            source_x = width - 1 - output_x if right_origin else output_x
            source_index = source_x * bytes_per_pixel
            destination_index = (output_y * width + output_x) * 4
            b = row[source_index]
            g = row[source_index + 1]
            r = row[source_index + 2]
            a = row[source_index + 3] if bytes_per_pixel == 4 else 255
            out[destination_index : destination_index + 4] = bytes((b, g, r, a))
            if a != 255:
                any_transparency = True

    return TgaImage(width, height, bytes(out), any_transparency)


def _rgb565(r: int, g: int, b: int) -> int:
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


def _solid_dxt1_thumbnail(r: int, g: int, b: int) -> bytes:
    colour0 = _rgb565(r, g, b)
    if colour0 == 0:
        colour0 = 1
    colour1 = 0
    return struct.pack("<HHI", colour0, colour1, 0)


def _average_rgb(pixels_bgra: bytes) -> tuple[float, float, float, int, int, int]:
    count = len(pixels_bgra) // 4
    if count <= 0:
        return 0.5, 0.5, 0.5, 128, 128, 128
    # Slice sums are implemented in C and remain quick for 2048 square textures.
    b_total = sum(pixels_bgra[0::4])
    g_total = sum(pixels_bgra[1::4])
    r_total = sum(pixels_bgra[2::4])
    r8 = int(round(r_total / count))
    g8 = int(round(g_total / count))
    b8 = int(round(b_total / count))
    scale = 1.0 / (count * 255.0)
    return r_total * scale, g_total * scale, b_total * scale, r8, g8, b8


def expected_mip_count(width: int, height: int) -> int:
    count = 1
    while width > 1 or height > 1:
        width = max(1, width // 2)
        height = max(1, height // 2)
        count += 1
    return count


def _downsample_bgra(pixels: bytes, width: int, height: int) -> tuple[bytes, int, int]:
    """Box filter one mip level down, averaging 2x2 blocks per channel."""
    new_width = max(1, width // 2)
    new_height = max(1, height // 2)
    out = bytearray(new_width * new_height * 4)
    for y in range(new_height):
        y0 = min(2 * y, height - 1) * width
        y1 = min(2 * y + 1, height - 1) * width
        for x in range(new_width):
            x0 = min(2 * x, width - 1)
            x1 = min(2 * x + 1, width - 1)
            a = (y0 + x0) * 4
            b = (y0 + x1) * 4
            c = (y1 + x0) * 4
            d = (y1 + x1) * 4
            target = (y * new_width + x) * 4
            for channel in range(4):
                out[target + channel] = (
                    pixels[a + channel] + pixels[b + channel] + pixels[c + channel] + pixels[d + channel] + 2
                ) >> 2
    return bytes(out), new_width, new_height


def build_mip_chain(pixels: bytes, width: int, height: int) -> list[tuple[bytes, int, int]]:
    """Full mip chain, largest level first."""
    chain = [(pixels, width, height)]
    while width > 1 or height > 1:
        pixels, width, height = _downsample_bgra(pixels, width, height)
        chain.append((pixels, width, height))
    return chain


def build_vtf(image: TgaImage, *, normal_map: bool = False) -> bytes:
    # A full mip chain is required for a texture that is ever seen at distance.
    # The v2.2.x atlas is thousands of small UV islands; sampling it without
    # mips made distant player models dissolve into per-texel noise, because the
    # GPU had no prefiltered levels and picked essentially arbitrary texels.
    flags = 0
    if normal_map:
        flags |= TEXTUREFLAGS_NORMAL
    else:
        flags |= TEXTUREFLAGS_SRGB
    if image.has_alpha:
        flags |= TEXTUREFLAGS_EIGHTBITALPHA

    chain = build_mip_chain(image.pixels_bgra, image.width, image.height)
    reflect_r, reflect_g, reflect_b, average_r, average_g, average_b = _average_rgb(image.pixels_bgra)
    header = bytearray(VTF_HEADER_SIZE)
    struct.pack_into("<4sIII", header, 0, VTF_SIGNATURE, VTF_MAJOR, VTF_MINOR, VTF_HEADER_SIZE)
    struct.pack_into("<HHIHH", header, 16, image.width, image.height, flags, 1, 0)
    # Bytes 28 to 31 are the documented alignment pad before reflectivity.
    struct.pack_into("<3f", header, 32, reflect_r, reflect_g, reflect_b)
    # Bytes 44 to 47 are the documented alignment pad after reflectivity.
    struct.pack_into("<fI", header, 48, 1.0, IMAGE_FORMAT_BGRA8888)
    struct.pack_into("<B", header, 56, len(chain))
    struct.pack_into("<I", header, 57, IMAGE_FORMAT_DXT1)
    struct.pack_into("<BBH", header, 61, 4, 4, 1)

    thumbnail = _solid_dxt1_thumbnail(average_r, average_g, average_b)
    # VTF stores mip levels smallest first, each followed by the next larger.
    body = b"".join(level_pixels for level_pixels, _w, _h in reversed(chain))
    return bytes(header) + thumbnail + body


def inspect_vtf(path: Path) -> VtfInfo:
    data = path.read_bytes()
    if len(data) < VTF_HEADER_SIZE:
        raise ValueError(f"{path.name}: VTF is shorter than the 80 byte header")
    signature, major, minor, header_size = struct.unpack_from("<4sIII", data, 0)
    if signature != VTF_SIGNATURE:
        raise ValueError(f"{path.name}: missing VTF signature")
    if (major, minor) != (VTF_MAJOR, VTF_MINOR):
        raise ValueError(f"{path.name}: unsupported VTF version {major}.{minor}")
    if header_size != VTF_HEADER_SIZE:
        raise ValueError(f"{path.name}: unexpected header size {header_size}")

    width, height, flags, frame_count, _first_frame = struct.unpack_from("<HHIHH", data, 16)
    image_format = struct.unpack_from("<I", data, 52)[0]
    mip_count = data[56]
    low_res_format = struct.unpack_from("<I", data, 57)[0]
    low_res_width = data[61]
    low_res_height = data[62]
    depth = struct.unpack_from("<H", data, 63)[0]

    if frame_count != 1:
        raise ValueError(f"{path.name}: expected one frame, got {frame_count}")
    if image_format != IMAGE_FORMAT_BGRA8888:
        raise ValueError(f"{path.name}: expected BGRA8888 format, got {image_format}")
    if not (_is_power_of_two(width) and _is_power_of_two(height)):
        raise ValueError(f"{path.name}: invalid dimensions {width}x{height}")
    expected_mips = expected_mip_count(width, height)
    if mip_count != expected_mips:
        raise ValueError(f"{path.name}: expected a full chain of {expected_mips} mip levels, got {mip_count}")
    if low_res_format != IMAGE_FORMAT_DXT1 or (low_res_width, low_res_height) != (4, 4):
        raise ValueError(f"{path.name}: expected a 4x4 DXT1 thumbnail")
    if depth != 1:
        raise ValueError(f"{path.name}: expected depth 1, got {depth}")

    thumbnail_size = 8
    image_offset = header_size + thumbnail_size
    image_size = 0
    level_width, level_height = width, height
    for _level in range(expected_mips):
        image_size += level_width * level_height * 4
        level_width = max(1, level_width // 2)
        level_height = max(1, level_height // 2)
    expected_size = image_offset + image_size
    if len(data) != expected_size:
        raise ValueError(f"{path.name}: file size is {len(data)}, expected {expected_size}")

    return VtfInfo(
        width=width,
        height=height,
        flags=flags,
        image_format=image_format,
        mip_count=mip_count,
        low_res_format=low_res_format,
        low_res_width=low_res_width,
        low_res_height=low_res_height,
        depth=depth,
        image_offset=image_offset,
        image_size=image_size,
    )


def write_vtf_from_tga(source: Path, destination: Path, *, normal_map: bool = False) -> VtfInfo:
    image = read_uncompressed_tga(source)
    payload = build_vtf(image, normal_map=normal_map)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_bytes(payload)
    info = inspect_vtf(temporary)
    temporary.replace(destination)
    return info
