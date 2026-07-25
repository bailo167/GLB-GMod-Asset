from pathlib import Path

from blender.tga_writer import write_uncompressed_tga
from ember_gmod.vtf_writer import (
    IMAGE_FORMAT_BGRA8888,
    IMAGE_FORMAT_DXT1,
    TEXTUREFLAGS_EIGHTBITALPHA,
    TEXTUREFLAGS_NOMIP,
    TEXTUREFLAGS_NOLOD,
    TEXTUREFLAGS_NORMAL,
    inspect_vtf,
    write_vtf_from_tga,
)


def test_internal_vtf_writer_produces_complete_7_2_file(tmp_path: Path):
    source = tmp_path / "body.tga"
    output = tmp_path / "body.vtf"
    pixels = []
    for y in range(4):
        for x in range(4):
            pixels.extend((x / 3, y / 3, 0.25, 1.0))
    write_uncompressed_tga(source, 4, 4, pixels, srgb=True, include_alpha=False)
    info = write_vtf_from_tga(source, output)
    assert info.width == 4
    assert info.height == 4
    assert info.image_format == IMAGE_FORMAT_BGRA8888
    assert info.low_res_format == IMAGE_FORMAT_DXT1
    assert info.mip_count == 1
    assert info.flags & TEXTUREFLAGS_NOMIP
    assert info.flags & TEXTUREFLAGS_NOLOD
    assert not info.flags & TEXTUREFLAGS_EIGHTBITALPHA
    assert output.read_bytes()[:4] == b"VTF\x00"
    assert output.stat().st_size == 80 + 8 + 4 * 4 * 4
    assert inspect_vtf(output) == info


def test_internal_vtf_writer_marks_normal_and_alpha(tmp_path: Path):
    normal_source = tmp_path / "body_normal.tga"
    normal_output = tmp_path / "body_normal.vtf"
    write_uncompressed_tga(
        normal_source,
        4,
        4,
        [0.5, 0.5, 1.0, 1.0] * 16,
        srgb=False,
        include_alpha=False,
    )
    normal_info = write_vtf_from_tga(normal_source, normal_output, normal_map=True)
    assert normal_info.flags & TEXTUREFLAGS_NORMAL

    alpha_source = tmp_path / "glass.tga"
    alpha_output = tmp_path / "glass.vtf"
    write_uncompressed_tga(
        alpha_source,
        4,
        4,
        [1.0, 0.5, 0.0, 0.5] * 16,
        srgb=True,
        include_alpha=True,
    )
    alpha_info = write_vtf_from_tga(alpha_source, alpha_output)
    assert alpha_info.flags & TEXTUREFLAGS_EIGHTBITALPHA


def test_internal_vtf_writer_rejects_truncated_output(tmp_path: Path):
    broken = tmp_path / "broken.vtf"
    broken.write_bytes(b"VTF\x00" + b"\x00" * 76)
    try:
        inspect_vtf(broken)
    except ValueError as exc:
        assert "version" in str(exc) or "file size" in str(exc)
    else:
        raise AssertionError("truncated VTF was accepted")


def test_colour_vtf_sets_srgb_while_normal_map_does_not(tmp_path: Path):
    from ember_gmod.vtf_writer import TEXTUREFLAGS_NORMAL, TEXTUREFLAGS_SRGB
    tga = tmp_path / "texture.tga"
    write_uncompressed_tga(tga, 4, 4, [0.25, 0.5, 0.75, 1.0] * 16, srgb=True, include_alpha=False)
    colour = tmp_path / "colour.vtf"
    normal = tmp_path / "normal.vtf"
    colour_info = write_vtf_from_tga(tga, colour, normal_map=False)
    normal_info = write_vtf_from_tga(tga, normal, normal_map=True)
    assert colour_info.flags & TEXTUREFLAGS_SRGB
    assert not (colour_info.flags & TEXTUREFLAGS_NORMAL)
    assert normal_info.flags & TEXTUREFLAGS_NORMAL
    assert not (normal_info.flags & TEXTUREFLAGS_SRGB)
