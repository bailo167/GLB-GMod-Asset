"""Exact Garry's Mod humanoid reference bind skeletons.

The values are SMD local transforms in radians, not `$definebone` values.  Earlier
releases incorrectly copied `$definebone` Euler fields into SMD skeleton records.
Those two representations are not interchangeable and the resulting model could
compile while collapsing as soon as Source evaluated the bones.
"""
from __future__ import annotations

import math
from typing import Mapping

Vec3 = tuple[float, float, float]
BoneTemplate = tuple[str | None, Vec3, Vec3]

CORE_ORDER = (
    "ValveBiped.Bip01_Pelvis",
    "ValveBiped.Bip01_Spine",
    "ValveBiped.Bip01_Spine1",
    "ValveBiped.Bip01_Spine2",
    "ValveBiped.Bip01_Spine4",
    "ValveBiped.Bip01_Neck1",
    "ValveBiped.Bip01_Head1",
    "ValveBiped.forward",
    "ValveBiped.Bip01_R_Clavicle",
    "ValveBiped.Bip01_R_UpperArm",
    "ValveBiped.Bip01_R_Forearm",
    "ValveBiped.Bip01_R_Hand",
    "ValveBiped.Anim_Attachment_RH",
    "ValveBiped.Bip01_L_Clavicle",
    "ValveBiped.Bip01_L_UpperArm",
    "ValveBiped.Bip01_L_Forearm",
    "ValveBiped.Bip01_L_Hand",
    "ValveBiped.Anim_Attachment_LH",
    "ValveBiped.Bip01_R_Thigh",
    "ValveBiped.Bip01_R_Calf",
    "ValveBiped.Bip01_R_Foot",
    "ValveBiped.Bip01_R_Toe0",
    "ValveBiped.Bip01_L_Thigh",
    "ValveBiped.Bip01_L_Calf",
    "ValveBiped.Bip01_L_Foot",
    "ValveBiped.Bip01_L_Toe0",
)

# Male reference SMD local transforms.  These are the 26 core bones used by the
# generated body.  Finger bones are intentionally omitted until a guided finger
# workflow exists; stock player activities do not require them to skin the body.
MALE: dict[str, BoneTemplate] = {
    "ValveBiped.Bip01_Pelvis": (None, (-0.002349, -0.531261, 38.566917), (1.570796, 0.0, 0.000001)),
    "ValveBiped.Bip01_Spine": ("ValveBiped.Bip01_Pelvis", (0.000005, 3.345135, -2.981901), (1.570795, 0.086294, 1.570791)),
    "ValveBiped.Bip01_Spine1": ("ValveBiped.Bip01_Spine", (4.018330, 0.0, 0.0), (0.0, 0.000001, -0.029231)),
    "ValveBiped.Bip01_Spine2": ("ValveBiped.Bip01_Spine1", (3.518568, 0.0, 0.0), (0.0, -0.000044, 0.100328)),
    "ValveBiped.Bip01_Spine4": ("ValveBiped.Bip01_Spine2", (8.942643, 0.0, 0.0), (0.000010, 0.000052, 0.194099)),
    "ValveBiped.Bip01_Neck1": ("ValveBiped.Bip01_Spine4", (3.307273, 0.000001, 0.0), (3.141588, -0.000020, 0.372981)),
    "ValveBiped.Bip01_Head1": ("ValveBiped.Bip01_Neck1", (3.593709, -0.000008, 0.0), (-0.000004, 0.000010, 0.302763)),
    "ValveBiped.forward": ("ValveBiped.Bip01_Head1", (1.999999, -3.000001, 0.0), (-0.142926, 1.570771, 0.106194)),
    "ValveBiped.Bip01_R_Clavicle": ("ValveBiped.Bip01_Spine4", (2.033358, 1.000771, -1.937608), (1.567160, 2.023839, -0.181980)),
    "ValveBiped.Bip01_R_UpperArm": ("ValveBiped.Bip01_R_Clavicle", (6.028136, 0.000011, 0.0), (1.570040, -0.001590, 0.453046)),
    "ValveBiped.Bip01_R_Forearm": ("ValveBiped.Bip01_R_UpperArm", (11.692554, 0.0, -0.000004), (-0.000419, -0.000001, 0.0)),
    "ValveBiped.Bip01_R_Hand": ("ValveBiped.Bip01_R_Forearm", (11.481694, 0.0, -0.000007), (-1.563666, 0.106381, 0.130254)),
    "ValveBiped.Anim_Attachment_RH": ("ValveBiped.Bip01_R_Hand", (2.676087, -1.712444, 0.0), (-1.565013, -3.012100, -0.107307)),
    "ValveBiped.Bip01_L_Clavicle": ("ValveBiped.Bip01_Spine4", (2.033347, 1.000767, 1.937661), (-1.577188, -2.025535, -0.171568)),
    "ValveBiped.Bip01_L_UpperArm": ("ValveBiped.Bip01_L_Clavicle", (6.028141, 0.000006, 0.0), (-1.573135, -0.002806, 0.454727)),
    "ValveBiped.Bip01_L_Forearm": ("ValveBiped.Bip01_L_UpperArm", (11.692557, 0.0, 0.000002), (-0.000182, 0.000001, 0.0)),
    "ValveBiped.Bip01_L_Hand": ("ValveBiped.Bip01_L_Forearm", (11.481669, 0.0, -0.000023), (1.573445, -0.106737, 0.134903)),
    "ValveBiped.Anim_Attachment_LH": ("ValveBiped.Bip01_L_Hand", (2.676081, -1.712441, -0.000001), (-1.572524, -0.135184, -0.106380)),
    "ValveBiped.Bip01_R_Thigh": ("ValveBiped.Bip01_Pelvis", (-3.890452, 0.000008, 0.000007), (-1.570525, 0.0, -1.570796)),
    "ValveBiped.Bip01_R_Calf": ("ValveBiped.Bip01_R_Thigh", (17.848173, 0.0, 0.0), (0.004880, 0.0, 0.0)),
    "ValveBiped.Bip01_R_Foot": ("ValveBiped.Bip01_R_Calf", (16.525253, 0.0, -0.000001), (-0.002131, -0.019446, -1.002273)),
    "ValveBiped.Bip01_R_Toe0": ("ValveBiped.Bip01_R_Foot", (6.879453, 0.0, 0.0), (-0.061129, 0.006496, -0.556042)),
    "ValveBiped.Bip01_L_Thigh": ("ValveBiped.Bip01_Pelvis", (3.890452, 0.000004, -0.000003), (-1.571081, 0.0, -1.570796)),
    "ValveBiped.Bip01_L_Calf": ("ValveBiped.Bip01_L_Thigh", (17.848177, 0.0, 0.0), (-0.004933, 0.0, 0.0)),
    "ValveBiped.Bip01_L_Foot": ("ValveBiped.Bip01_L_Calf", (16.525248, 0.0, -0.000001), (0.008798, 0.017185, -1.002420)),
    "ValveBiped.Bip01_L_Toe0": ("ValveBiped.Bip01_L_Foot", (6.879448, -0.000002, 0.000001), (-0.001050, -0.003487, -0.555370)),
}

FEMALE: dict[str, BoneTemplate] = {
    "ValveBiped.Bip01_Pelvis": (None, (-0.000005, -0.788460, 38.481480), (1.570796, 0.0, 0.000001)),
    "ValveBiped.Bip01_Spine": ("ValveBiped.Bip01_Pelvis", (0.000005, 4.212788, -1.689856), (1.570795, 0.086294, 1.570791)),
    "ValveBiped.Bip01_Spine1": ("ValveBiped.Bip01_Spine", (3.837400, 0.000028, 0.000001), (0.0, 0.000001, -0.029231)),
    "ValveBiped.Bip01_Spine2": ("ValveBiped.Bip01_Spine1", (3.617855, 0.000024, -0.000003), (0.0, -0.000044, 0.100328)),
    "ValveBiped.Bip01_Spine4": ("ValveBiped.Bip01_Spine2", (7.539775, 0.000026, 0.0), (0.000010, 0.000052, 0.194099)),
    "ValveBiped.Bip01_Neck1": ("ValveBiped.Bip01_Spine4", (3.178299, -0.000032, 0.000003), (3.141588, -0.000020, 0.372981)),
    "ValveBiped.Bip01_Head1": ("ValveBiped.Bip01_Neck1", (2.970290, -0.000016, 0.0), (-0.000004, 0.000010, 0.302762)),
    "ValveBiped.forward": ("ValveBiped.Bip01_Head1", (0.000002, 0.0, 0.0), (-1.570796, 0.0, -1.326447)),
    "ValveBiped.Bip01_R_Clavicle": ("ValveBiped.Bip01_Spine4", (2.023721, 0.907442, -0.852526), (1.567160, 2.023839, -0.181980)),
    "ValveBiped.Bip01_R_UpperArm": ("ValveBiped.Bip01_R_Clavicle", (4.983667, -0.000013, -0.000002), (1.570040, -0.001590, 0.453046)),
    "ValveBiped.Bip01_R_Forearm": ("ValveBiped.Bip01_R_UpperArm", (11.123065, -0.000033, 0.000014), (-0.000419, -0.000001, 0.0)),
    "ValveBiped.Bip01_R_Hand": ("ValveBiped.Bip01_R_Forearm", (11.208315, -0.000030, 0.000014), (-1.563666, 0.106381, 0.130254)),
    "ValveBiped.Anim_Attachment_RH": ("ValveBiped.Bip01_R_Hand", (2.676091, -1.712462, -0.000001), (-1.570805, 0.000002, -1.570600)),
    "ValveBiped.Bip01_L_Clavicle": ("ValveBiped.Bip01_Spine4", (2.023716, 0.907440, 0.852581), (-1.577188, -2.025535, -0.171568)),
    "ValveBiped.Bip01_L_UpperArm": ("ValveBiped.Bip01_L_Clavicle", (4.983674, -0.000009, 0.000009), (-1.573135, -0.002806, 0.454727)),
    "ValveBiped.Bip01_L_Forearm": ("ValveBiped.Bip01_L_UpperArm", (11.123064, -0.000036, -0.000006), (-0.000182, 0.000001, 0.0)),
    "ValveBiped.Bip01_L_Hand": ("ValveBiped.Bip01_L_Forearm", (11.208270, -0.000034, -0.000040), (1.573445, -0.106737, 0.134903)),
    "ValveBiped.Anim_Attachment_LH": ("ValveBiped.Bip01_L_Hand", (2.676090, -1.712441, 0.0), (1.570795, 0.000002, 1.570888)),
    "ValveBiped.Bip01_R_Thigh": ("ValveBiped.Bip01_Pelvis", (-3.984013, 0.000008, 0.000007), (-1.570525, 0.0, -1.570796)),
    "ValveBiped.Bip01_R_Calf": ("ValveBiped.Bip01_R_Thigh", (15.940014, 0.000156, 0.000001), (0.004880, 0.0, 0.0)),
    "ValveBiped.Bip01_R_Foot": ("ValveBiped.Bip01_R_Calf", (17.709564, -0.000012, 0.000001), (-0.002131, -0.019446, -1.002273)),
    "ValveBiped.Bip01_R_Toe0": ("ValveBiped.Bip01_R_Foot", (6.203997, -0.000012, -0.000003), (-0.061129, 0.006496, -0.556042)),
    "ValveBiped.Bip01_L_Thigh": ("ValveBiped.Bip01_Pelvis", (3.984014, 0.0, -0.000003), (-1.571081, 0.0, -1.570796)),
    "ValveBiped.Bip01_L_Calf": ("ValveBiped.Bip01_L_Thigh", (15.940022, 0.000153, 0.000005), (-0.004933, 0.0, 0.0)),
    "ValveBiped.Bip01_L_Foot": ("ValveBiped.Bip01_L_Calf", (17.709564, -0.000014, -0.000004), (0.008798, 0.017185, -1.002420)),
    "ValveBiped.Bip01_L_Toe0": ("ValveBiped.Bip01_L_Foot", (6.203997, 0.000014, 0.0), (-0.001050, -0.003487, -0.555370)),
}

TEMPLATES: dict[str, Mapping[str, BoneTemplate]] = {"male": MALE, "female": FEMALE}


def get_template(animation_base: str) -> Mapping[str, BoneTemplate]:
    return TEMPLATES.get(str(animation_base).lower(), MALE)


def _matmul(a: tuple[tuple[float, ...], ...], b: tuple[tuple[float, ...], ...]) -> tuple[tuple[float, ...], ...]:
    return tuple(tuple(sum(a[r][k] * b[k][c] for k in range(4)) for c in range(4)) for r in range(4))


def _local_matrix(position: Vec3, rotation: Vec3, scale_factor: float = 1.0) -> tuple[tuple[float, ...], ...]:
    x, y, z = rotation
    cx, sx = math.cos(x), math.sin(x)
    cy, sy = math.cos(y), math.sin(y)
    cz, sz = math.cos(z), math.sin(z)
    # Blender Euler XYZ for column vectors: Rz @ Ry @ Rx.
    r00, r01, r02 = cz * cy, cz * sy * sx - sz * cx, cz * sy * cx + sz * sx
    r10, r11, r12 = sz * cy, sz * sy * sx + cz * cx, sz * sy * cx - cz * sx
    r20, r21, r22 = -sy, cy * sx, cy * cx
    px, py, pz = (value * scale_factor for value in position)
    return (
        (r00, r01, r02, px),
        (r10, r11, r12, py),
        (r20, r21, r22, pz),
        (0.0, 0.0, 0.0, 1.0),
    )


def world_positions(animation_base: str = "male", target_height: float = 72.0) -> dict[str, Vec3]:
    template = get_template(animation_base)
    factor = float(target_height) / 72.0
    world: dict[str, tuple[tuple[float, ...], ...]] = {}
    positions: dict[str, Vec3] = {}
    for name in CORE_ORDER:
        parent, position, rotation = template[name]
        local = _local_matrix(position, rotation, factor)
        matrix = _matmul(world[parent], local) if parent else local
        world[name] = matrix
        positions[name] = (matrix[0][3], matrix[1][3], matrix[2][3])
    return positions


def validate_template(animation_base: str = "male") -> dict[str, float | int | bool]:
    template = get_template(animation_base)
    positions = world_positions(animation_base, 72.0)
    valid = (
        tuple(template.keys()) == CORE_ORDER
        and template[CORE_ORDER[0]][0] is None
        and positions["ValveBiped.Bip01_Head1"][2] > positions["ValveBiped.Bip01_Pelvis"][2] + 24.0
        and positions["ValveBiped.Bip01_L_Hand"][0] > 20.0
        and positions["ValveBiped.Bip01_R_Hand"][0] < -20.0
        and positions["ValveBiped.Bip01_L_Foot"][2] < 6.0
        and positions["ValveBiped.Bip01_R_Foot"][2] < 6.0
    )
    return {
        "passed": valid,
        "bone_count": len(template),
        "head_z": positions["ValveBiped.Bip01_Head1"][2],
        "pelvis_z": positions["ValveBiped.Bip01_Pelvis"][2],
        "left_hand_x": positions["ValveBiped.Bip01_L_Hand"][0],
        "right_hand_x": positions["ValveBiped.Bip01_R_Hand"][0],
    }


for _base in TEMPLATES:
    if not validate_template(_base)["passed"]:
        raise RuntimeError(f"Invalid embedded Source skeleton template: {_base}")
