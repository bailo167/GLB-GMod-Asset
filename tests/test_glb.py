from __future__ import annotations

import json
import struct
from pathlib import Path

from ember_gmod.glb import GLBError, inspect_glb


def make_glb(path: Path) -> None:
    doc = {
        "asset": {"version": "2.0", "generator": "pytest"},
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}],
        "meshes": [{"name": "Body", "primitives": [{"attributes": {"POSITION": 0}, "indices": 1, "material": 0}]}],
        "accessors": [
            {"componentType": 5126, "count": 4, "type": "VEC3", "min": [-1, -1, 0], "max": [1, 1, 2]},
            {"componentType": 5123, "count": 6, "type": "SCALAR"},
        ],
        "materials": [{"name": "Body Mat"}],
        "textures": [],
        "images": [],
    }
    payload = json.dumps(doc, separators=(",", ":")).encode("utf-8")
    payload += b" " * ((4 - len(payload) % 4) % 4)
    total = 12 + 8 + len(payload)
    data = struct.pack("<4sII", b"glTF", 2, total) + struct.pack("<II", len(payload), 0x4E4F534A) + payload
    path.write_bytes(data)


def test_inspect_glb(tmp_path: Path):
    path = tmp_path / "sample.glb"
    make_glb(path)
    result = inspect_glb(path)
    assert result.glb_version == 2
    assert result.meshes == 1
    assert result.primitives == 1
    assert result.vertices == 4
    assert result.triangles == 2
    assert result.materials == 1
    assert result.bounding_box["size"] == [2.0, 2.0, 2.0]
    assert "No skin data found. Character rigging is required." in result.warnings


def test_invalid_glb(tmp_path: Path):
    path = tmp_path / "bad.glb"
    path.write_bytes(b"not glb")
    try:
        inspect_glb(path)
    except GLBError:
        pass
    else:
        raise AssertionError("Invalid input was accepted")
