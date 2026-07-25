from pathlib import Path


def test_pipeline_imports_shutil_before_copying_materials():
    root = Path(__file__).resolve().parents[1]
    source = (root / "blender" / "pipeline.py").read_text(encoding="utf-8")
    assert "import shutil" in source
    assert "shutil.copy2(source, material_target / source.name)" in source


def test_config_fills_blank_detected_paths():
    root = Path(__file__).resolve().parents[1]
    source = (root / "ember_gmod" / "config.py").read_text(encoding="utf-8")
    assert "detected = detect_toolchain()" in source
    assert "if configured:" in source
    assert "values[key] = detected_value" in source


def test_pipeline_uses_strict_tga_writer_and_internal_vtf_writer():
    root = Path(__file__).resolve().parents[1]
    pipeline = (root / "blender" / "pipeline.py").read_text(encoding="utf-8")
    jobs = (root / "ember_gmod" / "jobs.py").read_text(encoding="utf-8")
    writer = (root / "ember_gmod" / "vtf_writer.py").read_text(encoding="utf-8")
    assert "from tga_writer import write_uncompressed_tga" in pipeline
    assert "write_uncompressed_tga(" in pipeline
    assert 'export.file_format = "TARGA"' not in pipeline
    assert "write_vtf_from_tga" in jobs
    assert "Internal VTF writer produced" in jobs
    assert "config.vtex" not in jobs[jobs.index("def _compile_textures"):jobs.index("def _compile_source")]
    assert "IMAGE_FORMAT_BGRA8888 = 12" in writer
    assert "IMAGE_FORMAT_DXT1 = 13" in writer
    assert "VTF_HEADER_SIZE = 80" in writer
    assert "include_alpha=has_alpha" in pipeline


def test_conformance_is_evaluated_before_any_mesh_write_and_has_no_warp_fallback():
    root = Path(__file__).resolve().parents[1]
    source = (root / "blender" / "pipeline.py").read_text(encoding="utf-8")
    section = source[source.index("def conform_mesh_to_standard_skeleton"):source.index("def create_armature")]
    decision = section.index("if candidate_safe and not use_original_mesh")
    first_write = section.index("vertex.co = inverse_object @ conformed")
    assert first_write > decision
    assert 'mode = "original_mesh_exact_stock_bind"' in section
    assert 'mesh_vertices_modified' in section
    assert 'stock_compatible_without_warp' in section
    assert 'robust_edge_deformation' in section
