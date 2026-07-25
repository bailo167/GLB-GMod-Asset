from pathlib import Path


def test_pipeline_removes_all_known_v204_runtime_failure_paths():
    root = Path(__file__).resolve().parents[1]
    pipeline = (root / "blender" / "pipeline.py").read_text(encoding="utf-8")
    jobs = (root / "ember_gmod" / "jobs.py").read_text(encoding="utf-8")
    runtime = (root / "ember_gmod" / "runtime_addon.py").read_text(encoding="utf-8")
    installer = (root / "ember_gmod" / "installer.py").read_text(encoding="utf-8")

    assert "ARMATURE_AUTO" not in pipeline
    assert "obj.evaluated_get" not in pipeline
    assert "commonbones.qci" not in pipeline.split("def generate_qc", 1)[1].split("def generate_addon", 1)[0]
    assert "exact_stock_smd_bind" in pipeline
    assert "stock_skeleton_conformance_passed" in pipeline
    assert "edge_deformation_valid" in pipeline
    assert "reference_smd_validated" in pipeline
    assert '"$phong"' not in pipeline.split("def extract_materials", 1)[1].split("def _vertex_influences", 1)[0]
    assert '"$bumpmap"' not in pipeline.split("def extract_materials", 1)[1].split("def _vertex_influences", 1)[0]
    assert "install_to_gmod(" in jobs
    assert "installed_unverified" in jobs
    assert "00_ember_{slug}_player.lua" in runtime
    assert 'list.Set("PlayerOptionsModel", ID, MODEL)' in runtime
    assert 'list.Set("PlayerOptionsModel", DISPLAY, MODEL)' in runtime
    assert "all_direct_file_hashes_verified" in installer
    assert "runtime_result_path.write_text" in installer
    assert "core_bone_hierarchy_valid" in runtime
    assert "ClientsideRagdoll" in runtime
    assert "info.SequenceCount" not in runtime
    assert "info.MaterialCount" not in runtime
    assert "lookup ~= index - 1" not in runtime


def test_blender_launch_propagates_python_failures():
    root = Path(__file__).resolve().parents[1]
    jobs = (root / "ember_gmod" / "jobs.py").read_text(encoding="utf-8")
    assert '"--python-exit-code"' in jobs
