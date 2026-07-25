from __future__ import annotations

import io
import json
from pathlib import Path

from ember_gmod.config import ToolchainConfig
from ember_gmod.jobs import JobManager
from ember_gmod.project import BuildOptions, ProjectStore
from test_project import glb_bytes, valid_landmarks


def test_strict_guided_source_and_material_validation(tmp_path: Path):
    store = ProjectStore(tmp_path / "workspace")
    record = store.create(io.BytesIO(glb_bytes()), "input.glb", BuildOptions.from_dict({"display_name": "Normal Test", "slug": "normal_test"}))
    record = store.update_guide(record.id, {"locked": True, "landmarks": valid_landmarks(), "rigid_zones": []})
    root = store.project_dir(record.id)
    modelsrc = root / "generated" / "modelsrc"
    modelsrc.mkdir(parents=True)
    (modelsrc / "normal_test.qc").write_text(
        '$include "ragdoll.qci"\n$include "hitbox.qci"\n$include "standardikchains.qci"\n$includemodel "m_anm.mdl"\n',
        encoding="utf-8",
    )
    (modelsrc / "ragdoll.qci").write_text('$collisionjoints "normal_test_physics.smd" {}\n', encoding="utf-8")
    (modelsrc / "hitbox.qci").write_text('$hbox 1 "ValveBiped.Bip01_Head1" 0 0 0 1 1 1\n', encoding="utf-8")
    (modelsrc / "standardikchains.qci").write_text('$ikchain lfoot ValveBiped.Bip01_L_Foot knee 0 1 0\n', encoding="utf-8")
    (modelsrc / "normal_test_reference.smd").write_text('version 1\nnodes\n0 "ValveBiped.Bip01_Pelvis" -1\nend\n', encoding="utf-8")
    (modelsrc / "normal_test_physics.smd").write_text("version 1\n", encoding="utf-8")

    addon = root / "addon"
    materials = addon / "materials" / "models" / "player" / "normal_test"
    materials.mkdir(parents=True)
    (materials / "body.vtf").write_bytes(b"VTF\x00" + b"\0" * 96)
    (materials / "body.vmt").write_text(
        '"VertexLitGeneric"\n{\n"$basetexture" "models/player/normal_test/body"\n"$model" "1"\n"$halflambert" "1"\n}\n',
        encoding="utf-8",
    )
    (addon / "addon.json").write_text("{}", encoding="utf-8")
    (root / "reports").mkdir(parents=True)
    (root / "reports" / "build_report.json").write_text(json.dumps({"checks": {
        "exact_stock_smd_bind": True,
        "stock_skeleton_conformance_passed": True,
        "guide_anatomy_valid": True,
        "source_axis_contract": True,
        "no_definebone_override": True,
    }}), encoding="utf-8")

    manager = JobManager(tmp_path, store, lambda: ToolchainConfig())
    manager.logs["job"] = []
    manager._post_validate("job", root, record, compiled=False, terminal_state="source_ready", textures_compiled=True)
    checks = json.loads((root / "reports" / "build_report.json").read_text(encoding="utf-8"))["post_build"]
    assert checks["guide_locked"] is True
    assert checks["valvebiped_root_is_pelvis"] is True
    assert checks["no_fake_valvebiped_root"] is True
    assert checks["exact_stock_smd_bind"] is True
    assert checks["stock_skeleton_conformance_passed"] is True
    assert checks["no_definebone_override"] is True
    assert checks["qc_includes_all_qci"] is True
    assert checks["all_vtf_headers_valid"] is True
    assert checks["material_references_complete"] is True
    assert checks["safe_base_only_materials"] is True
    assert checks["player_manager_registration_created"] is True
    assert checks["dedicated_client_registration_created"] is True
    assert checks["spawnable_ragdoll_created"] is True
    assert checks["runtime_animation_validation_created"] is True


def test_blender_pipeline_uses_exact_source_smd_bind_without_definebone_override():
    root = Path(__file__).resolve().parents[1]
    pipeline = (root / "blender" / "pipeline.py").read_text(encoding="utf-8")
    skeleton = (root / "blender" / "source_skeleton.py").read_text(encoding="utf-8")
    assert '"name": "ValveBiped.Bip01"' not in pipeline
    assert "'$eyeposition 0 0 64'" in pipeline
    assert "from source_skeleton import CORE_ORDER" in pipeline
    assert '"ValveBiped.Bip01_Pelvis": (None,' in skeleton
    assert "(1.570796, 0.0, 0.000001)" in skeleton
    assert "$definebone" not in pipeline.split("def generate_qc", 1)[1].split("def generate_addon", 1)[0]
    assert "commonbones.qci" not in pipeline.split("def generate_qc", 1)[1].split("def generate_addon", 1)[0]
    assert "guide_in_source_axes" in pipeline
    assert "conform_mesh_to_standard_skeleton" in pipeline
    assert "validate_reference_smd" in pipeline
