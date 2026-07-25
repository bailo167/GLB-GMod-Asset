from pathlib import Path

from blender.tga_writer import inspect_tga, write_uncompressed_tga
from ember_gmod.jobs import JobManager


def test_tga_writer_produces_vtex_compatible_uncompressed_24_bit_file(tmp_path: Path):
    path = tmp_path / "opaque.tga"
    pixels = [
        1.0, 0.0, 0.0, 1.0,
        0.0, 1.0, 0.0, 1.0,
        0.0, 0.0, 1.0, 1.0,
        1.0, 1.0, 1.0, 1.0,
    ] * 4
    write_uncompressed_tga(path, 4, 4, pixels, srgb=True, include_alpha=False)
    info = inspect_tga(path)
    assert info == {
        "image_type": 2,
        "width": 4,
        "height": 4,
        "depth": 24,
        "alpha_bits": 0,
        "top_origin": False,
        "expected_size": 66,
        "actual_size": 66,
        "size_matches": True,
    }
    valid, detail = JobManager._inspect_vtex_tga(path)
    assert valid is True
    assert "24 bit" in detail


def test_tga_writer_preserves_real_alpha_as_32_bit(tmp_path: Path):
    path = tmp_path / "alpha.tga"
    pixels = [1.0, 0.0, 0.0, 0.5] * 16
    write_uncompressed_tga(path, 4, 4, pixels, srgb=True, include_alpha=True)
    info = inspect_tga(path)
    assert info["depth"] == 32
    assert info["alpha_bits"] == 8
    assert info["actual_size"] == 82
    valid, detail = JobManager._inspect_vtex_tga(path)
    assert valid is True
    assert "32 bit" in detail


def test_tga_preflight_rejects_rle_and_non_power_of_two(tmp_path: Path):
    rle = tmp_path / "rle.tga"
    header = bytearray(18)
    header[2] = 10
    header[12:14] = (4).to_bytes(2, "little")
    header[14:16] = (4).to_bytes(2, "little")
    header[16] = 32
    rle.write_bytes(bytes(header) + b"\0" * 64)
    valid, detail = JobManager._inspect_vtex_tga(rle)
    assert valid is False
    assert "image type" in detail

    odd = tmp_path / "odd.tga"
    header[2] = 2
    header[12:14] = (6).to_bytes(2, "little")
    odd.write_bytes(bytes(header) + b"\0" * (6 * 4 * 4))
    valid, detail = JobManager._inspect_vtex_tga(odd)
    assert valid is False
    assert "powers of two" in detail
