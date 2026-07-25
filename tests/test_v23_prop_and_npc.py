"""V2.3.0 contract: prop projects, NPC variants and the localized edge repair."""
from __future__ import annotations

import io
import json
import struct
from pathlib import Path

from ember_gmod.installer import install_to_gmod
from ember_gmod.project import BuildOptions, ProjectStore
from ember_gmod.runtime_addon import NPC_VARIANTS, ensure_runtime_addon_files
from ember_gmod.vtf_writer import write_vtf_from_tga
from blender.tga_writer import write_uncompressed_tga
from blender.rig_math import repair_localized_edge_outliers, robust_edge_deformation


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


def test_asset_type_is_validated_and_props_never_carry_npcs():
    assert BuildOptions.from_dict({"display_name": "X"}).asset_type == "character"
    assert BuildOptions.from_dict({"display_name": "X", "asset_type": "prop"}).asset_type == "prop"
    assert BuildOptions.from_dict({"display_name": "X", "asset_type": "vehicle"}).asset_type == "character"
    assert BuildOptions.from_dict({"display_name": "X", "generate_npcs": True}).generate_npcs is True
    assert BuildOptions.from_dict({"display_name": "X", "asset_type": "prop", "generate_npcs": True}).generate_npcs is False


def test_prop_project_skips_the_guide_stage(tmp_path: Path):
    store = ProjectStore(tmp_path)
    record = store.create(io.BytesIO(glb_bytes()), "scan.glb", BuildOptions.from_dict({"display_name": "Crate", "asset_type": "prop"}))
    assert record.state == "prop_ready"
    loaded = store.load(record.id)
    assert loaded.state == "prop_ready"
    view = loaded.to_dict()
    assert view["guide_validation"]["status"] == "not_required"
    assert view["guide_validation"]["errors"] == []
    try:
        store.update_guide(record.id, {"locked": True, "landmarks": {}})
        raised = False
    except ValueError:
        raised = True
    assert raised


def test_generate_npcs_is_mutable_after_creation(tmp_path: Path):
    store = ProjectStore(tmp_path)
    record = store.create(io.BytesIO(glb_bytes()), "hero.glb", BuildOptions.from_dict({"display_name": "Hero"}))
    assert record.options.generate_npcs is False
    updated = store.update_options(record.id, {"generate_npcs": True})
    assert updated.options.generate_npcs is True
    prop = store.create(io.BytesIO(glb_bytes()), "scan.glb", BuildOptions.from_dict({"display_name": "Crate", "asset_type": "prop"}))
    still_off = store.update_options(prop.id, {"generate_npcs": True})
    assert still_off.options.generate_npcs is False


def test_prop_runtime_files_register_a_spawnmenu_prop_without_player_registration(tmp_path: Path):
    addon = tmp_path / "addon"
    paths = ensure_runtime_addon_files(
        addon, "crate", "Crate", project_id="abc123abc123", build_token="token",
        asset_type="prop",
    )
    registration = paths["registration"].read_text(encoding="utf-8")
    assert "models/props/crate/crate.mdl" in registration
    assert "spawnmenu.AddPropCategory" in registration
    assert "resource.AddFile(MODEL)" in registration
    assert "file.Write(RESULT_FILE" in registration
    assert "physics_file_exists" in registration
    assert "player_manager.AddValidModel" not in registration
    assert not (addon / "lua" / "autorun" / "client").exists()
    assert not (addon / "lua" / "entities").exists()


def test_npc_variants_generate_a_nextbot_base_and_spawnable_variants(tmp_path: Path):
    addon = tmp_path / "addon"
    paths = ensure_runtime_addon_files(
        addon, "hero", "Hero", project_id="abc123abc123", build_token="token",
        generate_npcs=True,
    )
    base = (addon / "lua" / "entities" / "ember_hero_npc_base" / "shared.lua").read_text(encoding="utf-8")
    assert 'ENT.Base = "base_nextbot"' in base
    assert "ENT.Spawnable = false" in base
    assert "self:BodyMoveXY()" in base
    assert "ACT_HL2MP_RUN" in base
    assert "EF_BONEMERGE" in base
    assert "self:FireBullets({" in base
    assert "self:BecomeRagdoll(damageInfo)" in base
    assert "function ENT:RunBehaviour()" in base

    variant_dirs = sorted(p.name for p in (addon / "lua" / "entities").glob("ember_hero_npc_*"))
    assert len(variant_dirs) == len(NPC_VARIANTS) + 1  # base + spawnable variants

    friendly = (addon / "lua" / "entities" / "ember_hero_npc_friendly" / "shared.lua").read_text(encoding="utf-8")
    assert "ENT.EmberHostile = false" in friendly
    assert "ENT.Spawnable = true" in friendly
    smg = (addon / "lua" / "entities" / "ember_hero_npc_smg" / "shared.lua").read_text(encoding="utf-8")
    assert "ENT.EmberHostile = true" in smg
    assert "models/weapons/w_smg1.mdl" in smg
    assert 'ENT.Base = "ember_hero_npc_base"' in smg

    registration = paths["registration"].read_text(encoding="utf-8")
    assert "ember_hero_npc_friendly" in registration
    assert "npc_entities_registered" in registration
    assert "and result.npc_entities_registered" in registration

    # Turning the option off removes every NPC entity again.
    ensure_runtime_addon_files(
        addon, "hero", "Hero", project_id="abc123abc123", build_token="token",
        generate_npcs=False,
    )
    assert not list((addon / "lua" / "entities").glob("ember_hero_npc_*"))
    registration = paths["registration"].read_text(encoding="utf-8")
    assert "ember_hero_npc_friendly" not in registration


def test_prop_install_verifies_the_props_model_path(tmp_path: Path):
    root = tmp_path
    slug = "crate"
    addon = root / "addon"
    model_dir = addon / "models" / "props" / slug
    material_dir = addon / "materials" / "models" / "props" / slug
    model_dir.mkdir(parents=True)
    material_dir.mkdir(parents=True)
    (model_dir / f"{slug}.mdl").write_bytes(b"IDST" + b"\0" * 2048)
    for ext in (".vvd", ".dx90.vtx", ".phy"):
        (model_dir / f"{slug}{ext}").write_bytes(b"DATA" + b"\0" * 128)
    (material_dir / "body.vmt").write_text('"VertexLitGeneric" {}\n', encoding="utf-8")
    tga = root / "body.tga"
    write_uncompressed_tga(tga, 4, 4, [0.5, 0.25, 0.75, 1.0] * 16, srgb=True, include_alpha=False)
    write_vtf_from_tga(tga, material_dir / "body.vtf")
    game = root / "garrysmod"
    game.mkdir()
    (game / "gameinfo.txt").write_text("GameInfo {}", encoding="utf-8")

    result = install_to_gmod(addon, game, slug, "Crate", "abc123abc123", "token", asset_type="prop")
    assert result["installed"] is True
    assert result["verification"]["prop_registration"] is True
    assert result["verification"]["spawnlist_registration"] is True
    assert (game / "lua" / "autorun" / f"ember_{slug}.lua").is_file()
    assert (game / "models" / "props" / slug / f"{slug}.mdl").is_file()
    assert not (game / "lua" / "autorun" / "client" / f"00_ember_{slug}_player.lua").exists()


def test_localized_edge_outlier_repair_fixes_one_spike_and_refuses_an_explosion():
    count = 300
    rest = [(float(i), 0.0, 0.0) for i in range(count)]
    edges = [(i, i + 1) for i in range(count - 1)]
    warped = [(x, y, z + 1.0) for x, y, z in rest]
    warped[50] = (50.0, 0.0, 9.0)

    broken = robust_edge_deformation(
        [((1.0), (((warped[b][0] - warped[a][0]) ** 2 + (warped[b][2] - warped[a][2]) ** 2) ** 0.5)) for a, b in edges],
        72.0,
    )
    assert not broken["passed"]

    repair = repair_localized_edge_outliers(rest, warped, edges, 72.0)
    assert repair["attempted"] is True
    assert repair["outlier_vertices"] >= 1
    repaired = repair["repaired"]
    assert abs(repaired[50][2] - 1.0) < 1e-6
    clean = robust_edge_deformation(
        [((1.0), (((repaired[b][0] - repaired[a][0]) ** 2 + (repaired[b][2] - repaired[a][2]) ** 2) ** 0.5)) for a, b in edges],
        72.0,
    )
    assert clean["passed"]

    exploded = [(x, y, z + (25.0 if i % 3 == 0 else 1.0)) for i, (x, y, z) in enumerate(rest)]
    refused = repair_localized_edge_outliers(rest, exploded, edges, 72.0)
    assert refused["attempted"] is False
    assert refused["reason"] == "outliers_not_localized"


def test_pipeline_contains_the_prop_branch_and_the_conformance_repair():
    root = Path(__file__).resolve().parents[1]
    pipeline = (root / "blender" / "pipeline.py").read_text(encoding="utf-8")
    assert "def normalise_prop(" in pipeline
    assert "def create_prop_collision(" in pipeline
    assert "def generate_prop_qc(" in pipeline
    assert "PROP_BONES" in pipeline
    assert "'$staticprop'" in pipeline
    assert '$collisionmodel "{slug}_physics.smd"' in pipeline
    assert 'asset_type == "prop"' in pipeline
    assert "repair_localized_edge_outliers" in pipeline
    assert "guided_to_stock_lbs_repaired" in pipeline
    assert '"edge_repair": edge_repair' in pipeline
    app = (root / "app.py").read_text(encoding="utf-8")
    assert 'asset_type != "prop"' in app
    assert "npc_entities_registered" in app
