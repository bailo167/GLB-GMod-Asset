from __future__ import annotations

import io
import json
import struct
import zipfile
from pathlib import Path

from ember_gmod.project import BuildOptions, ProjectStore, slugify, validate_guide


def glb_bytes() -> bytes:
    doc = {
        "asset": {"version": "2.0"},
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
        "accessors": [{"componentType": 5126, "count": 3, "type": "VEC3", "min": [0, 0, 0], "max": [1, 1, 2]}],
    }
    payload = json.dumps(doc).encode()
    payload += b" " * ((4 - len(payload) % 4) % 4)
    return struct.pack("<4sII", b"glTF", 2, 20 + len(payload)) + struct.pack("<II", len(payload), 0x4E4F534A) + payload


def valid_landmarks() -> dict[str, list[float]]:
    return {
        "head_top": [0, 0, 72], "neck_base": [0, 0, 59], "pelvis": [0, 0, 35],
        "shoulder_l": [0, 11, 57], "elbow_l": [0, 20, 56], "wrist_l": [0, 29, 55],
        "shoulder_r": [0, -11, 57], "elbow_r": [0, -20, 56], "wrist_r": [0, -29, 55],
        "hip_l": [0, 5, 34], "knee_l": [0, 5, 19], "ankle_l": [0, 4, 4], "toe_l": [6, 4, 1],
        "hip_r": [0, -5, 34], "knee_r": [0, -5, 19], "ankle_r": [0, -4, 4], "toe_r": [6, -4, 1],
    }


def test_slugify_and_options():
    assert slugify("My Great Character!") == "my_great_character"
    assert slugify("---") == "character"
    options = BuildOptions.from_dict({"display_name": "Axis Test", "front_axis": "pos_x"})
    assert options.front_axis == "pos_x"
    assert options.rig_mode == "guided"
    assert BuildOptions.from_dict({"front_axis": "invalid"}).front_axis == "neg_y"


def test_guide_validation_and_lock(tmp_path: Path):
    guide = {"version": 2, "locked": True, "landmarks": valid_landmarks(), "rigid_zones": []}
    validation = validate_guide(guide, 72)
    assert validation["status"] == "ready"
    assert validation["assigned_required"] == 17

    store = ProjectStore(tmp_path)
    record = store.create(io.BytesIO(glb_bytes()), "input.glb", BuildOptions.from_dict({"display_name": "Guide Test"}))
    assert record.state == "guide_required"
    updated = store.update_guide(record.id, guide)
    assert updated.state == "guide_ready"
    assert updated.guide["locked"] is True


def test_incomplete_guide_cannot_remain_locked(tmp_path: Path):
    store = ProjectStore(tmp_path)
    record = store.create(io.BytesIO(glb_bytes()), "input.glb", BuildOptions.from_dict({"display_name": "Guide Test"}))
    updated = store.update_guide(record.id, {"locked": True, "landmarks": {"head_top": [0, 0, 72]}})
    assert updated.guide["locked"] is False
    assert updated.state == "guide_required"


def test_create_and_package(tmp_path: Path):
    store = ProjectStore(tmp_path)
    options = BuildOptions.from_dict({"display_name": "Test Character", "quality": "good"})
    record = store.create(io.BytesIO(glb_bytes()), "input.glb", options)
    root = store.project_dir(record.id)
    assert (root / "source/model.glb").exists()
    addon = root / "addon"
    addon.mkdir()
    (addon / "addon.json").write_text("{}")
    artifact = store.package_zip(record.id)
    assert artifact.exists()
    with zipfile.ZipFile(artifact) as archive:
        names = archive.namelist()
        assert "source/model.glb" in names
        assert "addon/addon.json" in names
        assert artifact.name not in names



def test_build_settings_can_change_after_creation_without_touching_identity(tmp_path: Path):
    store = ProjectStore(tmp_path / "ws")
    record = store.create(io.BytesIO(glb_bytes()), "input.glb", BuildOptions.from_dict({
        "display_name": "Jack Hegarty", "quality": "good", "texture_size": 1024, "target_height": 64,
    }))
    updated = store.update_options(record.id, {
        "quality": "workshop", "texture_size": 2048, "target_height": 72,
        # identity and unknown fields must be ignored
        "slug": "hijacked", "display_name": "Hijacked", "rig_mode": "raw", "nonsense": 1,
    })
    assert updated.options.quality == "workshop"
    assert updated.options.texture_size == 2048
    assert updated.options.target_height == 72.0
    assert updated.options.slug == record.options.slug
    assert updated.options.display_name == "Jack Hegarty"
    assert updated.options.rig_mode == "guided"
    # invalid values fall back to safe defaults rather than erroring
    clamped = store.update_options(record.id, {"texture_size": 9999, "quality": "ultra", "target_height": 900})
    assert clamped.options.texture_size == 1024
    assert clamped.options.quality == "good"
    assert clamped.options.target_height == 120.0
