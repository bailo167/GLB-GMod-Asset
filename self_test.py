from __future__ import annotations

import io
import json
import struct
import tempfile
import unittest
from pathlib import Path

from ember_gmod.glb import inspect_glb
from ember_gmod.installer import install_to_gmod
from ember_gmod.project import BuildOptions, ProjectStore, validate_guide
from ember_gmod.runtime_addon import ensure_runtime_addon_files
from ember_gmod.vtf_writer import inspect_vtf, write_vtf_from_tga
from blender.tga_writer import inspect_tga, write_uncompressed_tga
from blender.source_skeleton import CORE_ORDER, MALE, validate_template, world_positions
from blender.rig_math import robust_edge_deformation


def sample_glb(binary_bytes: int = 0) -> bytes:
    doc = {
        "asset": {"version": "2.0", "generator": "Ember v2 self test"},
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1, "material": 0}]}],
        "accessors": [
            {"componentType": 5126, "count": 4, "type": "VEC3", "min": [-1, -1, 0], "max": [1, 1, 2]},
            {"componentType": 5123, "count": 6, "type": "SCALAR"},
        ],
        "materials": [{"name": "Body"}],
    }
    payload = json.dumps(doc, separators=(",", ":")).encode("utf-8")
    payload += b" " * ((4 - len(payload) % 4) % 4)
    chunks = struct.pack("<II", len(payload), 0x4E4F534A) + payload
    if binary_bytes:
        binary_bytes += (4 - binary_bytes % 4) % 4
        binary = b"\0" * binary_bytes
        chunks += struct.pack("<II", len(binary), 0x004E4942) + binary
    return struct.pack("<4sII", b"glTF", 2, 12 + len(chunks)) + chunks


def landmarks() -> dict[str, list[float]]:
    return {
        "head_top": [0, 0, 72], "neck_base": [0, 0, 59], "pelvis": [0, 0, 35],
        "shoulder_l": [0, 11, 57], "elbow_l": [0, 20, 56], "wrist_l": [0, 29, 55],
        "shoulder_r": [0, -11, 57], "elbow_r": [0, -20, 56], "wrist_r": [0, -29, 55],
        "hip_l": [0, 5, 34], "knee_l": [0, 5, 19], "ankle_l": [0, 4, 4], "toe_l": [6, 4, 1],
        "hip_r": [0, -5, 34], "knee_r": [0, -5, 19], "ankle_r": [0, -4, 4], "toe_r": [6, -4, 1],
    }


class EmberV2SelfTest(unittest.TestCase):
    def test_glb_import_and_guided_project(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            glb = root / "sample.glb"
            glb.write_bytes(sample_glb())
            self.assertEqual(inspect_glb(glb).triangles, 2)
            store = ProjectStore(root / "workspace")
            record = store.create(io.BytesIO(sample_glb()), "sample.glb", BuildOptions.from_dict({"display_name": "Self Test"}))
            self.assertEqual(record.state, "guide_required")
            record = store.update_guide(record.id, {"locked": True, "landmarks": landmarks(), "rigid_zones": []})
            self.assertEqual(record.state, "guide_ready")
            self.assertTrue(record.guide["locked"])
            self.assertEqual(validate_guide(record.guide, 72)["status"], "ready")

    def test_hunyuan_sized_glb_is_not_truncated(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            blob = sample_glb(45 * 1024 * 1024)
            store = ProjectStore(Path(temp) / "workspace")
            record = store.create(io.BytesIO(blob), "large.glb", BuildOptions.from_dict({"display_name": "Large"}))
            saved = store.project_dir(record.id) / "source" / "model.glb"
            self.assertEqual(saved.stat().st_size, len(blob))

    def test_runtime_registration_checks_animation_materials_and_spawnability(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            paths = ensure_runtime_addon_files(Path(temp) / "addon", "self_test", "Self Test", project_id="123456abcdef", build_token="selftoken")
            registration = paths["registration"].read_text(encoding="utf-8")
            validation = paths["validation"].read_text(encoding="utf-8")
            entity = paths["entity_shared"].read_text(encoding="utf-8")
            self.assertIn("player_manager.AddValidModel(ID, MODEL)", registration)
            self.assertIn("resource.AddFile(MODEL)", registration)
            self.assertIn('list.Set("PlayerOptionsModel", ID, MODEL)', registration)
            self.assertIn("spawnmenu.AddPropCategory", registration)
            self.assertIn("ENT.Spawnable = true", entity)
            self.assertIn("SelectWeightedSequence", validation)
            self.assertIn("required_bones_valid", validation)
            self.assertIn("material_error_count", validation)
            self.assertIn("IsErrorTexture", validation)
            self.assertIn('local BUILD_TOKEN = "selftoken"', validation)

    def test_installer_accepts_fully_validated_internal_vtf(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); slug = "self_test"; addon = root / "addon"
            model_dir = addon / "models" / "player" / slug; material_dir = addon / "materials" / "models" / "player" / slug
            model_dir.mkdir(parents=True); material_dir.mkdir(parents=True)
            (model_dir / f"{slug}.mdl").write_bytes(b"IDST" + b"\0" * 2048)
            for ext in (".vvd", ".dx90.vtx", ".phy"): (model_dir / f"{slug}{ext}").write_bytes(b"DATA" + b"\0" * 128)
            (material_dir / "body.vmt").write_text('"VertexLitGeneric" {}\n', encoding="utf-8")
            tga = root / "body.tga"
            write_uncompressed_tga(tga, 4, 4, [0.5, 0.25, 0.75, 1.0] * 16, srgb=True, include_alpha=False)
            write_vtf_from_tga(tga, material_dir / "body.vtf")
            game = root / "garrysmod"; game.mkdir(); (game / "gameinfo.txt").write_text("GameInfo {}", encoding="utf-8")
            result = install_to_gmod(addon, game, slug, "Self Test", "123456abcdef", "selftoken")
            self.assertTrue(result["verification"]["full_vtf_validation"])
            self.assertTrue((game / "lua" / "autorun" / "ember_self_test.lua").is_file())



    def test_internal_vtf_is_complete_not_just_a_signature(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tga = root / "body.tga"
            vtf = root / "body.vtf"
            write_uncompressed_tga(tga, 4, 4, [0.2, 0.4, 0.8, 1.0] * 16, srgb=True, include_alpha=False)
            info = write_vtf_from_tga(tga, vtf)
            self.assertEqual(info.width, 4)
            self.assertEqual(info.height, 4)
            self.assertEqual(info.image_offset, 88)
            # 4x4 with a full mip chain: 4x4 + 2x2 + 1x1 = 21 texels of BGRA.
            self.assertEqual(info.mip_count, 3)
            self.assertEqual(vtf.stat().st_size, 88 + 21 * 4)
            self.assertEqual(inspect_vtf(vtf), info)

    def test_vtex_source_tga_uses_24_bit_when_opaque(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "texture.tga"
            write_uncompressed_tga(path, 4, 4, [1.0, 0.25, 0.0, 1.0] * 16, srgb=True, include_alpha=False)
            info = inspect_tga(path)
            self.assertEqual(info["image_type"], 2)
            self.assertEqual(info["depth"], 24)
            self.assertFalse(info["top_origin"])
            self.assertTrue(info["size_matches"])

    def test_exact_source_skeleton_contract(self) -> None:
        contract = validate_template("male")
        positions = world_positions("male")
        self.assertTrue(contract["passed"])
        self.assertEqual(len(CORE_ORDER), 26)
        self.assertEqual(MALE["ValveBiped.Bip01_Pelvis"][2], (1.570796, 0.0, 0.000001))
        self.assertGreater(positions["ValveBiped.Bip01_L_Hand"][0], 30)
        self.assertLess(positions["ValveBiped.Bip01_R_Hand"][0], -30)

    def test_v211_removes_failed_v204_rig_and_install_paths(self) -> None:
        root = Path(__file__).resolve().parent
        pipeline = (root / "blender" / "pipeline.py").read_text(encoding="utf-8")
        jobs = (root / "ember_gmod" / "jobs.py").read_text(encoding="utf-8")
        runtime = (root / "ember_gmod" / "runtime_addon.py").read_text(encoding="utf-8")
        self.assertNotIn("ARMATURE_AUTO", pipeline)
        self.assertNotIn("obj.evaluated_get", pipeline)
        self.assertIn("exact_stock_smd_bind", pipeline)
        self.assertIn("stock_skeleton_conformance_passed", pipeline)
        self.assertIn("original_mesh_exact_stock_bind", pipeline)
        self.assertIn("robust_edge_deformation", pipeline)
        self.assertIn("--python-exit-code", jobs)
        self.assertIn("install_to_gmod(", jobs)
        self.assertIn("00_ember_{slug}_player.lua", runtime)
        self.assertIn("core_bone_hierarchy_valid", runtime)
        self.assertNotIn("info.SequenceCount", runtime)
        self.assertNotIn("lookup ~= index - 1", runtime)

    def test_v211_microscopic_edge_regression_and_real_explosion_gate(self) -> None:
        safe = robust_edge_deformation([(0.65, 0.67)] * 47999 + [(0.005, 0.354432)], 72.0)
        self.assertTrue(safe["passed"])
        self.assertGreater(safe["raw_maximum_ratio"], 70)
        exploded = robust_edge_deformation([(0.5, 0.52)] * 1000 + [(0.5, 20.0)] * 20, 72.0)
        self.assertFalse(exploded["passed"])
        self.assertGreaterEqual(exploded["severe_outliers"], 20)

    def test_v22_rebakes_the_texture_instead_of_reusing_the_imported_uv_map(self) -> None:
        root = Path(__file__).resolve().parent
        pipeline = (root / "blender" / "pipeline.py").read_text(encoding="utf-8")
        app_js = (root / "web" / "app.js").read_text(encoding="utf-8")
        self.assertLess(
            pipeline.index("bake_source = duplicate_bake_source(obj)"),
            pipeline.index("base_reduction = reduce_base_mesh(obj, base_targets[quality])"),
        )
        self.assertLess(
            pipeline.index("texture_bake = rebuild_atlas_and_bake("),
            pipeline.index("materials = extract_materials("),
        )
        self.assertIn("bpy.ops.uv.smart_project", pipeline)
        self.assertIn("bake.use_selected_to_active = True", pipeline)
        self.assertIn("bpy.ops.object.bake(type=bake_type)", pipeline)
        self.assertIn('if not texture_bake["passed"]:', pipeline)
        self.assertIn("render_material_proof(", pipeline)
        # The compiled files row must be green when the four files exist.
        self.assertNotIn("Array.isArray(value)?value.length===0:true", app_js)
        self.assertIn("PROBLEM_LIST_PATTERN", app_js)

    def test_v22_bake_rules_reject_a_scrambled_atlas(self) -> None:
        from blender.bake_validation import island_report, rasterize_triangles, total_uv_area, uv_bounds_report

        def square(x, y, size):
            return [
                ((x, y), (x + size, y), (x + size, y + size)),
                ((x, y), (x + size, y + size), (x, y + size)),
            ]

        stacked = square(0.05, 0.05, 0.4) * 2
        report = island_report(rasterize_triangles(stacked, 64), 64, total_uv_area(stacked))
        self.assertFalse(report["islands_do_not_overlap"])
        self.assertFalse(uv_bounds_report([(0.5, 0.5), (7.0, -2.0)])["inside_atlas"])
        self.assertFalse(uv_bounds_report([(float("nan"), 0.0)])["all_finite"])

    def test_web_workbench_contract(self) -> None:
        root = Path(__file__).resolve().parent / "web"
        html = (root / "index.html").read_text(encoding="utf-8")
        app = (root / "app.js").read_text(encoding="utf-8")
        viewer = (root / "viewer.js").read_text(encoding="utf-8")
        self.assertIn('id="glCanvas"', html)
        self.assertIn('id="landmarkList"', html)
        self.assertIn('id="lockGuideBtn"', html)
        self.assertIn("REQUIRED_SERVICE_VERSION = '2.3.0'", app)
        self.assertIn("raycast", viewer)
        self.assertIn("autoSeed", viewer)


if __name__ == "__main__":
    unittest.main(verbosity=2)
