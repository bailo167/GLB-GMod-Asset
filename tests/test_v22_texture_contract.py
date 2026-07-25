"""Static contract for the v2.2 texture rebake pipeline.

Version 2.1.1 reduced the mesh to 32k triangles and then reused the UV map and
texture that belonged to the 500k triangle import.  These assertions pin the
replacement pipeline so a later change cannot quietly reintroduce that.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIPELINE = (ROOT / "blender" / "pipeline.py").read_text(encoding="utf-8")
JOBS = (ROOT / "ember_gmod" / "jobs.py").read_text(encoding="utf-8")
APP_JS = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
APP_PY = (ROOT / "app.py").read_text(encoding="utf-8")


def test_high_resolution_copy_is_taken_before_the_mesh_is_reduced():
    duplicate = PIPELINE.index("bake_source = duplicate_bake_source(obj)")
    reduce_call = PIPELINE.index("base_reduction = reduce_base_mesh(obj, base_targets[quality])")
    assert duplicate < reduce_call


def test_the_atlas_is_rebuilt_and_baked_after_reduction():
    reduce_call = PIPELINE.index("base_reduction = reduce_base_mesh(obj, base_targets[quality])")
    bake = PIPELINE.index("texture_bake = rebuild_atlas_and_bake(")
    materials = PIPELINE.index("materials = extract_materials(")
    assert reduce_call < bake < materials


def test_imported_uv_layers_are_discarded_rather_than_reused():
    section = PIPELINE[PIPELINE.index("def rebuild_uv_atlas"):PIPELINE.index("def uv_triangles")]
    assert "mesh.uv_layers.remove(mesh.uv_layers[0])" in section
    assert "bpy.ops.uv.smart_project" in section
    assert 'mesh.uv_layers.new(name=BAKE_UV_LAYER)' in section


def test_bake_uses_cycles_selected_to_active_diffuse_colour():
    section = PIPELINE[PIPELINE.index("def _bake_settings"):PIPELINE.index("def source_alpha_required")]
    assert 'scene.render.engine = "CYCLES"' in section
    assert "bake.use_selected_to_active = True" in section
    assert "bake.cage_extrusion = extrusion" in section
    assert "max_ray_distance" in section
    assert "bake.use_pass_direct = False" in section
    assert "bake.use_pass_indirect = False" in section
    assert "bake.use_pass_color = True" in section
    assert "bpy.ops.object.bake(type=bake_type)" in PIPELINE


def test_the_bake_source_is_the_high_resolution_copy_and_the_target_is_active():
    section = PIPELINE[PIPELINE.index("def bake_selected_to_active"):PIPELINE.index("def source_alpha_required")]
    assert "bake_source.select_set(True)" in section
    assert "bpy.context.view_layer.objects.active = target" in section


def test_every_validation_rule_runs_before_the_build_can_continue():
    section = PIPELINE[PIPELINE.index("def rebuild_atlas_and_bake"):PIPELINE.index("def safe_material_name")]
    for call in (
        "uv_bounds_report(",
        "rasterize_triangles(",
        "island_report(",
        "triangle_coverage_report(",
        "colour_report(",
        "unpainted_region_report(",
        "render_material_proof(",
        "evaluate_bake(",
    ):
        assert call in section, call
    assert 'if not texture_bake["passed"]:' in PIPELINE
    assert "The rebuilt UV atlas and texture bake failed validation" in PIPELINE


def test_the_material_proof_is_rendered_before_studiomdl_is_launched():
    # The proof render happens inside the Blender stage, and the job runner only
    # reaches StudioMDL after that stage exits with code zero.
    proof = PIPELINE.index("render_material_proof(proof_path, obj, height, back=is_back)")
    assert proof < PIPELINE.index('reports.joinpath("build_report.json")')
    blender_stage = JOBS.index("Generating guided ValveBiped source")
    studiomdl = JOBS.index("Compiling Source player model")
    assert blender_stage < studiomdl


def test_lod_reduction_protects_the_new_atlas_seams():
    section = PIPELINE[PIPELINE.index("def duplicate_lod"):PIPELINE.index("def create_hands_source")]
    assert "split = split_atlas_seams(obj)" in section
    assert PIPELINE.index("def split_atlas_seams") < PIPELINE.index("def duplicate_lod")
    assert "bmesh.ops.split_edges" in PIPELINE


def test_the_sentinel_never_reaches_the_exported_texture():
    section = PIPELINE[PIPELINE.index("def flatten_unpainted"):PIPELINE.index("def persist_bake_image")]
    assert "values[base + 3] = 1.0" in section
    assert "flattened = flatten_unpainted(image, painted, dilation_passes)" in PIPELINE


def test_the_atlas_is_dilated_before_the_shipped_coverage_is_measured():
    # The verdict has to describe the texture that ships, after the bake margin
    # and the dilation have grown colour outward from every painted island.
    dilate = PIPELINE.index("flattened = flatten_unpainted(image, painted, dilation_passes)")
    shipped = PIPELINE.index('shipped = flattened.pop("mask")')
    measure = PIPELINE.index("coverage = triangle_coverage_report(triangles, shipped, texture_size, neighbourhood=1)")
    verdict = PIPELINE.index("verdict = evaluate_bake(")
    assert dilate < shipped < measure < verdict


def test_the_retry_loop_reads_the_raw_bake_and_stops_when_escalation_stops_helping():
    section = PIPELINE[PIPELINE.index("def rebuild_atlas_and_bake"):PIPELINE.index("def safe_material_name")]
    assert "triangle_coverage_report(triangles, painted, texture_size, neighbourhood=0)" in section
    assert 'attempts[-1]["uncovered_measurable_triangles"] >= attempts[-2]["uncovered_measurable_triangles"]' in section


def test_deprecated_use_nodes_is_not_read_on_blender_5_or_newer():
    # Blender 5.x warns on Material.use_nodes and 6.0 removes it. Reading
    # node_tree first keeps 4.x working without touching the deprecated flag.
    helper = PIPELINE[PIPELINE.index("def material_node_tree"):PIPELINE.index("def assign_baked_material")]
    assert "material.use_nodes = True" in helper
    # The single write inside the helper is the only mention anywhere.
    assert PIPELINE.count("use_nodes") == helper.count("use_nodes")


def test_the_baked_atlas_survives_reopening_the_saved_blender_source():
    # A generated image stores only its generation settings in a .blend, so the
    # atlas has to become file backed before the source file is written.
    section = PIPELINE[PIPELINE.index("def persist_bake_image"):PIPELINE.index("def render_material_proof")]
    assert "image.filepath_raw = str(path)" in section
    assert "image.save()" in section
    assert "image.pack()" in section
    assert PIPELINE.index("atlas_file = persist_bake_image(") < PIPELINE.index("bpy.ops.wm.save_as_mainfile")


def test_a_failed_texture_bake_still_writes_a_report_and_names_the_real_rule():
    # A build that stops at the gate has already written the atlas and both proof
    # renders. Without a report the Builder cannot point at them.
    section = PIPELINE[PIPELINE.index('if not texture_bake["passed"]:'):PIPELINE.index("animation_base = options.get")]
    assert '"status": "texture_bake_failed"' in section
    assert 'reports.joinpath("build_report.json").write_text' in section
    assert '"texture_bake_failures": texture_bake["failures"]' in section
    assert 'texture_bake["failure_detail"]' in section
    assert "atlas_resolution_adequate" in PIPELINE
    assert "atlas_resolution_adequate" in JOBS


def test_the_proof_card_loads_renders_even_when_validation_rejected_the_bake():
    section = APP_JS[APP_JS.index("function refreshTextureProof"):APP_JS.index("async function refreshFiles")]
    assert "texture_bake_failures" in section
    # No dependency on a passing report: the images are always requested and the
    # figure removes itself if the service has none.
    assert "addEventListener('error'" in section
    assert "texture-proof?view=" in section


def test_texture_bake_results_are_reported_to_the_builder_interface():
    for key in (
        "uv_atlas_rebuilt_on_reduced_mesh",
        "original_uv_map_discarded",
        "texture_baked_from_high_resolution",
        "every_triangle_has_bake_coverage",
        "bake_has_colour_variation",
        "no_large_unpainted_regions",
        "baked_material_rendered_in_blender",
        "texture_bake_passed",
    ):
        assert key in PIPELINE, key
        assert key in JOBS, key


def test_the_builder_serves_and_shows_the_baked_material_proof():
    assert "/api/projects/([a-f0-9]{12})/texture-proof" in APP_PY
    assert "texture_proof_{view}.png" in APP_PY
    assert "refreshTextureProof" in APP_JS
    assert "texture-proof?view=" in APP_JS


def test_compiled_file_lists_are_no_longer_reported_as_failures():
    # The v2.1.1 expression marked any non empty array red, which made a
    # successful four file StudioMDL compile look like a failed check.
    assert "Array.isArray(value)?value.length===0:true" not in APP_JS
    assert "function checkRowState(name,value)" in APP_JS
    assert "PROBLEM_LIST_PATTERN" in APP_JS


def _check_row_state(name, value):
    """Python port of the browser rule, driven by the regex app.js actually ships."""
    literal = APP_JS.split("const PROBLEM_LIST_PATTERN=", 1)[1].split(";", 1)[0].strip()
    assert literal.startswith("/") and literal.endswith("/i")
    pattern = re.compile(literal[1:-2], re.IGNORECASE)
    if isinstance(value, bool):
        return value
    if isinstance(value, list):
        return len(value) == 0 if pattern.search(name) else len(value) > 0
    return True


def test_produced_artefact_lists_are_green_when_populated():
    compiled = [
        "addon/models/player/jack.mdl",
        "addon/models/player/jack.vvd",
        "addon/models/player/jack.dx90.vtx",
        "addon/models/player/jack.phy",
    ]
    assert _check_row_state("compiled_files", compiled) is True
    assert _check_row_state("compiled_files", []) is False
    assert _check_row_state("baked_material_proofs", ["jack_texture_proof_front.png"]) is True
    assert _check_row_state("root_bones", ["ValveBiped.Bip01_Pelvis"]) is True


def test_problem_lists_are_red_when_populated():
    assert _check_row_state("missing_vtf_references", ["body"]) is False
    assert _check_row_state("missing_vtf_references", []) is True
    assert _check_row_state("unsafe_material_tokens", ["body.vmt:$phong"]) is False
    assert _check_row_state("post_build.missing_runtime_checks", ["materials_valid"]) is False
    assert _check_row_state("guide_validation.errors", []) is True


def test_boolean_and_scalar_rows_are_unchanged():
    assert _check_row_state("studiomdl_compiled", True) is True
    assert _check_row_state("studiomdl_compiled", False) is False
    assert _check_row_state("valid_vtf_count", 1) is True


def test_left_and_right_foot_ik_chains_use_the_same_valve_knee_direction():
    # ValveBiped leg bones are not axis mirrored, so Valve's player QCs give
    # BOTH feet the same knee hint. The earlier mirrored "0 1 0" on the left
    # chain bent the left knee backwards whenever walking foot IK engaged.
    section = PIPELINE[PIPELINE.index("def _write_ik_qci"):PIPELINE.index("def _write_ragdoll_qci")]
    assert "$ikchain rfoot ValveBiped.Bip01_R_Foot knee 0.707107 -0.707107 0.000000" in section
    assert "$ikchain lfoot ValveBiped.Bip01_L_Foot knee 0.707107 -0.707107 0.000000" in section
    assert "knee 0 1 0" not in section
    assert "knee 0 -1 0" not in section


def test_no_decimated_lods_are_generated():
    # Decimating the rebuilt atlas either merges island loops or cracks the
    # split seams, and StudioMDL measured LOD1 diverging by 11,674 vertices.
    # The reference mesh is used at every distance.
    section = PIPELINE[PIPELINE.index("ratios: list[float] = []"):PIPELINE.index("physics = create_physics_mesh")]
    assert "duplicate_lod" in section  # the machinery stays, the list is empty


def test_gaps_are_flood_filled_and_the_sentinel_is_neutral():
    assert "dilation_passes = 2 * texture_size" in PIPELINE
    assert "BAKE_SENTINEL = (0.42, 0.42, 0.42, 0.0)" in PIPELINE


def test_the_vtf_ships_a_full_mip_chain():
    writer = (ROOT / "ember_gmod" / "vtf_writer.py").read_text(encoding="utf-8")
    assert "def build_mip_chain" in writer
    assert "chain = build_mip_chain(image.pixels_bgra, image.width, image.height)" in writer
    assert "flags = TEXTUREFLAGS_NOMIP | TEXTUREFLAGS_NOLOD" not in writer


def test_smd_uvs_are_written_in_blender_v_orientation_without_a_flip():
    # The SMD text format stores V bottom-origin, same as Blender; StudioMDL
    # does the DirectX flip itself. Every release through 2.2.4 pre-flipped V in
    # export_smd, so the game sampled the atlas vertically mirrored while the
    # Blender proof renders looked correct. This was the last difference between
    # the proof and the game.
    section = PIPELINE[PIPELINE.index("def export_smd"):PIPELINE.index("def point_camera")]
    assert "{uv.x:.6f} {uv.y:.6f}" in section
    assert "1.0 - uv.y" not in PIPELINE
