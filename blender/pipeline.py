from __future__ import annotations

import json
import math
import os
import re
import shutil
import struct
import sys
import traceback
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import bpy
import bmesh
from mathutils import Euler, Matrix, Vector

from tga_writer import write_uncompressed_tga
from bake_validation import (
    colour_report,
    downsample_mask,
    evaluate_bake,
    island_report,
    painted_mask_from_pixels,
    rasterize_triangles,
    total_uv_area,
    triangle_coverage_report,
    unpainted_region_report,
    uv_bounds_report,
)
from rig_math import anatomical_influences, classify_region, robust_edge_deformation, validate_influences
from source_skeleton import CORE_ORDER, get_template, validate_template


def log(message: str) -> None:
    print(f"[EMBER] {message}", flush=True)


def slugify(value: str) -> str:
    value = value.lower().replace("-", "_").replace(" ", "_")
    value = re.sub(r"[^a-z0-9_]+", "_", value)
    return re.sub(r"_+", "_", value).strip("_") or "character"


def clean_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for block in bpy.data.meshes:
        if block.users == 0:
            bpy.data.meshes.remove(block)


def import_glb(path: Path) -> list[bpy.types.Object]:
    log(f"Importing {path.name}")
    bpy.ops.import_scene.gltf(filepath=str(path))
    meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    if not meshes:
        raise RuntimeError("The GLB imported successfully but contains no mesh objects.")
    return meshes


def join_meshes(meshes: list[bpy.types.Object]) -> bpy.types.Object:
    bpy.ops.object.select_all(action="DESELECT")
    for obj in meshes:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    bpy.ops.object.convert(target="MESH")
    if len(meshes) > 1:
        bpy.ops.object.join()
    obj = bpy.context.view_layer.objects.active
    obj.name = "character_reference"
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    return obj


def cleanup_mesh(obj: bpy.types.Object) -> dict[str, int]:
    mesh = obj.data
    before = len(mesh.vertices)
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.0001)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    return {"vertices_before": before, "vertices_after": len(mesh.vertices)}



def mesh_triangle_count(obj: bpy.types.Object) -> int:
    obj.data.calc_loop_triangles()
    return len(obj.data.loop_triangles)


def reduce_base_mesh(obj: bpy.types.Object, target_triangles: int) -> dict[str, Any]:
    before_triangles = mesh_triangle_count(obj)
    before_vertices = len(obj.data.vertices)
    if before_triangles <= target_triangles:
        return {
            "applied": False,
            "target_triangles": target_triangles,
            "triangles_before": before_triangles,
            "triangles_after": before_triangles,
            "vertices_before": before_vertices,
            "vertices_after": before_vertices,
            "ratio": 1.0,
        }
    ratio = max(0.01, min(1.0, target_triangles / max(1, before_triangles)))
    modifier = obj.modifiers.new(name="Source Base Reduction", type="DECIMATE")
    modifier.decimate_type = "COLLAPSE"
    modifier.ratio = ratio
    modifier.use_collapse_triangulate = True
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.modifier_apply(modifier=modifier.name)
    obj.select_set(False)
    after_triangles = mesh_triangle_count(obj)
    return {
        "applied": True,
        "target_triangles": target_triangles,
        "triangles_before": before_triangles,
        "triangles_after": after_triangles,
        "vertices_before": before_vertices,
        "vertices_after": len(obj.data.vertices),
        "ratio": ratio,
    }

def normalise_character(obj: bpy.types.Object, target_height: float, front_axis: str = "neg_y") -> dict[str, Any]:
    """Normalise the imported GLB into the actual Source humanoid axes.

    The browser workbench uses X forward, Y left and Z up because that is intuitive
    for landmark editing.  Source's stock male and female reference SMDs use X for
    left/right, negative Y for forward and Z for up.  V2.0.4 compiled the mesh in the
    workbench axes and then attached stock animation bones authored in Source axes.
    The model could compile but deformed catastrophically in game.
    """
    # Blender imports glTF as Z up. Rotate the source model so it faces negative Y,
    # matching the stock Garry's Mod humanoid bind skeleton.
    rotation_degrees = {"neg_y": 0.0, "pos_y": 180.0, "pos_x": -90.0, "neg_x": 90.0}.get(front_axis, 0.0)
    obj.rotation_euler[2] += math.radians(rotation_degrees)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)

    coords = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
    min_v = Vector((min(v.x for v in coords), min(v.y for v in coords), min(v.z for v in coords)))
    max_v = Vector((max(v.x for v in coords), max(v.y for v in coords), max(v.z for v in coords)))
    height = max_v.z - min_v.z
    if height <= 1e-6:
        raise RuntimeError("The imported mesh has zero height.")
    scale = target_height / height
    obj.scale = (scale, scale, scale)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)

    coords = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
    min_v = Vector((min(v.x for v in coords), min(v.y for v in coords), min(v.z for v in coords)))
    max_v = Vector((max(v.x for v in coords), max(v.y for v in coords), max(v.z for v in coords)))
    centre_xy = Vector(((min_v.x + max_v.x) / 2.0, (min_v.y + max_v.y) / 2.0, 0.0))
    obj.location -= Vector((centre_xy.x, centre_xy.y, min_v.z))
    bpy.ops.object.transform_apply(location=True, rotation=False, scale=False)

    coords = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
    min_v = Vector((min(v.x for v in coords), min(v.y for v in coords), min(v.z for v in coords)))
    max_v = Vector((max(v.x for v in coords), max(v.y for v in coords), max(v.z for v in coords)))
    return {
        "target_height": target_height,
        "scale_factor": scale,
        "bounds_min": list(min_v),
        "bounds_max": list(max_v),
        "width": max_v.x - min_v.x,
        "depth": max_v.y - min_v.y,
        "height": max_v.z - min_v.z,
        "input_front_axis": front_axis,
        "rotation_to_source_degrees": rotation_degrees,
        "source_axes": "X left, negative Y forward, Z up",
    }


def tool_to_source(point: Vector) -> Vector:
    """Convert browser guide coordinates into Source reference coordinates."""
    return Vector((point.y, -point.x, point.z))


def guide_in_source_axes(guide: dict[str, Any]) -> dict[str, Any]:
    converted = json.loads(json.dumps(guide))
    landmarks = converted.get("landmarks") or {}
    for key, value in list(landmarks.items()):
        landmarks[key] = list(tool_to_source(Vector(tuple(float(v) for v in value))))
    for zone in converted.get("rigid_zones") or []:
        value = zone.get("center")
        if isinstance(value, (list, tuple)) and len(value) == 3:
            zone["center"] = list(tool_to_source(Vector(tuple(float(v) for v in value))))
    converted["coordinate_space"] = "source_humanoid"
    return converted


def validate_source_guide(guide: dict[str, Any], height: float) -> dict[str, Any]:
    """Reject mirrored, crossed or anatomically impossible landmark guides."""
    lm = guide.get("landmarks") or {}
    errors: list[str] = []
    h = max(float(height), 1.0)

    def point(name: str) -> Vector:
        value = lm.get(name)
        if not isinstance(value, (list, tuple)) or len(value) != 3:
            errors.append(f"missing landmark {name}")
            return Vector((0.0, 0.0, 0.0))
        return Vector(tuple(float(v) for v in value))

    names = [
        "head_top", "neck_base", "pelvis",
        "shoulder_l", "elbow_l", "wrist_l", "shoulder_r", "elbow_r", "wrist_r",
        "hip_l", "knee_l", "ankle_l", "toe_l", "hip_r", "knee_r", "ankle_r", "toe_r",
    ]
    pts = {name: point(name) for name in names}
    for base in ("shoulder", "elbow", "wrist", "hip", "knee", "ankle", "toe"):
        if pts[f"{base}_l"].x <= pts[f"{base}_r"].x:
            errors.append(f"{base} left and right sides are crossed in Source X")
    if not (pts["head_top"].z > pts["neck_base"].z > pts["pelvis"].z):
        errors.append("head, neck and pelvis vertical order is invalid")
    for side in ("l", "r"):
        if not (pts[f"hip_{side}"].z > pts[f"knee_{side}"].z > pts[f"ankle_{side}"].z):
            errors.append(f"{side} leg vertical order is invalid")
        if pts[f"toe_{side}"].y >= pts[f"ankle_{side}"].y + h * 0.02:
            errors.append(f"{side} toe points backwards instead of Source forward")
        upper_arm = (pts[f"shoulder_{side}"] - pts[f"elbow_{side}"]).length
        forearm = (pts[f"elbow_{side}"] - pts[f"wrist_{side}"]).length
        thigh = (pts[f"hip_{side}"] - pts[f"knee_{side}"]).length
        calf = (pts[f"knee_{side}"] - pts[f"ankle_{side}"]).length
        for label, length, low, high in (
            ("upper arm", upper_arm, 0.08, 0.30),
            ("forearm", forearm, 0.07, 0.28),
            ("thigh", thigh, 0.14, 0.36),
            ("calf", calf, 0.14, 0.36),
        ):
            if not h * low <= length <= h * high:
                errors.append(f"{side} {label} length {length:.3f} is outside the supported humanoid range")
    shoulder_span = (pts["shoulder_l"] - pts["shoulder_r"]).length
    hip_span = (pts["hip_l"] - pts["hip_r"]).length
    if not h * 0.16 <= shoulder_span <= h * 0.55:
        errors.append(f"shoulder span {shoulder_span:.3f} is outside the supported humanoid range")
    if not h * 0.08 <= hip_span <= h * 0.32:
        errors.append(f"hip span {hip_span:.3f} is outside the supported humanoid range")
    return {
        "passed": not errors,
        "errors": errors,
        "coordinate_space": "Source X left, negative Y forward, Z up",
        "shoulder_span": shoulder_span,
        "hip_span": hip_span,
    }


# Exact SMD bind transforms are stored in source_skeleton.py. They are not the
# `$definebone` fields from commonbones.qci; those use a different representation.


def _v(landmarks: dict[str, Any], key: str) -> Vector:
    value = landmarks.get(key)
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise RuntimeError(f"Required landmark {key} is missing from the locked guide.")
    return Vector((float(value[0]), float(value[1]), float(value[2])))


def _lerp(a: Vector, b: Vector, t: float) -> Vector:
    return a + (b - a) * t


def _safe_direction(a: Vector, b: Vector, fallback: Vector) -> Vector:
    direction = b - a
    return direction.normalized() if direction.length > 1e-5 else fallback.normalized()


def _template_local_matrix(position: tuple[float, float, float], rotation: tuple[float, float, float], factor: float) -> Matrix:
    return Matrix.Translation(Vector(position) * factor) @ Euler(rotation, "XYZ").to_matrix().to_4x4()


def standard_bones(animation_base: str, target_height: float) -> list[dict[str, Any]]:
    """Return the exact stock male or female SMD core bind skeleton."""
    contract = validate_template(animation_base)
    if not contract["passed"]:
        raise RuntimeError(f"Embedded {animation_base} Source skeleton contract is invalid: {contract}")
    template = get_template(animation_base)
    factor = float(target_height) / 72.0
    worlds: dict[str, Matrix] = {}
    for name in CORE_ORDER:
        parent, position, rotation = template[name]
        local = _template_local_matrix(position, rotation, factor)
        worlds[name] = worlds[parent] @ local if parent else local

    primary_child = {
        "ValveBiped.Bip01_Pelvis": "ValveBiped.Bip01_Spine",
        "ValveBiped.Bip01_Spine": "ValveBiped.Bip01_Spine1",
        "ValveBiped.Bip01_Spine1": "ValveBiped.Bip01_Spine2",
        "ValveBiped.Bip01_Spine2": "ValveBiped.Bip01_Spine4",
        "ValveBiped.Bip01_Spine4": "ValveBiped.Bip01_Neck1",
        "ValveBiped.Bip01_Neck1": "ValveBiped.Bip01_Head1",
        "ValveBiped.Bip01_R_Clavicle": "ValveBiped.Bip01_R_UpperArm",
        "ValveBiped.Bip01_R_UpperArm": "ValveBiped.Bip01_R_Forearm",
        "ValveBiped.Bip01_R_Forearm": "ValveBiped.Bip01_R_Hand",
        "ValveBiped.Bip01_R_Hand": "ValveBiped.Anim_Attachment_RH",
        "ValveBiped.Bip01_L_Clavicle": "ValveBiped.Bip01_L_UpperArm",
        "ValveBiped.Bip01_L_UpperArm": "ValveBiped.Bip01_L_Forearm",
        "ValveBiped.Bip01_L_Forearm": "ValveBiped.Bip01_L_Hand",
        "ValveBiped.Bip01_L_Hand": "ValveBiped.Anim_Attachment_LH",
        "ValveBiped.Bip01_R_Thigh": "ValveBiped.Bip01_R_Calf",
        "ValveBiped.Bip01_R_Calf": "ValveBiped.Bip01_R_Foot",
        "ValveBiped.Bip01_R_Foot": "ValveBiped.Bip01_R_Toe0",
        "ValveBiped.Bip01_L_Thigh": "ValveBiped.Bip01_L_Calf",
        "ValveBiped.Bip01_L_Calf": "ValveBiped.Bip01_L_Foot",
        "ValveBiped.Bip01_L_Foot": "ValveBiped.Bip01_L_Toe0",
    }
    bones: list[dict[str, Any]] = []
    for name in CORE_ORDER:
        parent, position, rotation = template[name]
        world = worlds[name]
        head = world.translation
        child = primary_child.get(name)
        if child:
            tail = worlds[child].translation
        else:
            leaf_length = target_height * (0.065 if name == "ValveBiped.Bip01_Head1" else 0.035)
            tail = head + (world.to_3x3() @ Vector((leaf_length, 0.0, 0.0)))
        bones.append({
            "name": name,
            "parent": parent,
            "head": tuple(head),
            "tail": tuple(tail),
            "world_rot": tuple(world.to_euler("XYZ")),
            "local_pos": tuple(Vector(position) * factor),
            "local_rot": tuple(rotation),
            "deform": not (name.startswith("ValveBiped.Anim_Attachment") or name == "ValveBiped.forward"),
            "bind_source": f"stock_{animation_base}_smd",
        })
    return bones


def guided_source_bones(guide: dict[str, Any], bounds: dict[str, Any], animation_base: str) -> list[dict[str, Any]]:
    """Build a guide fitted source bind used only for weighting and conformance.

    Rotational axes come from the exact stock SMD skeleton. Joint locations come
    from the user's landmarks. The mesh is then conformed from this source bind to
    the exact stock target bind before export.
    """
    lm = guide.get("landmarks", {})
    h = float(bounds["height"])
    target = {bone["name"]: bone for bone in standard_bones(animation_base, h)}
    pelvis = _v(lm, "pelvis")
    neck = _v(lm, "neck_base")
    head_top = _v(lm, "head_top")
    shoulder_l, shoulder_r = _v(lm, "shoulder_l"), _v(lm, "shoulder_r")
    chest = (shoulder_l + shoulder_r) * 0.5

    heads: dict[str, Vector] = {
        "ValveBiped.Bip01_Pelvis": pelvis,
        "ValveBiped.Bip01_Spine": _lerp(pelvis, chest, 0.10),
        "ValveBiped.Bip01_Spine1": _lerp(pelvis, chest, 0.30),
        "ValveBiped.Bip01_Spine2": _lerp(pelvis, chest, 0.53),
        "ValveBiped.Bip01_Spine4": _lerp(chest, neck, 0.18),
        "ValveBiped.Bip01_Neck1": neck,
        "ValveBiped.Bip01_Head1": _lerp(neck, head_top, 0.34),
        "ValveBiped.forward": _lerp(neck, head_top, 0.58),
    }
    for side in ("L", "R"):
        suffix = side.lower()
        shoulder = _v(lm, f"shoulder_{suffix}")
        elbow = _v(lm, f"elbow_{suffix}")
        wrist = _v(lm, f"wrist_{suffix}")
        lateral = Vector((1 if side == "L" else -1, 0, 0))
        hand_tip = _v(lm, f"hand_tip_{suffix}") if f"hand_tip_{suffix}" in lm else wrist + _safe_direction(elbow, wrist, lateral) * (h * 0.055)
        heads[f"ValveBiped.Bip01_{side}_Clavicle"] = _lerp(heads["ValveBiped.Bip01_Spine4"], shoulder, 0.22)
        heads[f"ValveBiped.Bip01_{side}_UpperArm"] = shoulder
        heads[f"ValveBiped.Bip01_{side}_Forearm"] = elbow
        heads[f"ValveBiped.Bip01_{side}_Hand"] = wrist
        heads[f"ValveBiped.Anim_Attachment_{side}H"] = _lerp(wrist, hand_tip, 0.62)
        heads[f"ValveBiped.Bip01_{side}_Thigh"] = _v(lm, f"hip_{suffix}")
        heads[f"ValveBiped.Bip01_{side}_Calf"] = _v(lm, f"knee_{suffix}")
        heads[f"ValveBiped.Bip01_{side}_Foot"] = _v(lm, f"ankle_{suffix}")
        heads[f"ValveBiped.Bip01_{side}_Toe0"] = _v(lm, f"toe_{suffix}")

    tails: dict[str, Vector] = {
        "ValveBiped.Bip01_Pelvis": heads["ValveBiped.Bip01_Spine"],
        "ValveBiped.Bip01_Spine": heads["ValveBiped.Bip01_Spine1"],
        "ValveBiped.Bip01_Spine1": heads["ValveBiped.Bip01_Spine2"],
        "ValveBiped.Bip01_Spine2": heads["ValveBiped.Bip01_Spine4"],
        "ValveBiped.Bip01_Spine4": heads["ValveBiped.Bip01_Neck1"],
        "ValveBiped.Bip01_Neck1": heads["ValveBiped.Bip01_Head1"],
        "ValveBiped.Bip01_Head1": head_top,
        "ValveBiped.forward": heads["ValveBiped.forward"] + Vector((0, -h * 0.05, 0)),
    }
    for side in ("L", "R"):
        suffix = side.lower()
        shoulder = heads[f"ValveBiped.Bip01_{side}_UpperArm"]
        elbow = heads[f"ValveBiped.Bip01_{side}_Forearm"]
        wrist = heads[f"ValveBiped.Bip01_{side}_Hand"]
        lateral = Vector((1 if side == "L" else -1, 0, 0))
        hand_tip = _v(lm, f"hand_tip_{suffix}") if f"hand_tip_{suffix}" in lm else wrist + _safe_direction(elbow, wrist, lateral) * (h * 0.055)
        tails[f"ValveBiped.Bip01_{side}_Clavicle"] = shoulder
        tails[f"ValveBiped.Bip01_{side}_UpperArm"] = elbow
        tails[f"ValveBiped.Bip01_{side}_Forearm"] = wrist
        tails[f"ValveBiped.Bip01_{side}_Hand"] = hand_tip
        tails[f"ValveBiped.Anim_Attachment_{side}H"] = hand_tip + lateral * (h * 0.015)
        tails[f"ValveBiped.Bip01_{side}_Thigh"] = heads[f"ValveBiped.Bip01_{side}_Calf"]
        tails[f"ValveBiped.Bip01_{side}_Calf"] = heads[f"ValveBiped.Bip01_{side}_Foot"]
        tails[f"ValveBiped.Bip01_{side}_Foot"] = heads[f"ValveBiped.Bip01_{side}_Toe0"]
        foot_dir = _safe_direction(heads[f"ValveBiped.Bip01_{side}_Foot"], heads[f"ValveBiped.Bip01_{side}_Toe0"], Vector((0, -1, 0)))
        tails[f"ValveBiped.Bip01_{side}_Toe0"] = heads[f"ValveBiped.Bip01_{side}_Toe0"] + foot_dir * (h * 0.035)

    bones: list[dict[str, Any]] = []
    for name in CORE_ORDER:
        target_spec = target[name]
        bones.append({
            "name": name,
            "parent": target_spec["parent"],
            "head": tuple(heads[name]),
            "tail": tuple(tails[name]),
            "world_rot": target_spec["world_rot"],
            "deform": target_spec["deform"],
            "bind_source": "guided_source",
        })
    return bones


def _bone_world_matrices(bones: list[dict[str, Any]]) -> list[Matrix]:
    return [Matrix.Translation(Vector(bone["head"])) @ Euler(tuple(bone["world_rot"]), "XYZ").to_matrix().to_4x4() for bone in bones]


def conform_mesh_to_standard_skeleton(obj: bpy.types.Object, source_bones: list[dict[str, Any]], target_bones: list[dict[str, Any]], height: float) -> dict[str, Any]:
    """Prepare a normal humanoid mesh for the exact stock Source bind.

    The first 2.1 release always committed a per-bone bind warp before it knew the
    result was safe. Generated meshes contain microscopic decimation edges and hard
    shell boundaries, so a single tiny edge could report a huge ratio and block the
    build. Worse, committing that warp could create a visible seam even when the
    source and stock skeletons were already only a few units apart.

    This revision evaluates the candidate without modifying the mesh. It applies the
    warp only when robust edge diagnostics are clean. When the guide is already close
    to the stock bind, it deliberately preserves the original mesh and uses the exact
    stock SMD skeleton only for animation. This is the safer path for an ordinary T or
    A pose humanoid and cannot tear the reference topology.
    """
    if [bone["name"] for bone in source_bones] != [bone["name"] for bone in target_bones]:
        raise RuntimeError("Source and target skeleton contracts do not match.")
    bone_ids = {bone["name"]: index for index, bone in enumerate(target_bones)}
    source_world = _bone_world_matrices(source_bones)
    target_world = _bone_world_matrices(target_bones)
    transforms = [target_world[index] @ source_world[index].inverted() for index in range(len(target_bones))]
    before = [obj.matrix_world @ vertex.co for vertex in obj.data.vertices]
    candidate: list[Vector] = []
    displacements: list[float] = []
    non_finite = 0
    for vertex, rest_world in zip(obj.data.vertices, before):
        conformed = Vector((0.0, 0.0, 0.0))
        influences = _vertex_influences(obj, vertex, bone_ids)
        for bone_id, weight in influences:
            conformed += (transforms[bone_id] @ rest_world) * weight
        if not all(math.isfinite(value) for value in conformed):
            non_finite += 1
            conformed = rest_world.copy()
        displacements.append((conformed - rest_world).length)
        candidate.append(conformed)

    def dimensions(points: list[Vector]) -> tuple[Vector, Vector, Vector]:
        minimum = Vector((min(p.x for p in points), min(p.y for p in points), min(p.z for p in points)))
        maximum = Vector((max(p.x for p in points), max(p.y for p in points), max(p.z for p in points)))
        return minimum, maximum, maximum - minimum

    before_min, before_max, before_dims = dimensions(before)
    after_min, after_max, after_dims = dimensions(candidate)
    edge_pairs: list[tuple[float, float]] = []
    stride = max(1, len(obj.data.edges) // 32000)
    for edge_index in range(0, len(obj.data.edges), stride):
        first, second = obj.data.edges[edge_index].vertices
        old_length = (before[first] - before[second]).length
        new_length = (candidate[first] - candidate[second]).length
        edge_pairs.append((old_length, new_length))
    edge_stats = robust_edge_deformation(edge_pairs, height)

    maximum = max(displacements, default=0.0)
    average = sum(displacements) / max(len(displacements), 1)
    lateral_ok = height * 0.48 <= after_dims.x <= height * 1.35
    depth_ok = height * 0.035 <= after_dims.y <= height * 0.60
    vertical_ok = height * 0.72 <= after_dims.z <= height * 1.20 and after_min.z >= -height * 0.12

    # A guide within roughly one hand-width of the stock bind does not need a
    # destructive per-bone warp. The user's failing Jack Hegarty build measured a
    # 4.16-unit maximum and 2.80-unit average mismatch at 72 units tall, well inside
    # this compatibility envelope.
    stock_compatible_without_warp = (
        non_finite == 0
        and maximum <= height * 0.12
        and average <= height * 0.07
        and lateral_ok and depth_ok and vertical_ok
    )
    candidate_safe = (
        non_finite == 0
        and maximum <= height * 0.35
        and average <= height * 0.14
        and lateral_ok and depth_ok and vertical_ok
        and bool(edge_stats["passed"])
    )

    # A raw extreme above 12x with otherwise small bind mismatch is characteristic
    # of a microscopic decimation edge or a hard shell boundary. Preserve the mesh
    # instead of baking that discontinuity into the player model.
    localized_edge_discontinuity = (
        float(edge_stats["raw_maximum_ratio"]) > 12.0
        or float(edge_stats["raw_minimum_ratio"]) < 0.035
        or int(edge_stats["severe_outliers"]) > 0
    )
    use_original_mesh = stock_compatible_without_warp and localized_edge_discontinuity
    if candidate_safe and not use_original_mesh:
        inverse_object = obj.matrix_world.inverted()
        for vertex, conformed in zip(obj.data.vertices, candidate):
            vertex.co = inverse_object @ conformed
        obj.data.update()
        mode = "guided_to_stock_lbs"
        applied = True
        fallback_reason = ""
    elif stock_compatible_without_warp:
        mode = "original_mesh_exact_stock_bind"
        applied = False
        fallback_reason = "Candidate bind warp contained localized edge discontinuities; original topology was preserved."
    else:
        mode = "rejected"
        applied = False
        fallback_reason = "Guide-to-stock mismatch is too large for either the validated warp or the no-warp compatibility path."

    passed = applied or stock_compatible_without_warp
    final_min, final_max, final_dims = (after_min, after_max, after_dims) if applied else (before_min, before_max, before_dims)
    return {
        "passed": passed,
        "mode": mode,
        "conformance_applied": applied,
        "mesh_vertices_modified": len(candidate) if applied else 0,
        "vertices_evaluated": len(candidate),
        "vertices_conformed": len(candidate) if applied else 0,
        "edges_tested": int(edge_stats["samples"]),
        "non_finite_vertices": non_finite,
        "maximum_displacement": maximum,
        "average_displacement": average,
        "minimum_edge_ratio": float(edge_stats["raw_minimum_ratio"]),
        "maximum_edge_ratio": float(edge_stats["raw_maximum_ratio"]),
        "edge_diagnostics": edge_stats,
        "before_dimensions": list(before_dims),
        "candidate_dimensions": list(after_dims),
        "final_dimensions": list(final_dims),
        "candidate_bounds_min": list(after_min),
        "candidate_bounds_max": list(after_max),
        "final_bounds_min": list(final_min),
        "final_bounds_max": list(final_max),
        "lateral_extent_valid": lateral_ok,
        "depth_extent_valid": depth_ok,
        "vertical_extent_valid": vertical_ok,
        "candidate_edge_deformation_valid": bool(edge_stats["passed"]),
        "edge_deformation_valid": bool(edge_stats["passed"]) if applied else True,
        "stock_compatible_without_warp": stock_compatible_without_warp,
        "localized_edge_discontinuity": localized_edge_discontinuity,
        "fallback_reason": fallback_reason,
        "source_axes": "X left, negative Y forward, Z up",
        "target_bind": "exact stock SMD core",
    }

def create_armature(bones: list[dict[str, Any]]) -> bpy.types.Object:
    arm_data = bpy.data.armatures.new("ValveBiped")
    arm_obj = bpy.data.objects.new("ValveBiped", arm_data)
    bpy.context.collection.objects.link(arm_obj)
    bpy.context.view_layer.objects.active = arm_obj
    arm_obj.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    edit_bones: dict[str, bpy.types.EditBone] = {}
    for spec in bones:
        eb = arm_data.edit_bones.new(spec["name"])
        eb.head = Vector(spec["head"])
        eb.tail = Vector(spec["tail"])
        if (eb.tail - eb.head).length < .01:
            eb.tail = eb.head + Vector((0.5, 0.0, 0.0))
        eb.use_deform = bool(spec.get("deform", True))
        edit_bones[spec["name"]] = eb
    for spec in bones:
        parent_name = spec.get("parent")
        if parent_name and parent_name in edit_bones:
            edit_bones[spec["name"]].parent = edit_bones[parent_name]
            edit_bones[spec["name"]].use_connect = False
    bpy.ops.object.mode_set(mode="OBJECT")
    arm_obj.show_in_front = True
    return arm_obj

def point_segment_distance(point: Vector, a: Vector, b: Vector) -> float:
    ab = b - a
    denom = ab.length_squared
    if denom <= 1e-9:
        return (point - a).length
    t = max(0.0, min(1.0, (point - a).dot(ab) / denom))
    return (point - (a + ab * t)).length


def _prune_weights(obj: bpy.types.Object, max_influences: int = 3) -> dict[str, Any]:
    unweighted = 0
    observed = 0
    for vertex in obj.data.vertices:
        influences = []
        for item in vertex.groups:
            if item.group < len(obj.vertex_groups) and item.weight > 0.00001:
                influences.append((item.group, float(item.weight)))
        influences.sort(key=lambda pair: pair[1], reverse=True)
        keep = influences[:max_influences]
        remove = influences[max_influences:]
        for group_index, _weight in remove:
            obj.vertex_groups[group_index].remove([vertex.index])
        total = sum(weight for _idx, weight in keep)
        if total <= 1e-8:
            unweighted += 1
            pelvis = obj.vertex_groups.get("ValveBiped.Bip01_Pelvis")
            if pelvis:
                pelvis.add([vertex.index], 1.0, "REPLACE")
            continue
        observed = max(observed, len(keep))
        for group_index, weight in keep:
            obj.vertex_groups[group_index].add([vertex.index], weight / total, "REPLACE")
    return {"unweighted_vertices": unweighted, "max_influences": observed}


def _geometric_weights(obj: bpy.types.Object, bones: list[dict[str, Any]], height: float) -> dict[str, Any]:
    """Assign deterministic region constrained humanoid weights.

    Bone heat was the source of the v2.0.4 collapsed ragdoll failure. Generated
    Hunyuan meshes contain disconnected shells and close surfaces, which lets heat
    leak between the torso, arms and legs. Every vertex is now classified into one
    anatomical region before it is blended across at most three adjacent bones.
    """
    deform = [bone for bone in bones if bone.get("deform", True)]
    groups = {bone["name"]: obj.vertex_groups.get(bone["name"]) or obj.vertex_groups.new(name=bone["name"]) for bone in deform}
    segments = {
        bone["name"]: (tuple(float(v) for v in bone["head"]), tuple(float(v) for v in bone["tail"]))
        for bone in deform
    }
    region_counts: dict[str, int] = {}
    for vert in obj.data.vertices:
        point_v = obj.matrix_world @ vert.co
        point = (float(point_v.x), float(point_v.y), float(point_v.z))
        region = classify_region(point, segments, height)
        region_counts[region] = region_counts.get(region, 0) + 1
        influences = anatomical_influences(point, segments, height, 3)
        validate_influences(influences, 3)
        for name, weight in influences:
            groups[name].add([vert.index], float(weight), "REPLACE")
    return {"region_counts": region_counts}


def assign_weights(obj: bpy.types.Object, armature: bpy.types.Object, bones: list[dict[str, Any]], height: float) -> dict[str, Any]:
    for group in list(obj.vertex_groups):
        obj.vertex_groups.remove(group)
    anatomical = _geometric_weights(obj, bones, height)
    modifier = obj.modifiers.get("ValveBiped Armature") or obj.modifiers.new(name="ValveBiped Armature", type="ARMATURE")
    modifier.object = armature
    # Do not parent the mesh to the armature. The Source reference SMD contains
    # absolute rest geometry, and parenting introduced an unnecessary transform path.
    obj.parent = None
    result = _prune_weights(obj, 3)
    result["method"] = "anatomical_region_v2"
    result["region_counts"] = anatomical["region_counts"]
    result["auto_weight_error"] = None
    return result


def apply_rigid_zones(obj: bpy.types.Object, zones: list[dict[str, Any]]) -> dict[str, Any]:
    applied = 0
    vertices = 0
    for zone in zones:
        center = Vector(tuple(float(v) for v in zone.get("center", (0, 0, 0))))
        radius = float(zone.get("radius", 0.0))
        bone = str(zone.get("bone", ""))
        group = obj.vertex_groups.get(bone)
        if not group or radius <= 0:
            continue
        selected = [vert.index for vert in obj.data.vertices if ((obj.matrix_world @ vert.co) - center).length <= radius]
        if not selected:
            continue
        for other in obj.vertex_groups:
            try:
                other.remove(selected)
            except RuntimeError:
                pass
        group.add(selected, 1.0, "REPLACE")
        applied += 1
        vertices += len(selected)
    return {"zones_applied": applied, "vertices_rigid": vertices}


def rigidify_small_components(obj: bpy.types.Object, bones: list[dict[str, Any]], height: float) -> dict[str, Any]:
    mesh = obj.data
    total = len(mesh.vertices)
    if total == 0:
        return {"components": 0, "rigid_components": 0, "rigid_vertices": 0}
    adjacency = [set() for _ in range(total)]
    for edge in mesh.edges:
        a, b = edge.vertices
        adjacency[a].add(b)
        adjacency[b].add(a)
    seen = set()
    components: list[list[int]] = []
    for start in range(total):
        if start in seen:
            continue
        stack = [start]
        seen.add(start)
        component = []
        while stack:
            idx = stack.pop()
            component.append(idx)
            for nxt in adjacency[idx]:
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        components.append(component)
    segments = {b["name"]: (Vector(b["head"]), Vector(b["tail"])) for b in bones if b.get("deform", True) and obj.vertex_groups.get(b["name"])}
    rigid_components = 0
    rigid_vertices = 0
    max_component = max(24, int(total * 0.055))
    for component in components:
        if len(component) < 4 or len(component) > max_component:
            continue
        points = [obj.matrix_world @ mesh.vertices[i].co for i in component]
        minimum = Vector((min(v.x for v in points), min(v.y for v in points), min(v.z for v in points)))
        maximum = Vector((max(v.x for v in points), max(v.y for v in points), max(v.z for v in points)))
        dimensions = maximum - minimum
        if max(dimensions) > height * 0.19:
            continue
        centroid = sum(points, Vector((0, 0, 0))) / len(points)
        influence = anatomical_influences(tuple(centroid), {name: (tuple(a), tuple(b)) for name, (a, b) in segments.items()}, height, 1)
        nearest = influence[0][0]
        for group in obj.vertex_groups:
            try:
                group.remove(component)
            except RuntimeError:
                pass
        obj.vertex_groups[nearest].add(component, 1.0, "REPLACE")
        rigid_components += 1
        rigid_vertices += len(component)
    return {"components": len(components), "rigid_components": rigid_components, "rigid_vertices": rigid_vertices}

def split_atlas_seams(obj: bpy.types.Object) -> int:
    """Disconnect the mesh along every UV seam before a Decimate pass.

    Collapse decimation merges vertices without regard for UV islands.  When a
    seam vertex is merged, loops from two unrelated islands become one and the
    resulting triangles sample the wrong part of the atlas.  Splitting the seams
    first makes those vertices topological boundaries, which collapse preserves,
    so the reduced LOD keeps sampling the atlas region it belongs to.
    """
    mesh = obj.data
    if not mesh.uv_layers.active:
        return 0
    layer_name = mesh.uv_layers.active.name
    bm = bmesh.new()
    bm.from_mesh(mesh)
    uv_layer = bm.loops.layers.uv.get(layer_name) or bm.loops.layers.uv.active
    if uv_layer is None:
        bm.free()
        return 0
    seams = []
    for edge in bm.edges:
        if len(edge.link_faces) != 2:
            continue
        for vert in edge.verts:
            coordinates = []
            for face in edge.link_faces:
                for loop in face.loops:
                    if loop.vert is vert:
                        coordinates.append((loop[uv_layer].uv[0], loop[uv_layer].uv[1]))
                        break
            if len(coordinates) == 2 and (
                abs(coordinates[0][0] - coordinates[1][0]) > 1e-5
                or abs(coordinates[0][1] - coordinates[1][1]) > 1e-5
            ):
                seams.append(edge)
                break
    if seams:
        bmesh.ops.split_edges(bm, edges=seams)
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    return len(seams)


def duplicate_lod(source: bpy.types.Object, name: str, ratio: float) -> bpy.types.Object:
    obj = source.copy()
    obj.data = source.data.copy()
    obj.name = name
    bpy.context.collection.objects.link(obj)
    obj.parent = source.parent

    # Apply topology reduction before the armature modifier. Blender otherwise
    # warns that the applied modifier is not first and can produce inconsistent LODs.
    armatures = [mod.object for mod in obj.modifiers if mod.type == "ARMATURE" and mod.object]
    for existing in list(obj.modifiers):
        obj.modifiers.remove(existing)

    if ratio < .999:
        split = split_atlas_seams(obj)
        if split:
            log(f"Protected {split} UV seam edges on {name} before reduction.")
        mod = obj.modifiers.new(name="LOD Decimate", type="DECIMATE")
        mod.decimate_type = "COLLAPSE"
        mod.ratio = ratio
        mod.use_collapse_triangulate = True
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        try:
            bpy.ops.object.modifier_apply(modifier=mod.name)
        except RuntimeError:
            log(f"Decimate modifier could not be applied to {name}; using original topology.")
        obj.select_set(False)

    for armature in armatures:
        armature_modifier = obj.modifiers.new(name="ValveBiped Armature", type="ARMATURE")
        armature_modifier.object = armature
    return obj



def create_hands_source(source: bpy.types.Object) -> bpy.types.Object | None:
    arm_names = {
        "ValveBiped.Bip01_L_Clavicle", "ValveBiped.Bip01_R_Clavicle",
        "ValveBiped.Bip01_L_UpperArm", "ValveBiped.Bip01_R_UpperArm",
        "ValveBiped.Bip01_L_Forearm", "ValveBiped.Bip01_R_Forearm",
        "ValveBiped.Bip01_L_Hand", "ValveBiped.Bip01_R_Hand",
    }
    keep = []
    for vert in source.data.vertices:
        retained = False
        for item in vert.groups:
            if item.group < len(source.vertex_groups):
                name = source.vertex_groups[item.group].name
                if name in arm_names and item.weight > 0.08:
                    retained = True
                    break
        if retained:
            keep.append(vert.index)
    if len(keep) < 20:
        return None
    obj = source.copy()
    obj.data = source.data.copy()
    obj.name = "character_hands_source"
    bpy.context.collection.objects.link(obj)
    obj.parent = source.parent
    group = obj.vertex_groups.new(name="EMBER_HANDS_KEEP")
    group.add(keep, 1.0, "REPLACE")
    mod = obj.modifiers.new(name="Hands Mask", type="MASK")
    mod.vertex_group = group.name
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    try:
        bpy.ops.object.modifier_apply(modifier=mod.name)
    except RuntimeError:
        bpy.data.objects.remove(obj, do_unlink=True)
        return None
    if group.name in obj.vertex_groups:
        obj.vertex_groups.remove(obj.vertex_groups[group.name])
    obj.select_set(False)
    return obj

def create_physics_mesh(armature: bpy.types.Object, bones: list[dict[str, Any]], height: float) -> bpy.types.Object:
    use_names = [
        "ValveBiped.Bip01_Pelvis", "ValveBiped.Bip01_Spine1", "ValveBiped.Bip01_Head1",
        "ValveBiped.Bip01_L_UpperArm", "ValveBiped.Bip01_R_UpperArm",
        "ValveBiped.Bip01_L_Forearm", "ValveBiped.Bip01_R_Forearm",
        "ValveBiped.Bip01_L_Thigh", "ValveBiped.Bip01_R_Thigh",
        "ValveBiped.Bip01_L_Calf", "ValveBiped.Bip01_R_Calf",
        "ValveBiped.Bip01_L_Foot", "ValveBiped.Bip01_R_Foot",
    ]
    by_name = {b["name"]: b for b in bones}
    parts = []
    for name in use_names:
        if name not in by_name:
            continue
        spec = by_name[name]
        head, tail = Vector(spec["head"]), Vector(spec["tail"])
        centre = (head + tail) * .5
        length = max((tail - head).length, height * .04)
        bpy.ops.mesh.primitive_cube_add(size=1.0, location=centre)
        part = bpy.context.object
        part.name = "phys_" + slugify(name)
        if "Pelvis" in name or "Spine" in name:
            part.dimensions = (height * .12, height * .26, length * 1.2)
        elif "Head" in name:
            part.dimensions = (height * .14, height * .15, height * .15)
        elif "Foot" in name:
            part.dimensions = (height * .16, height * .08, height * .06)
        else:
            part.dimensions = (height * .075, height * .075, length)
        direction = tail - head
        if direction.length > .001:
            part.rotation_mode = "QUATERNION"
            part.rotation_quaternion = Vector((0, 0, 1)).rotation_difference(direction.normalized())
        bpy.context.view_layer.objects.active = part
        part.select_set(True)
        bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
        group = part.vertex_groups.new(name=name)
        group.add(list(range(len(part.data.vertices))), 1.0, "REPLACE")
        parts.append(part)
    bpy.ops.object.select_all(action="DESELECT")
    for part in parts:
        part.select_set(True)
    bpy.context.view_layer.objects.active = parts[0]
    bpy.ops.object.join()
    physics = bpy.context.object
    physics.name = "character_physics"
    physics.parent = armature
    modifier = physics.modifiers.new(name="ValveBiped Armature", type="ARMATURE")
    modifier.object = armature
    return physics


BAKE_SENTINEL = (1.0, 0.0, 1.0, 0.0)
BAKE_RASTER_RESOLUTION = 256
BAKE_UV_LAYER = "ember_bake"


def duplicate_bake_source(obj: bpy.types.Object) -> bpy.types.Object:
    """Keep a full resolution textured copy of the imported GLB.

    Everything after this point reduces, re-atlases and re-rigs the working mesh.
    The copy stays exactly as the artist authored it so the bake has an accurate
    surface to read colour from.
    """
    copy = obj.copy()
    copy.data = obj.data.copy()
    copy.name = "character_bake_source"
    collections = list(obj.users_collection) or [bpy.context.scene.collection]
    for collection in collections:
        collection.objects.link(copy)
    # Detach the material datablocks so the alpha probe can rewire the copy
    # without ever touching the shaders on the working mesh.
    for slot in copy.material_slots:
        if slot.material is not None:
            slot.material = slot.material.copy()
    copy.select_set(False)
    return copy


def _fit_uvs_into_atlas(mesh: bpy.types.Mesh) -> dict[str, Any]:
    """Force every UV coordinate inside the 0 to 1 atlas without distortion."""
    layer = mesh.uv_layers.active
    if layer is None or not len(layer.data):
        return {"rescaled": False, "scale": 1.0}
    min_u = min_v = float("inf")
    max_u = max_v = float("-inf")
    for item in layer.data:
        u, v = float(item.uv[0]), float(item.uv[1])
        if not (math.isfinite(u) and math.isfinite(v)):
            item.uv = (0.0, 0.0)
            u = v = 0.0
        min_u, max_u = min(min_u, u), max(max_u, u)
        min_v, max_v = min(min_v, v), max(max_v, v)
    span = max(max_u - min_u, max_v - min_v, 1e-6)
    if min_u >= -1e-6 and min_v >= -1e-6 and max_u <= 1.0 + 1e-6 and max_v <= 1.0 + 1e-6:
        return {"rescaled": False, "scale": 1.0}
    scale = (1.0 - 2e-3) / span
    for item in layer.data:
        u, v = float(item.uv[0]), float(item.uv[1])
        item.uv = (1e-3 + (u - min_u) * scale, 1e-3 + (v - min_v) * scale)
    log(f"Rescaled the rebuilt UV atlas by {scale:.5f} so every island sits inside 0 to 1.")
    return {"rescaled": True, "scale": scale}


def rebuild_uv_atlas(obj: bpy.types.Object, texture_size: int) -> dict[str, Any]:
    """Replace the imported UV map with a fresh non overlapping atlas.

    The original glTF UV coordinates belong to the original 499k triangle
    topology.  Decimate collapses vertices that sit on UV seams, which merges
    loops from unrelated atlas islands and makes surviving triangles sample the
    wrong part of the texture.  A rebuilt atlas is generated from the reduced
    topology itself, so no such mismatch can exist.
    """
    mesh = obj.data
    discarded = [layer.name for layer in mesh.uv_layers]
    while mesh.uv_layers:
        mesh.uv_layers.remove(mesh.uv_layers[0])
    layer = mesh.uv_layers.new(name=BAKE_UV_LAYER)
    mesh.uv_layers.active = layer
    layer.active_render = True

    island_margin = max(0.004, 6.0 / max(64.0, float(texture_size)))
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    signature: dict[str, Any] | None = None
    try:
        bpy.ops.mesh.select_all(action="SELECT")
        candidates: list[dict[str, Any]] = [
            {
                "angle_limit": math.radians(66.0),
                "island_margin": island_margin,
                "area_weight": 0.0,
                "correct_aspect": True,
                "scale_to_bounds": False,
            },
            {"angle_limit": math.radians(66.0), "island_margin": island_margin},
            {"island_margin": island_margin},
            {},
        ]
        for kwargs in candidates:
            try:
                bpy.ops.uv.smart_project(**kwargs)
            except TypeError:
                continue
            signature = kwargs
            break
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")
    obj.select_set(False)
    if signature is None:
        raise RuntimeError("Blender rejected every supported smart UV project signature.")
    fit = _fit_uvs_into_atlas(mesh)
    log(f"Rebuilt the UV atlas on the reduced mesh with a {island_margin:.5f} island margin.")
    return {
        "layer": BAKE_UV_LAYER,
        "discarded_layers": discarded,
        "island_margin": island_margin,
        "smart_project_arguments": sorted(signature.keys()),
        "atlas_refit": fit,
    }


def uv_triangles(obj: bpy.types.Object) -> list[tuple[tuple[float, float], ...]]:
    mesh = obj.data
    mesh.calc_loop_triangles()
    layer = mesh.uv_layers.active
    if layer is None:
        return []
    data = layer.data
    return [tuple(tuple(data[loop].uv) for loop in tri.loops) for tri in mesh.loop_triangles]


def uv_coordinates(obj: bpy.types.Object) -> list[tuple[float, float]]:
    layer = obj.data.uv_layers.active
    return [tuple(item.uv) for item in layer.data] if layer else []


def create_bake_image(name: str, size: int) -> bpy.types.Image:
    for existing in list(bpy.data.images):
        if existing.name == name:
            bpy.data.images.remove(existing)
    image = bpy.data.images.new(name, width=int(size), height=int(size), alpha=True, float_buffer=False)
    image.colorspace_settings.name = "sRGB"
    image.alpha_mode = "STRAIGHT"
    return image


def fill_image(image: bpy.types.Image, colour: tuple[float, float, float, float]) -> None:
    from array import array

    count = int(image.size[0]) * int(image.size[1])
    image.pixels.foreach_set(array("f", colour) * count)


def read_image_pixels(image: bpy.types.Image):
    from array import array

    count = int(image.size[0]) * int(image.size[1]) * 4
    values = array("f", [0.0]) * count
    image.pixels.foreach_get(values)
    return values


def assign_baked_material(obj: bpy.types.Object, image: bpy.types.Image, name: str) -> bpy.types.Material:
    """Give the reduced mesh a single material driven by the bake target."""
    mesh = obj.data
    mesh.materials.clear()
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    tree = material.node_tree
    nodes, links = tree.nodes, tree.links
    principled = next((node for node in nodes if node.type == "BSDF_PRINCIPLED"), None)
    output = next((node for node in nodes if node.type == "OUTPUT_MATERIAL"), None)
    if output is None:
        output = nodes.new("ShaderNodeOutputMaterial")
    if principled is None:
        principled = nodes.new("ShaderNodeBsdfPrincipled")
        links.new(principled.outputs["BSDF"], output.inputs["Surface"])
    texture = nodes.new("ShaderNodeTexImage")
    texture.image = image
    texture.location = (-360, 260)
    links.new(texture.outputs["Color"], principled.inputs["Base Color"])
    for socket_name, value in (("Metallic", 0.0), ("Roughness", 0.65)):
        socket = principled.inputs.get(socket_name)
        if socket is not None:
            socket.default_value = value
    for node in nodes:
        node.select = False
    texture.select = True
    nodes.active = texture
    mesh.materials.append(material)
    for polygon in mesh.polygons:
        polygon.material_index = 0
    return material


def _bake_settings(extrusion: float, ray_distance: float, margin_pixels: int) -> None:
    scene = bpy.context.scene
    try:
        scene.render.engine = "CYCLES"
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Cycles is required to bake the reduced mesh texture.") from exc
    cycles = getattr(scene, "cycles", None)
    if cycles is not None:
        cycles.samples = 1
        for attribute, value in (("device", "CPU"), ("use_denoising", False), ("use_adaptive_sampling", False)):
            try:
                setattr(cycles, attribute, value)
            except (AttributeError, TypeError, ValueError):
                continue
    bake = scene.render.bake
    bake.use_selected_to_active = True
    bake.use_cage = False
    bake.cage_extrusion = extrusion
    bake.margin = int(margin_pixels)
    bake.use_clear = False
    bake.use_pass_direct = False
    bake.use_pass_indirect = False
    bake.use_pass_color = True
    for attribute, value in (("max_ray_distance", ray_distance), ("margin_type", "ADJACENT_FACES"), ("target", "IMAGE_TEXTURES")):
        try:
            setattr(bake, attribute, value)
        except (AttributeError, TypeError, ValueError):
            continue


def bake_selected_to_active(
    bake_source: bpy.types.Object,
    target: bpy.types.Object,
    bake_type: str,
    extrusion: float,
    ray_distance: float,
    margin_pixels: int,
) -> None:
    """Project the low resolution target onto the detailed source and bake it."""
    _bake_settings(extrusion, ray_distance, margin_pixels)
    bpy.ops.object.select_all(action="DESELECT")
    bake_source.select_set(True)
    target.select_set(True)
    bpy.context.view_layer.objects.active = target
    bpy.ops.object.bake(type=bake_type)
    bake_source.select_set(False)
    target.select_set(False)


def source_alpha_required(bake_source: bpy.types.Object) -> bool:
    for slot in bake_source.material_slots:
        material = slot.material
        if material is None:
            continue
        if str(getattr(material, "blend_method", "OPAQUE")).upper() in {"CLIP", "BLEND", "HASHED"}:
            return True
        if str(getattr(material, "surface_render_method", "")).upper() in {"BLENDED", "DITHERED"}:
            return True
        if not (material.use_nodes and material.node_tree):
            continue
        for node in material.node_tree.nodes:
            if node.type != "BSDF_PRINCIPLED":
                continue
            socket = node.inputs.get("Alpha")
            if socket is None:
                continue
            if socket.is_linked or float(socket.default_value) < 0.999:
                return True
    return False


def _rewire_materials_to_alpha(bake_source: bpy.types.Object) -> list[tuple[Any, Any, Any]]:
    """Route each source material's alpha into an emission shader for baking."""
    restore: list[tuple[Any, Any, Any]] = []
    for slot in bake_source.material_slots:
        material = slot.material
        if material is None or not material.use_nodes or not material.node_tree:
            continue
        tree = material.node_tree
        output = next((node for node in tree.nodes if node.type == "OUTPUT_MATERIAL" and node.is_active_output), None)
        if output is None:
            output = next((node for node in tree.nodes if node.type == "OUTPUT_MATERIAL"), None)
        if output is None:
            continue
        surface = output.inputs["Surface"]
        previous = surface.links[0].from_socket if surface.is_linked else None
        emission = tree.nodes.new("ShaderNodeEmission")
        emission.name = "EMBER_ALPHA_PROBE"
        principled = next((node for node in tree.nodes if node.type == "BSDF_PRINCIPLED"), None)
        alpha_socket = principled.inputs.get("Alpha") if principled else None
        if alpha_socket is not None and alpha_socket.is_linked:
            tree.links.new(alpha_socket.links[0].from_socket, emission.inputs["Color"])
        else:
            value = float(alpha_socket.default_value) if alpha_socket is not None else 1.0
            emission.inputs["Color"].default_value = (value, value, value, 1.0)
        tree.links.new(emission.outputs["Emission"], surface)
        restore.append((tree, output, previous))
    return restore


def _restore_materials(restore: list[tuple[Any, Any, Any]]) -> None:
    for tree, output, previous in restore:
        probe = tree.nodes.get("EMBER_ALPHA_PROBE")
        if probe is not None:
            tree.nodes.remove(probe)
        if previous is not None:
            tree.links.new(previous, output.inputs["Surface"])


def bake_alpha_channel(
    bake_source: bpy.types.Object,
    target: bpy.types.Object,
    image: bpy.types.Image,
    painted: list[int],
    extrusion: float,
    ray_distance: float,
    margin_pixels: int,
) -> dict[str, Any]:
    """Bake the source alpha and merge it into the colour atlas.

    Losing alpha would turn hair cards and cut out clothing into solid blocks, so
    the transparency is transferred with the same projection as the colour.
    """
    size = int(image.size[0])
    probe = create_bake_image(f"{image.name}_alpha", size)
    node = None
    restore: list[tuple[Any, Any, Any]] = []
    try:
        fill_image(probe, (1.0, 1.0, 1.0, 1.0))
        tree = target.data.materials[0].node_tree
        node = tree.nodes.new("ShaderNodeTexImage")
        node.image = probe
        for other in tree.nodes:
            other.select = False
        node.select = True
        tree.nodes.active = node
        restore = _rewire_materials_to_alpha(bake_source)
        bake_selected_to_active(bake_source, target, "EMIT", extrusion, ray_distance, margin_pixels)
        values = read_image_pixels(probe)
        colours = read_image_pixels(image)
        changed = 0
        for index, is_painted in enumerate(painted):
            alpha = float(values[index * 4]) if is_painted else 1.0
            alpha = min(1.0, max(0.0, alpha))
            if colours[index * 4 + 3] != alpha:
                changed += 1
            colours[index * 4 + 3] = alpha
        image.pixels.foreach_set(colours)
        image.update()
        return {"applied": True, "texels_updated": changed}
    finally:
        _restore_materials(restore)
        if node is not None:
            tree = target.data.materials[0].node_tree
            texture = next((other for other in tree.nodes if other.type == "TEX_IMAGE" and other.image is image), None)
            tree.nodes.remove(node)
            if texture is not None:
                for other in tree.nodes:
                    other.select = False
                texture.select = True
                tree.nodes.active = texture
        bpy.data.images.remove(probe)


def flatten_unpainted(image: bpy.types.Image, painted: list[int]) -> dict[str, Any]:
    """Replace the sentinel fill with the average baked colour.

    Unpainted texels only exist in the gaps between atlas islands, but the
    magenta sentinel must never reach the exported TGA: it would show along
    island seams and its zero alpha would make the material look translucent.
    """
    values = read_image_pixels(image)
    total = len(painted)
    red = green = blue = 0.0
    samples = 0
    for index, is_painted in enumerate(painted):
        if not is_painted:
            continue
        base = index * 4
        red += float(values[base])
        green += float(values[base + 1])
        blue += float(values[base + 2])
        samples += 1
    if samples == 0:
        return {"filled_texels": 0, "fill_colour": [0.0, 0.0, 0.0]}
    fill = (red / samples, green / samples, blue / samples)
    filled = 0
    for index in range(total):
        if painted[index]:
            continue
        base = index * 4
        values[base], values[base + 1], values[base + 2] = fill
        values[base + 3] = 1.0
        filled += 1
    image.pixels.foreach_set(values)
    image.update()
    return {"filled_texels": filled, "fill_colour": [round(channel, 6) for channel in fill]}


def render_material_proof(path: Path, obj: bpy.types.Object, height: float, back: bool = False) -> bool:
    """Render the baked material inside Blender before StudioMDL is launched."""
    scene = bpy.context.scene
    saved_engine = scene.render.engine
    saved_visibility = [(other, other.hide_render) for other in list(scene.objects) if hasattr(other, "hide_render")]
    temporary: list[bpy.types.Object] = []
    try:
        for other, _hidden in saved_visibility:
            if other.type == "MESH":
                other.hide_render = other != obj
        obj.hide_render = False
        engine_set = False
        for candidate in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE", "BLENDER_WORKBENCH"):
            try:
                scene.render.engine = candidate
                engine_set = True
                break
            except (TypeError, ValueError):
                continue
        if not engine_set:
            log("No realtime render engine was available for the baked material proof.")
            return False
        scene.render.resolution_x = 640
        scene.render.resolution_y = 640
        scene.render.resolution_percentage = 100
        scene.render.image_settings.file_format = "PNG"
        scene.render.image_settings.color_mode = "RGB"
        scene.render.film_transparent = False
        if scene.world is None:
            scene.world = bpy.data.worlds.new("EmberProofWorld")
        scene.world.color = (0.05, 0.05, 0.06)
        # normalise_character leaves the character facing negative Y, so the
        # front view camera has to sit on the negative Y side of the model.
        direction = 1.0 if back else -1.0
        bpy.ops.object.camera_add(location=(0.0, direction * height * 1.9, height * 0.55))
        camera = bpy.context.object
        temporary.append(camera)
        camera.data.lens = 62
        point_camera(camera, Vector((0.0, 0.0, height * 0.53)))
        scene.camera = camera
        for offset, energy in (((0.9, direction * 1.1, 1.3), 1400), ((-0.9, direction * 0.9, 0.9), 900), ((0.0, -direction * 1.2, 1.1), 600)):
            bpy.ops.object.light_add(type="AREA", location=(offset[0] * height, offset[1] * height, offset[2] * height))
            light = bpy.context.object
            temporary.append(light)
            light.data.energy = energy
            light.data.shape = "DISK"
            light.data.size = height * 0.6
            point_camera(light, Vector((0.0, 0.0, height * 0.53)))
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
        return path.is_file() and path.stat().st_size > 1024
    except Exception as exc:
        log(f"Baked material proof render failed: {exc}")
        return False
    finally:
        for other, hidden in saved_visibility:
            if other.name in bpy.data.objects:
                other.hide_render = hidden
        for temporary_object in temporary:
            if temporary_object.name in bpy.data.objects:
                bpy.data.objects.remove(temporary_object, do_unlink=True)
        try:
            scene.render.engine = saved_engine
        except (TypeError, ValueError):
            pass


def rebuild_atlas_and_bake(
    obj: bpy.types.Object,
    bake_source: bpy.types.Object,
    texture_size: int,
    height: float,
    generated: Path,
    slug: str,
) -> dict[str, Any]:
    """Generate a new atlas on the reduced mesh and bake the original colour in.

    This is the whole point of version 2.2.  Nothing downstream is allowed to
    reuse the imported UV coordinates.
    """
    texture_size = max(64, int(texture_size))
    atlas = rebuild_uv_atlas(obj, texture_size)
    coordinates = uv_coordinates(obj)
    triangles = uv_triangles(obj)
    uv_bounds = uv_bounds_report(coordinates)
    counts = rasterize_triangles(triangles, BAKE_RASTER_RESOLUTION)
    islands = island_report(counts, BAKE_RASTER_RESOLUTION, total_uv_area(triangles))
    log(
        f"Rebuilt atlas: {uv_bounds['count']} UV coordinates, {islands['atlas_usage_ratio'] * 100:.1f}% atlas usage, "
        f"{islands['overlap_ratio'] * 100:.2f}% overlapping texels."
    )

    image = create_bake_image(f"{slug}_baked_colour", texture_size)
    assign_baked_material(obj, image, "body")
    margin_pixels = max(4, int(texture_size / 128))
    attempts: list[dict[str, Any]] = []
    painted: list[int] = []
    coverage: dict[str, Any] = {}
    for index, scale in enumerate((1.0, 2.5, 6.0), start=1):
        extrusion = max(0.05, height * 0.006 * scale)
        ray_distance = max(0.20, height * 0.02 * scale)
        fill_image(image, BAKE_SENTINEL)
        bake_selected_to_active(bake_source, obj, "DIFFUSE", extrusion, ray_distance, margin_pixels)
        pixels = read_image_pixels(image)
        painted = painted_mask_from_pixels(pixels, BAKE_SENTINEL)
        coverage = triangle_coverage_report(triangles, painted, texture_size)
        attempts.append({
            "attempt": index,
            "cage_extrusion": extrusion,
            "max_ray_distance": ray_distance,
            "coverage_ratio": coverage["coverage_ratio"],
            "uncovered_triangles": coverage["uncovered_triangles"],
        })
        log(
            f"Bake attempt {index}: extrusion {extrusion:.3f}, ray distance {ray_distance:.3f}, "
            f"triangle coverage {coverage['coverage_ratio'] * 100:.3f}%."
        )
        if coverage["every_triangle_has_bake_coverage"]:
            break

    pixels = read_image_pixels(image)
    colour = colour_report(pixels, painted)
    reduced_painted = downsample_mask(painted, texture_size, BAKE_RASTER_RESOLUTION)
    unpainted = unpainted_region_report(counts, reduced_painted, BAKE_RASTER_RESOLUTION)

    alpha: dict[str, Any] = {"applied": False, "required": source_alpha_required(bake_source)}
    if alpha["required"]:
        last = attempts[-1]
        try:
            alpha.update(bake_alpha_channel(
                bake_source,
                obj,
                image,
                painted,
                float(last["cage_extrusion"]),
                float(last["max_ray_distance"]),
                margin_pixels,
            ))
            log(f"Transferred source transparency into the baked atlas ({alpha.get('texels_updated', 0)} texels).")
        except Exception as exc:  # the colour bake is still valid without it
            alpha["applied"] = False
            alpha["error"] = str(exc)
            log(f"Alpha transfer failed, the baked atlas stays opaque: {exc}")
    flattened = flatten_unpainted(image, painted)

    if bake_source.name in bpy.data.objects:
        bpy.data.objects.remove(bake_source, do_unlink=True)

    proofs: dict[str, Any] = {}
    rendered = True
    for view, is_back in (("front", False), ("back", True)):
        proof_path = generated / f"{slug}_texture_proof_{view}.png"
        ok = render_material_proof(proof_path, obj, height, back=is_back)
        proofs[view] = str(proof_path.name) if ok else None
        rendered = rendered and ok

    verdict = evaluate_bake(uv_bounds, islands, coverage, colour, unpainted, rendered)
    verdict.update({
        "atlas": atlas,
        "texture_size": texture_size,
        "bake_margin_pixels": margin_pixels,
        "bake_attempts": attempts,
        "alpha": alpha,
        "unpainted_fill": flattened,
        "material_proofs": proofs,
        "image": image.name,
        "method": "cycles_selected_to_active_diffuse_colour",
        "source_uv_reused": False,
    })
    return verdict


def safe_material_name(name: str, index: int) -> str:
    value = slugify(name)
    return value[:48] or f"material_{index + 1}"


def linked_image_from_socket(socket) -> bpy.types.Image | None:
    if not socket or not socket.is_linked:
        return None
    node = socket.links[0].from_node
    if node.type == "TEX_IMAGE" and node.image:
        return node.image
    if node.type == "NORMAL_MAP":
        return linked_image_from_socket(node.inputs.get("Color"))
    for input_socket in getattr(node, "inputs", []):
        image = linked_image_from_socket(input_socket)
        if image:
            return image
    return None


def prepare_export_image(image: bpy.types.Image, texture_size: int, name: str) -> bpy.types.Image:
    export = image.copy()
    export.name = name
    width, height = int(export.size[0]), int(export.size[1])
    if width <= 0 or height <= 0:
        raise RuntimeError(f"Texture {image.name} has no pixel dimensions.")
    scale = min(1.0, texture_size / max(width, height))
    target_w = max(4, 1 << max(2, int(math.floor(math.log2(max(4.0, width * scale))))))
    target_h = max(4, 1 << max(2, int(math.floor(math.log2(max(4.0, height * scale))))))
    if (target_w, target_h) != (width, height):
        export.scale(target_w, target_h)
    return export


def save_tga(image: bpy.types.Image, tga_path: Path, texture_size: int, normal_map: bool = False) -> bool:
    export = prepare_export_image(image, texture_size, f"EMBER_EXPORT_{tga_path.stem}")
    try:
        count = int(export.size[0]) * int(export.size[1]) * 4
        from array import array
        values = array("f", [0.0]) * count
        export.pixels.foreach_get(values)
        if normal_map:
            for index in range(1, count, 4):
                values[index] = 1.0 - values[index]
        has_alpha = (not normal_map) and any(values[index] < 0.999 for index in range(3, count, 4))
        write_uncompressed_tga(
            tga_path,
            int(export.size[0]),
            int(export.size[1]),
            values,
            srgb=not normal_map,
            include_alpha=has_alpha,
        )
        return has_alpha
    finally:
        bpy.data.images.remove(export)


def extract_materials(obj: bpy.types.Object, materials_dir: Path, texture_size: int, slug: str) -> list[dict[str, Any]]:
    """Export the safest Source 1 material contract.

    The previous release translated glTF normal, metallic and roughness inputs into
    Source phong settings.  That produced overbright, blown out materials on the
    user's actual model.  A player model only needs a validated base texture to be
    correct, so v2.1 deliberately emits one conservative VertexLitGeneric material.
    Normal maps and phong are left out until they can be previewed in Source itself.

    In v2.2 the mesh reaches this function carrying a single material whose base
    colour is the validated bake target, so the exported TGA is the atlas that was
    proven against the reduced topology rather than the imported glTF texture.
    """
    materials_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    if not obj.data.materials:
        mat = bpy.data.materials.new("body")
        obj.data.materials.append(mat)
    used_names = set()
    for i, mat in enumerate(obj.data.materials):
        if mat is None:
            continue
        base = safe_material_name(mat.name, i)
        while base in used_names:
            base = f"{base}_{i + 1}"
        used_names.add(base)
        mat.name = base
        principled = None
        if mat.use_nodes and mat.node_tree:
            principled = next((node for node in mat.node_tree.nodes if node.type == "BSDF_PRINCIPLED"), None)
        base_image = linked_image_from_socket(principled.inputs.get("Base Color")) if principled else None
        if base_image is None and mat.use_nodes and mat.node_tree:
            base_image = next((node.image for node in mat.node_tree.nodes if node.type == "TEX_IMAGE" and node.image), None)
        tga_path = materials_dir / f"{base}.tga"
        if base_image and base_image.size[0] and base_image.size[1]:
            has_alpha = save_tga(base_image, tga_path, texture_size)
        else:
            colour = tuple(mat.diffuse_color) if hasattr(mat, "diffuse_color") else (0.7, 0.7, 0.7, 1.0)
            generated = bpy.data.images.new(f"{base}_generated", width=4, height=4, alpha=True)
            try:
                generated.pixels = list(colour) * 16
                has_alpha = save_tga(generated, tga_path, texture_size)
            finally:
                bpy.data.images.remove(generated)

        vmt_lines = [
            '"VertexLitGeneric"',
            '{',
            f'    "$basetexture" "models/player/{slug}/{base}"',
            '    "$model" "1"',
            '    "$halflambert" "1"',
        ]
        if has_alpha:
            vmt_lines.append('    "$translucent" "1"')
        vmt_lines.append('}')
        vmt = materials_dir / f"{base}.vmt"
        vmt.write_text("\n".join(vmt_lines) + "\n", encoding="utf-8")
        compile_options = ["nomip 1", "nocompress 1"]
        if not has_alpha:
            compile_options.append("stripalphachannel 1")
        (materials_dir / f"{base}.txt").write_text("\n".join(compile_options) + "\n", encoding="utf-8")
        rows.append({
            "name": base,
            "tga": tga_path.name,
            "vmt": vmt.name,
            "normal_tga": None,
            "has_alpha": has_alpha,
            "material_profile": "safe_vertexlit_base_only",
            "normal_map_disabled": True,
            "phong_disabled": True,
            "baked_atlas": base_image is not None and base_image.name.endswith("_baked_colour"),
        })
    return rows


def bone_local_transforms(bones: list[dict[str, Any]]) -> list[tuple[Vector, Euler]]:
    """Return exact SMD local transforms.

    Target stock bones carry their authored SMD local position and Euler values.
    Guided source bones are only used before conformance and fall back to a world
    matrix calculation. `$definebone` values are never used here.
    """
    world_mats: dict[str, Matrix] = {}
    locals_out: list[tuple[Vector, Euler]] = []
    for bone in bones:
        name = bone["name"]
        if "local_pos" in bone and "local_rot" in bone:
            position = Vector(tuple(float(v) for v in bone["local_pos"]))
            rotation = Euler(tuple(float(v) for v in bone["local_rot"]), "XYZ")
            local = Matrix.Translation(position) @ rotation.to_matrix().to_4x4()
            parent_name = bone.get("parent")
            world = world_mats[parent_name] @ local if parent_name in world_mats else local
        else:
            head = Vector(bone["head"])
            rotation = Euler(tuple(bone["world_rot"]), "XYZ")
            world = Matrix.Translation(head) @ rotation.to_matrix().to_4x4()
            parent_name = bone.get("parent")
            local = world_mats[parent_name].inverted() @ world if parent_name in world_mats else world
            position = local.translation
            rotation = local.to_euler("XYZ")
        world_mats[name] = world
        locals_out.append((position, rotation))
    return locals_out


def _vertex_influences(obj: bpy.types.Object, vertex, bone_ids: dict[str, int]) -> list[tuple[int, float]]:
    influences: list[tuple[int, float]] = []
    for item in vertex.groups:
        if item.group >= len(obj.vertex_groups):
            continue
        name = obj.vertex_groups[item.group].name
        if name in bone_ids and item.weight > 0.0001:
            influences.append((bone_ids[name], float(item.weight)))
    influences.sort(key=lambda value: value[1], reverse=True)
    influences = influences[:3] or [(0, 1.0)]
    total = sum(weight for _bone, weight in influences) or 1.0
    return [(bone_id, weight / total) for bone_id, weight in influences]


def deformation_probe(obj: bpy.types.Object, bones: list[dict[str, Any]], height: float) -> dict[str, Any]:
    """Run a deterministic skeletal pose before allowing StudioMDL compilation.

    This catches non finite vertices, cross body weighting and bind transform errors
    before the model reaches Garry's Mod. It uses the exact matrices exported to SMD,
    not Blender's display armature.
    """
    bone_ids = {bone["name"]: index for index, bone in enumerate(bones)}
    local_transforms = bone_local_transforms(bones)
    bind_world: list[Matrix] = []
    posed_world: list[Matrix] = []
    deltas = {
        "ValveBiped.Bip01_L_UpperArm": Euler((0.0, 0.0, math.radians(22.0)), "XYZ").to_matrix().to_4x4(),
        "ValveBiped.Bip01_R_UpperArm": Euler((0.0, 0.0, math.radians(-22.0)), "XYZ").to_matrix().to_4x4(),
        "ValveBiped.Bip01_L_Forearm": Euler((0.0, 0.0, math.radians(-42.0)), "XYZ").to_matrix().to_4x4(),
        "ValveBiped.Bip01_R_Forearm": Euler((0.0, 0.0, math.radians(42.0)), "XYZ").to_matrix().to_4x4(),
        "ValveBiped.Bip01_L_Thigh": Euler((0.0, math.radians(14.0), 0.0), "XYZ").to_matrix().to_4x4(),
        "ValveBiped.Bip01_R_Thigh": Euler((0.0, math.radians(-14.0), 0.0), "XYZ").to_matrix().to_4x4(),
        "ValveBiped.Bip01_L_Calf": Euler((0.0, 0.0, math.radians(28.0)), "XYZ").to_matrix().to_4x4(),
        "ValveBiped.Bip01_R_Calf": Euler((0.0, 0.0, math.radians(28.0)), "XYZ").to_matrix().to_4x4(),
    }
    for index, (bone, (position, rotation)) in enumerate(zip(bones, local_transforms)):
        local = Matrix.Translation(position) @ rotation.to_matrix().to_4x4()
        parent_id = bone_ids.get(bone.get("parent"), -1)
        bind = bind_world[parent_id] @ local if parent_id >= 0 else local
        delta = deltas.get(bone["name"], Matrix.Identity(4))
        posed_local = local @ delta
        posed = posed_world[parent_id] @ posed_local if parent_id >= 0 else posed_local
        bind_world.append(bind)
        posed_world.append(posed)

    skin = [posed_world[index] @ bind_world[index].inverted() for index in range(len(bones))]
    rest_points: list[Vector] = []
    posed_points: list[Vector] = []
    maximum_displacement = 0.0
    non_finite = 0
    for vertex in obj.data.vertices:
        rest = obj.matrix_world @ vertex.co
        posed = Vector((0.0, 0.0, 0.0))
        for bone_id, weight in _vertex_influences(obj, vertex, bone_ids):
            posed += (skin[bone_id] @ rest) * weight
        if not all(math.isfinite(value) for value in posed):
            non_finite += 1
            posed = rest.copy()
        rest_points.append(rest)
        posed_points.append(posed)
        maximum_displacement = max(maximum_displacement, (posed - rest).length)

    def dimensions(points: list[Vector]) -> Vector:
        minimum = Vector((min(point.x for point in points), min(point.y for point in points), min(point.z for point in points)))
        maximum = Vector((max(point.x for point in points), max(point.y for point in points), max(point.z for point in points)))
        return maximum - minimum

    rest_dimensions = dimensions(rest_points)
    posed_dimensions = dimensions(posed_points)
    dimension_ratios = [
        posed_dimensions[index] / max(rest_dimensions[index], height * 0.03)
        for index in range(3)
    ]
    edge_pairs: list[tuple[float, float]] = []
    edge_stride = max(1, len(obj.data.edges) // 24000)
    for edge_index in range(0, len(obj.data.edges), edge_stride):
        edge = obj.data.edges[edge_index]
        first, second = edge.vertices
        rest_length = (rest_points[first] - rest_points[second]).length
        posed_length = (posed_points[first] - posed_points[second]).length
        edge_pairs.append((rest_length, posed_length))
    edge_stats = robust_edge_deformation(edge_pairs, height)

    passed = (
        non_finite == 0
        and maximum_displacement <= height * 1.15
        and max(dimension_ratios) <= 3.5
        and bool(edge_stats["passed"])
    )
    return {
        "passed": passed,
        "vertices_tested": len(rest_points),
        "edges_tested": int(edge_stats["samples"]),
        "non_finite_vertices": non_finite,
        "maximum_displacement": maximum_displacement,
        "dimension_ratios": dimension_ratios,
        "minimum_edge_ratio": float(edge_stats["raw_minimum_ratio"]),
        "maximum_edge_ratio": float(edge_stats["raw_maximum_ratio"]),
        "edge_diagnostics": edge_stats,
    }


def validate_reference_smd(path: Path, bones: list[dict[str, Any]]) -> dict[str, Any]:
    """Parse the generated SMD and enforce the complete bind and mesh contract."""
    lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
    bone_ids = {bone["name"]: index for index, bone in enumerate(bones)}
    expected_parents = [bone_ids.get(bone.get("parent"), -1) for bone in bones]

    nodes: dict[int, tuple[str, int]] = {}
    section = None
    for line in lines:
        stripped = line.strip()
        if stripped in {"nodes", "skeleton", "triangles"}:
            section = stripped
            continue
        if stripped == "end":
            section = None
            continue
        if section == "nodes":
            match = re.fullmatch(r'(\d+)\s+"([^"]+)"\s+(-?\d+)', stripped)
            if match:
                nodes[int(match.group(1))] = (match.group(2), int(match.group(3)))

    node_errors = 0
    for index, bone in enumerate(bones):
        actual = nodes.get(index)
        if actual != (bone["name"], expected_parents[index]):
            node_errors += 1
    node_errors += max(0, len(nodes) - len(bones))

    skeleton_values: dict[int, list[float]] = {}
    in_skeleton = False
    at_time_zero = False
    for line in lines:
        stripped = line.strip()
        if stripped == "skeleton":
            in_skeleton = True
            continue
        if not in_skeleton:
            continue
        if stripped == "time 0":
            at_time_zero = True
            continue
        if stripped == "end":
            break
        if at_time_zero:
            parts = stripped.split()
            if len(parts) == 7:
                try:
                    values = [float(value) for value in parts[1:]]
                    if all(math.isfinite(value) for value in values):
                        skeleton_values[int(parts[0])] = values
                except ValueError:
                    pass

    expected = bone_local_transforms(bones)
    bind_errors = 0
    maximum_bind_error = 0.0
    for index, (position, rotation) in enumerate(expected):
        actual = skeleton_values.get(index)
        expected_values = [position.x, position.y, position.z, rotation.x, rotation.y, rotation.z]
        if actual is None:
            bind_errors += 1
            continue
        error = max(abs(a - b) for a, b in zip(actual, expected_values))
        maximum_bind_error = max(maximum_bind_error, error)
        if error > 2e-5:
            bind_errors += 1

    in_triangles = False
    material_expected = True
    vertices = 0
    invalid_parent_fields = 0
    invalid_weights = 0
    invalid_bone_links = 0
    invalid_geometry = 0
    max_links = 0
    positions: list[Vector] = []
    material_names: set[str] = set()
    for line in lines:
        stripped = line.strip()
        if stripped == "triangles":
            in_triangles = True
            material_expected = True
            continue
        if not in_triangles:
            continue
        if stripped == "end":
            break
        if material_expected:
            if not stripped or any(char in stripped for char in '\r\n'):
                invalid_geometry += 1
            else:
                material_names.add(stripped)
            material_expected = False
            continue
        parts = stripped.split()
        if len(parts) < 12:
            invalid_weights += 1
        else:
            try:
                parent = int(parts[0])
                position = Vector(tuple(float(value) for value in parts[1:4]))
                normal = Vector(tuple(float(value) for value in parts[4:7]))
                uv = tuple(float(value) for value in parts[7:9])
                links = int(parts[9])
                values = parts[10:]
                link_pairs = [(int(values[index]), float(values[index + 1])) for index in range(0, links * 2, 2)]
                max_links = max(max_links, links)
                if parent != 0:
                    invalid_parent_fields += 1
                if links < 1 or links > 3 or len(values) != links * 2 or abs(sum(weight for _bone, weight in link_pairs) - 1.0) > 2e-4:
                    invalid_weights += 1
                if any(bone < 0 or bone >= len(bones) for bone, _weight in link_pairs):
                    invalid_bone_links += 1
                if not all(math.isfinite(value) for value in (*position, *normal, *uv)) or not 0.75 <= normal.length <= 1.25:
                    invalid_geometry += 1
                positions.append(position)
            except (ValueError, IndexError):
                invalid_weights += 1
        vertices += 1
        if vertices % 3 == 0:
            material_expected = True

    if positions:
        minimum = Vector((min(p.x for p in positions), min(p.y for p in positions), min(p.z for p in positions)))
        maximum = Vector((max(p.x for p in positions), max(p.y for p in positions), max(p.z for p in positions)))
        dimensions = maximum - minimum
        finite_extent = all(math.isfinite(value) and abs(value) < 1000 for value in (*minimum, *maximum))
        humanoid_extent = dimensions.x > 30 and dimensions.z > 45 and dimensions.y > 1
    else:
        minimum = maximum = dimensions = Vector((0.0, 0.0, 0.0))
        finite_extent = humanoid_extent = False

    passed = (
        node_errors == 0
        and vertices > 0 and vertices % 3 == 0
        and invalid_parent_fields == 0 and invalid_weights == 0 and invalid_bone_links == 0
        and invalid_geometry == 0 and max_links <= 3
        and bind_errors == 0 and len(skeleton_values) == len(bones)
        and finite_extent and humanoid_extent and bool(material_names)
    )
    return {
        "passed": passed,
        "nodes": len(nodes),
        "node_contract_errors": node_errors,
        "vertices": vertices,
        "triangles": vertices // 3,
        "materials": sorted(material_names),
        "invalid_parent_fields": invalid_parent_fields,
        "invalid_weight_records": invalid_weights,
        "invalid_bone_links": invalid_bone_links,
        "invalid_geometry_records": invalid_geometry,
        "max_links": max_links,
        "bind_bones": len(skeleton_values),
        "bind_contract_errors": bind_errors,
        "maximum_bind_error": maximum_bind_error,
        "bounds_min": list(minimum),
        "bounds_max": list(maximum),
        "dimensions": list(dimensions),
        "finite_extent": finite_extent,
        "humanoid_extent": humanoid_extent,
        "bind_source": bones[0].get("bind_source") if bones else None,
    }


def export_smd(path: Path, objects: list[bpy.types.Object], bones: list[dict[str, Any]]) -> dict[str, Any]:
    """Write an undeformed reference SMD with explicit normalized links.

    The v2.0.4 exporter evaluated the armature modifier while also exporting its
    weights. That is not how a reference SMD is authored and can bake deformation
    into geometry before StudioMDL applies the same skeleton again. This exporter
    reads the rest mesh directly, matching established Source exporters.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    bone_ids = {bone["name"]: i for i, bone in enumerate(bones)}
    local_transforms = bone_local_transforms(bones)
    triangle_total = 0
    vertex_total = 0
    maximum_weight_error = 0.0
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write("version 1\n")
        fh.write("nodes\n")
        for i, bone in enumerate(bones):
            parent = bone_ids.get(bone.get("parent"), -1)
            fh.write(f'{i} "{bone["name"]}" {parent}\n')
        fh.write("end\n")
        fh.write("skeleton\n")
        fh.write("time 0\n")
        for i, (pos, rot) in enumerate(local_transforms):
            fh.write(f"{i} {pos.x:.6f} {pos.y:.6f} {pos.z:.6f} {rot.x:.6f} {rot.y:.6f} {rot.z:.6f}\n")
        fh.write("end\n")
        fh.write("triangles\n")
        for obj in objects:
            mesh = obj.data
            mesh.calc_loop_triangles()
            mesh.calc_normals_split() if hasattr(mesh, "calc_normals_split") else None
            uv_layer = mesh.uv_layers.active.data if mesh.uv_layers.active else None
            normal_matrix = obj.matrix_world.to_3x3().inverted().transposed()
            for tri in mesh.loop_triangles:
                mat_index = tri.material_index
                material = obj.data.materials[mat_index] if mat_index < len(obj.data.materials) else None
                fh.write((material.name if material else "body") + "\n")
                for loop_index in tri.loops:
                    loop = mesh.loops[loop_index]
                    vert = mesh.vertices[loop.vertex_index]
                    co = obj.matrix_world @ vert.co
                    normal = (normal_matrix @ loop.normal).normalized()
                    uv = uv_layer[loop_index].uv if uv_layer else Vector((0.0, 0.0))
                    influences = _vertex_influences(obj, vert, bone_ids)
                    maximum_weight_error = max(maximum_weight_error, abs(sum(weight for _bone, weight in influences) - 1.0))
                    links = " ".join(f"{bone_id} {weight:.6f}" for bone_id, weight in influences)
                    # With explicit links, keep the legacy parent field at root. The
                    # links are the complete weight map and sum to exactly one.
                    fh.write(
                        f"0 {co.x:.6f} {co.y:.6f} {co.z:.6f} "
                        f"{normal.x:.6f} {normal.y:.6f} {normal.z:.6f} "
                        f"{uv.x:.6f} {1.0 - uv.y:.6f} {len(influences)} {links}\n"
                    )
                    vertex_total += 1
                triangle_total += 1
        fh.write("end\n")
    return {
        "triangles": triangle_total,
        "smd_vertices": vertex_total,
        "rest_mesh_export": True,
        "explicit_link_parent_is_root": True,
        "maximum_weight_sum_error": maximum_weight_error,
    }



def point_camera(camera: bpy.types.Object, target: Vector) -> None:
    direction = target - camera.location
    camera.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def render_workshop_icon(path: Path, obj: bpy.types.Object, bounds: dict[str, Any]) -> bool:
    scene = bpy.context.scene
    saved_visibility = [(other, other.hide_render) for other in list(scene.objects) if hasattr(other, "hide_render")]
    temporary: list[bpy.types.Object] = []
    try:
        for other, _hidden in saved_visibility:
            if other.type == "MESH":
                other.hide_render = other != obj
        obj.hide_render = False
        height = float(bounds["height"])
        engine_set = False
        for candidate in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE", "BLENDER_WORKBENCH"):
            try:
                scene.render.engine = candidate
                engine_set = True
                break
            except (TypeError, ValueError):
                continue
        if not engine_set:
            log("No supported realtime render engine was available for the Workshop icon.")
            return False
        scene.render.resolution_x = 512
        scene.render.resolution_y = 512
        scene.render.resolution_percentage = 100
        scene.render.image_settings.file_format = "JPEG"
        scene.render.image_settings.color_mode = "RGB"
        scene.render.image_settings.quality = 92
        scene.render.film_transparent = False
        scene.world.color = (0.008, 0.006, 0.004)
        bpy.ops.object.camera_add(location=(height * 1.65, -height * 0.08, height * 0.56))
        camera = bpy.context.object
        temporary.append(camera)
        camera.data.lens = 58
        point_camera(camera, Vector((0, 0, height * 0.54)))
        scene.camera = camera
        for location, energy, size, target in [
            ((height * 0.72, height * 0.72, height * 0.92), 1150, height * 0.7, height * 0.55),
            ((height * 0.45, -height * 0.78, height * 0.58), 620, height * 0.5, height * 0.50),
            ((-height * 0.5, 0, height * 0.8), 900, height * 0.45, height * 0.62),
        ]:
            bpy.ops.object.light_add(type="AREA", location=location)
            light = bpy.context.object
            temporary.append(light)
            light.data.energy = energy
            light.data.shape = "DISK"
            light.data.size = size
            point_camera(light, Vector((0, 0, target)))
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
        return path.exists()
    except Exception as exc:
        log(f"Workshop icon render failed: {exc}")
        return False
    finally:
        for other, hidden in saved_visibility:
            if other.name in bpy.data.objects:
                other.hide_render = hidden
        for temporary_object in temporary:
            if temporary_object.name in bpy.data.objects:
                bpy.data.objects.remove(temporary_object, do_unlink=True)


def _write_hitbox_qci(modelsrc: Path) -> Path:
    path = modelsrc / "hitbox.qci"
    lines = [
        '$hboxset "default"',
        '$hbox 1 "ValveBiped.Bip01_Head1" -3.8 -4.2 -4.0 6.8 4.2 4.0',
        '$hbox 2 "ValveBiped.Bip01_Spine4" -3.5 -6.5 -7.0 7.5 6.5 7.0',
        '$hbox 3 "ValveBiped.Bip01_Spine2" -4.0 -6.5 -7.0 8.0 6.5 7.0',
        '$hbox 3 "ValveBiped.Bip01_Pelvis" -5.0 -7.0 -6.0 6.0 7.0 6.0',
        '$hbox 4 "ValveBiped.Bip01_L_UpperArm" -2.0 -2.5 -2.5 11.5 2.5 2.5',
        '$hbox 4 "ValveBiped.Bip01_L_Forearm" -2.0 -2.1 -2.1 11.0 2.1 2.1',
        '$hbox 5 "ValveBiped.Bip01_R_UpperArm" -2.0 -2.5 -2.5 11.5 2.5 2.5',
        '$hbox 5 "ValveBiped.Bip01_R_Forearm" -2.0 -2.1 -2.1 11.0 2.1 2.1',
        '$hbox 6 "ValveBiped.Bip01_L_Thigh" -3.2 -3.5 -3.5 17.5 3.5 3.5',
        '$hbox 6 "ValveBiped.Bip01_L_Calf" -2.8 -3.0 -3.0 16.0 3.0 3.0',
        '$hbox 7 "ValveBiped.Bip01_R_Thigh" -3.2 -3.5 -3.5 17.5 3.5 3.5',
        '$hbox 7 "ValveBiped.Bip01_R_Calf" -2.8 -3.0 -3.0 16.0 3.0 3.0',
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _write_ik_qci(modelsrc: Path) -> Path:
    path = modelsrc / "standardikchains.qci"
    lines = [
        '$ikchain rhand ValveBiped.Bip01_R_Hand knee 0 -1 0',
        '$ikchain lhand ValveBiped.Bip01_L_Hand knee 0 1 0',
        '$ikchain rfoot ValveBiped.Bip01_R_Foot knee 0 -1 0',
        '$ikchain lfoot ValveBiped.Bip01_L_Foot knee 0 1 0',
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _write_ragdoll_qci(modelsrc: Path, slug: str) -> Path:
    """Write conservative human constraints derived from Garry's Mod's public male ragdoll source."""
    path = modelsrc / "ragdoll.qci"
    lines = [
        f'$collisionjoints "{slug}_physics.smd"',
        '{',
        '    $mass 90.0',
        '    $inertia 10.0',
        '    $damping 0.01',
        '    $rotdamping 1.50',
        '    $rootbone "ValveBiped.Bip01_Pelvis"',
        '    $animatedfriction 1 400 0.5 0.0 0.3',
        '    $jointconstrain "ValveBiped.Bip01_Spine1" x limit -35 35 0',
        '    $jointconstrain "ValveBiped.Bip01_Spine1" y limit -22 22 0',
        '    $jointconstrain "ValveBiped.Bip01_Spine1" z limit -22 38 0',
        '    $jointconstrain "ValveBiped.Bip01_Head1" x limit -20 20 0',
        '    $jointconstrain "ValveBiped.Bip01_Head1" y limit -25 25 0',
        '    $jointconstrain "ValveBiped.Bip01_Head1" z limit -13 30 0',
        '    $jointconstrain "ValveBiped.Bip01_R_UpperArm" x limit -39 39 0',
        '    $jointconstrain "ValveBiped.Bip01_R_UpperArm" y limit -79 95 0',
        '    $jointconstrain "ValveBiped.Bip01_R_UpperArm" z limit -93 23 0',
        '    $jointconstrain "ValveBiped.Bip01_L_UpperArm" x limit -30 30 0',
        '    $jointconstrain "ValveBiped.Bip01_L_UpperArm" y limit -95 84 0',
        '    $jointconstrain "ValveBiped.Bip01_L_UpperArm" z limit -86 26 0',
        '    $jointconstrain "ValveBiped.Bip01_L_Forearm" x limit 0 0 0',
        '    $jointconstrain "ValveBiped.Bip01_L_Forearm" y limit 0 0 0',
        '    $jointconstrain "ValveBiped.Bip01_L_Forearm" z limit -149 4 0',
        '    $jointconstrain "ValveBiped.Bip01_R_Forearm" x limit 0 0 0',
        '    $jointconstrain "ValveBiped.Bip01_R_Forearm" y limit 0 0 0',
        '    $jointconstrain "ValveBiped.Bip01_R_Forearm" z limit -149 4 0',
        '    $jointconstrain "ValveBiped.Bip01_R_Thigh" x limit -12 12 0',
        '    $jointconstrain "ValveBiped.Bip01_R_Thigh" y limit -8 75 0',
        '    $jointconstrain "ValveBiped.Bip01_R_Thigh" z limit -97 32 0',
        '    $jointconstrain "ValveBiped.Bip01_L_Thigh" x limit -12 12 0',
        '    $jointconstrain "ValveBiped.Bip01_L_Thigh" y limit -73 6 0',
        '    $jointconstrain "ValveBiped.Bip01_L_Thigh" z limit -93 30 0',
        '    $jointconstrain "ValveBiped.Bip01_R_Calf" x limit 0 0 0',
        '    $jointconstrain "ValveBiped.Bip01_R_Calf" y limit 0 0 0',
        '    $jointconstrain "ValveBiped.Bip01_R_Calf" z limit -12 126 0',
        '    $jointconstrain "ValveBiped.Bip01_L_Calf" x limit 0 0 0',
        '    $jointconstrain "ValveBiped.Bip01_L_Calf" y limit 0 0 0',
        '    $jointconstrain "ValveBiped.Bip01_L_Calf" z limit -8 126 0',
        '    $jointconstrain "ValveBiped.Bip01_L_Foot" x limit 0 0 0',
        '    $jointconstrain "ValveBiped.Bip01_L_Foot" y limit -19 19 0',
        '    $jointconstrain "ValveBiped.Bip01_L_Foot" z limit -15 35 0',
        '    $jointconstrain "ValveBiped.Bip01_R_Foot" x limit 0 0 0',
        '    $jointconstrain "ValveBiped.Bip01_R_Foot" y limit -25 6 0',
        '    $jointconstrain "ValveBiped.Bip01_R_Foot" z limit -15 35 0',
        '    $jointcollide "ValveBiped.Bip01_L_Forearm" "ValveBiped.Bip01_Pelvis"',
        '    $jointcollide "ValveBiped.Bip01_R_Forearm" "ValveBiped.Bip01_Pelvis"',
        '    $jointcollide "ValveBiped.Bip01_L_Forearm" "ValveBiped.Bip01_Spine1"',
        '    $jointcollide "ValveBiped.Bip01_R_Forearm" "ValveBiped.Bip01_Spine1"',
        '    $jointcollide "ValveBiped.Bip01_R_Thigh" "ValveBiped.Bip01_L_Thigh"',
        '    $jointcollide "ValveBiped.Bip01_R_Calf" "ValveBiped.Bip01_L_Calf"',
        '    $jointcollide "ValveBiped.Bip01_L_Foot" "ValveBiped.Bip01_R_Foot"',
        '}',
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def generate_qc(modelsrc: Path, slug: str, animation_base: str, lods: list[str], materials: list[dict[str, Any]], bones: list[dict[str, Any]]) -> Path:
    animation = {"male": "m_anm.mdl", "female": "f_anm.mdl"}[animation_base]
    _write_hitbox_qci(modelsrc)
    _write_ik_qci(modelsrc)
    _write_ragdoll_qci(modelsrc, slug)
    qc = modelsrc / f"{slug}.qc"
    lines = [
        f'$modelname "player/{slug}/{slug}.mdl"',
        f'$body "body" "{slug}_reference.smd"',
        '$surfaceprop "flesh"',
        f'$cdmaterials "models/player/{slug}"',
        '$contents "solid"',
        '$mostlyopaque',
        '$illumposition 0 0 42',
        '$eyeposition 0 0 64',
        '$attachment "eyes" "ValveBiped.Bip01_Head1" 3.5 -4 0 rotate 0 -90 -90',
        '$attachment "anim_attachment_RH" "ValveBiped.Anim_Attachment_RH" 0 0 0 rotate -90 0 -90',
        '$attachment "anim_attachment_LH" "ValveBiped.Anim_Attachment_LH" 0 0 0 rotate -90 0 -90',
        '$include "ragdoll.qci"',
        '$include "hitbox.qci"',
        '$include "standardikchains.qci"',
        '$lockbonelengths',
        f'$includemodel "{animation}"',
        f'$sequence "reference" "{slug}_reference.smd" fps 1',
    ]
    distances = [15, 35, 60]
    for index, lod in enumerate(lods):
        lines.extend([
            f'$lod {distances[min(index, len(distances) - 1)]}',
            '{',
            f'    replacemodel "{slug}_reference.smd" "{lod}.smd"',
            '}',
        ])
    if lods:
        lines.extend(['$shadowlod', '{', f'    replacemodel "{slug}_reference.smd" "{lods[-1]}.smd"', '}'])
    qc.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return qc


def generate_addon(addon: Path, slug: str, display_name: str, material_src: Path, generate_hands: bool) -> None:
    material_target = addon / "materials" / "models" / "player" / slug
    material_target.mkdir(parents=True, exist_ok=True)
    for source in material_src.glob("*.vmt"):
        shutil.copy2(source, material_target / source.name)


def generate_compile_scripts(generated: Path, qc: Path, slug: str, toolchain: dict, material_src: Path) -> None:
    """Write a StudioMDL diagnostic script only.

    Textures are written by Ember's validated internal VTF writer in the service.
    Keeping VTEX in this script would reintroduce the hanging path removed in 2.0.4.
    """
    studiomdl = str(toolchain.get("studiomdl", "")).replace("%", "%%")
    game_dir = str(toolchain.get("game_dir", "")).replace("%", "%%")
    lines = [
        "@echo off",
        "setlocal",
        "title Ember Guided GMod StudioMDL Diagnostic",
        f'set "STUDIOMDL={studiomdl}"',
        f'set "GMOD_GAME={game_dir}"',
        'if not exist "%STUDIOMDL%" (echo StudioMDL was not found. Configure it in Toolchain. & pause & exit /b 1)',
        "if not exist \"%GMOD_GAME%\\gameinfo.txt\" (echo Garry's Mod game directory is invalid. ^& pause ^& exit /b 1)",
        'echo Textures are generated internally by Ember. This script only reruns StudioMDL.',
        f'"%STUDIOMDL%" -game "%GMOD_GAME%" "{str(qc)}"',
        'set "RESULT=%ERRORLEVEL%"',
        'if "%RESULT%"=="0" (echo StudioMDL completed.) else (echo StudioMDL failed with exit code %RESULT%.)',
        'pause',
        'exit /b %RESULT%',
    ]
    generated.joinpath("build_windows.bat").write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")


def main(config_path: Path) -> None:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    root = Path(config["project_root"])
    options = config["options"]
    guide = config.get("guide") or {}
    if not guide.get("locked"):
        raise RuntimeError("The guided rig is not locked. Return to the workbench and confirm all landmarks.")
    slug = slugify(options["slug"])
    display_name = options["display_name"]
    generated = root / "generated"
    modelsrc = generated / "modelsrc"
    material_root = generated / "materialsrc"
    material_src = material_root / "models" / "player" / slug
    addon = root / "addon"
    reports = root / "reports"
    for directory in (generated, modelsrc, material_src, addon, reports):
        directory.mkdir(parents=True, exist_ok=True)
    # Remove the unsafe v2.0.x `$definebone` override before regenerating source.
    (modelsrc / "commonbones.qci").unlink(missing_ok=True)

    clean_scene()
    meshes = import_glb(Path(config["source_glb"]))
    obj = join_meshes(meshes)
    cleanup = cleanup_mesh(obj)
    bounds = normalise_character(obj, float(options.get("target_height", 72.0)), options.get("front_axis", "neg_y"))
    quality = options.get("quality", "good")
    base_targets = {"fast": 18000, "good": 32000, "workshop": 48000}
    # Keep the untouched high resolution import before anything reduces the mesh.
    bake_source = duplicate_bake_source(obj)
    base_reduction = reduce_base_mesh(obj, base_targets[quality])
    if base_reduction["applied"]:
        log(f"Reduced base mesh from {base_reduction['triangles_before']} to {base_reduction['triangles_after']} triangles.")

    # Rebuild the atlas and bake the original colour onto the reduced mesh while
    # the reduced geometry still matches the high resolution surface exactly.
    texture_size = int(options.get("texture_size", 1024))
    texture_bake = rebuild_atlas_and_bake(obj, bake_source, texture_size, float(bounds["height"]), generated, slug)
    if not texture_bake["passed"]:
        raise RuntimeError(
            "The rebuilt UV atlas and texture bake failed validation: " + json.dumps(texture_bake["failures"])
        )
    log("Texture bake validated: new atlas, full triangle coverage and real colour variation.")

    animation_base = options.get("animation_base", "male")
    source_guide = guide_in_source_axes(guide)
    guide_anatomy = validate_source_guide(source_guide, float(bounds["height"]))
    if not guide_anatomy["passed"]:
        raise RuntimeError("The locked guide is mirrored, crossed or anatomically invalid: " + json.dumps(guide_anatomy))
    source_bones = guided_source_bones(source_guide, bounds, animation_base)
    bones = standard_bones(animation_base, float(bounds["height"]))
    armature = create_armature(bones)
    weights = assign_weights(obj, armature, source_bones, float(bounds["height"]))
    component_rigid = rigidify_small_components(obj, source_bones, float(bounds["height"]))
    rigid = apply_rigid_zones(obj, source_guide.get("rigid_zones", []))
    final_weight_check = _prune_weights(obj, 3)
    weights.update(final_weight_check)
    weights["small_components"] = component_rigid
    conformance = conform_mesh_to_standard_skeleton(obj, source_bones, bones, float(bounds["height"]))
    if not conformance["passed"]:
        raise RuntimeError("The stock skeleton conformance stage rejected this mesh before compilation: " + json.dumps(conformance))
    pose_probe = deformation_probe(obj, bones, float(bounds["height"]))
    if not pose_probe["passed"]:
        raise RuntimeError("The deterministic deformation probe rejected this rig before compilation: " + json.dumps(pose_probe))

    ratios = {"fast": [0.45], "good": [0.65, 0.35], "workshop": [0.72, 0.45, 0.20]}[quality]
    lod_objects = [duplicate_lod(obj, f"character_lod{index}", ratio) for index, ratio in enumerate(ratios, 1)]
    lod_atlas: dict[str, Any] = {}
    for lod_obj in lod_objects:
        lod_uvs = uv_triangles(lod_obj)
        lod_atlas[lod_obj.name] = island_report(
            rasterize_triangles(lod_uvs, BAKE_RASTER_RESOLUTION),
            BAKE_RASTER_RESOLUTION,
            total_uv_area(lod_uvs),
        )
    for name, summary in lod_atlas.items():
        log(f"{name} atlas overlap after reduction: {summary['overlap_ratio'] * 100:.2f}%.")
    physics = create_physics_mesh(armature, bones, float(bounds["height"]))
    materials = extract_materials(obj, material_src, texture_size, slug)

    smd_results: dict[str, Any] = {}
    reference_path = modelsrc / f"{slug}_reference.smd"
    smd_results["reference"] = export_smd(reference_path, [obj], bones)
    smd_format_check = validate_reference_smd(reference_path, bones)
    if not smd_format_check["passed"]:
        raise RuntimeError("The reference SMD failed structural validation: " + json.dumps(smd_format_check))
    lod_names: list[str] = []
    for index, lod_obj in enumerate(lod_objects, 1):
        name = f"{slug}_lod{index}"
        lod_names.append(name)
        smd_results[name] = export_smd(modelsrc / f"{name}.smd", [lod_obj], bones)
    smd_results["physics"] = export_smd(modelsrc / f"{slug}_physics.smd", [physics], bones)

    qc = generate_qc(modelsrc, slug, animation_base, lod_names, materials, bones)
    generate_addon(addon, slug, display_name, material_src, bool(options.get("generate_hands")))
    app_root = Path(config["app_root"])
    if str(app_root) not in sys.path:
        sys.path.insert(0, str(app_root))
    from ember_gmod.runtime_addon import ensure_runtime_addon_files
    ensure_runtime_addon_files(
        addon,
        slug,
        display_name,
        project_id=str(config.get("project_id", "")),
        build_token=str(config.get("runtime_token", "")),
    )
    generate_compile_scripts(generated, qc, slug, config.get("toolchain", {}), material_src)

    icon_path = generated / f"{slug}_workshop_icon.jpg"
    icon_rendered = render_workshop_icon(icon_path, obj, bounds)
    blend_path = generated / f"{slug}_source.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend_path))

    local_transforms = bone_local_transforms(bones)
    root_bones = [bone["name"] for bone in bones if not bone.get("parent")]
    report = {
        "status": "source_ready",
        "display_name": display_name,
        "slug": slug,
        "rig_source": "locked_guided_valvebiped",
        "guide_version": guide.get("version"),
        "guide_landmarks": len(guide.get("landmarks", {})),
        "cleanup": cleanup,
        "base_reduction": base_reduction,
        "texture_bake": texture_bake,
        "lod_atlas": lod_atlas,
        "bounds": bounds,
        "weights": weights,
        "rigid_zones": rigid,
        "guide_anatomy": guide_anatomy,
        "stock_skeleton_conformance": conformance,
        "deformation_probe": pose_probe,
        "smd_format_check": smd_format_check,
        "source_skeleton_contract": validate_template(animation_base),
        "bones": len(bones),
        "root_bones": root_bones,
        "materials": materials,
        "smd": smd_results,
        "qc": str(qc.relative_to(root)).replace("\\", "/"),
        "blend": str(blend_path.relative_to(root)).replace("\\", "/"),
        "checks": {
            "guide_locked": True,
            "guide_anatomy_valid": guide_anatomy["passed"],
            "source_axis_contract": source_guide.get("coordinate_space") == "source_humanoid",
            "exact_valvebiped_hierarchy": root_bones == ["ValveBiped.Bip01_Pelvis"],
            "no_generated_extra_root": all(bone["name"] != "ValveBiped.Bip01" for bone in bones),
            "exact_stock_smd_bind": smd_format_check["bind_contract_errors"] == 0 and len(local_transforms) == len(CORE_ORDER),
            "stock_skeleton_conformance_passed": conformance["passed"],
            "reference_topology_preserved_or_validated": conformance["conformance_applied"] or conformance["stock_compatible_without_warp"],
            "conformance_mode": conformance["mode"],
            "max_three_influences": weights["max_influences"] <= 3,
            "unweighted_vertices": weights["unweighted_vertices"],
            "anatomical_weighting": weights.get("method") == "anatomical_region_v2",
            "deformation_probe_passed": pose_probe["passed"],
            "reference_smd_validated": smd_format_check["passed"],
            "physics_generated": True,
            "no_definebone_override": '$definebone' not in qc.read_text(encoding="utf-8", errors="replace") and not (modelsrc / "commonbones.qci").exists(),
            "qci_files_generated": all((modelsrc / name).exists() for name in ("ragdoll.qci", "hitbox.qci", "standardikchains.qci")),
            "lod_count": len(lod_objects),
            "source_materials_generated": len(materials),
            "workshop_icon_rendered": icon_rendered,
            "uv_atlas_rebuilt_on_reduced_mesh": texture_bake["atlas"]["layer"] == BAKE_UV_LAYER,
            "original_uv_map_discarded": not texture_bake["source_uv_reused"],
            "texture_baked_from_high_resolution": texture_bake["method"] == "cycles_selected_to_active_diffuse_colour",
            "uv_coordinates_finite": texture_bake["checks"]["uv_coordinates_finite"],
            "uv_inside_atlas": texture_bake["checks"]["uv_inside_atlas"],
            "atlas_islands_do_not_overlap": texture_bake["checks"]["islands_do_not_overlap"],
            "every_triangle_has_bake_coverage": texture_bake["checks"]["every_triangle_has_bake_coverage"],
            "bake_has_colour_variation": texture_bake["checks"]["bake_has_colour_variation"],
            "no_large_unpainted_regions": texture_bake["checks"]["no_large_unpainted_regions"],
            "baked_material_rendered_in_blender": texture_bake["checks"]["baked_material_rendered_in_blender"],
            "texture_bake_passed": texture_bake["passed"],
            "lod_atlas_islands_do_not_overlap": all(summary["islands_do_not_overlap"] for summary in lod_atlas.values()),
        },
        "manual_review": [
            f"Open {slug}_texture_proof_front.png and {slug}_texture_proof_back.png in generated to see the baked material rendered in Blender.",
            "Use the generated Blender source to inspect weight deformation if any joint preview looks wrong.",
            "The final Complete state requires validated VTF output, StudioMDL output and Garry's Mod runtime validation.",
            "First person hands use the standard citizen hands until a dedicated c_arms workflow is added.",
        ],
    }
    reports.joinpath("build_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    reports.joinpath("VALIDATION.md").write_text(
        "# Source generation\n\n"
        "Guided ValveBiped source files were generated from the locked landmark guide.\n\n"
        "The build is not considered complete until VTF generation, StudioMDL and the in game runtime check pass.\n",
        encoding="utf-8",
    )
    log("Guided pipeline source generation complete")


if __name__ == "__main__":
    try:
        if "--" not in sys.argv:
            raise RuntimeError("Expected pipeline config path after --")
        idx = sys.argv.index("--")
        main(Path(sys.argv[idx + 1]))
    except Exception:
        traceback.print_exc()
        sys.exit(1)
