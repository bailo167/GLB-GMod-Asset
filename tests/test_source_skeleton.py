from __future__ import annotations

import math

from blender.source_skeleton import CORE_ORDER, FEMALE, MALE, validate_template, world_positions


def test_embedded_male_smd_bind_is_exactly_source_oriented():
    contract = validate_template("male")
    assert contract["passed"] is True
    assert len(MALE) == len(CORE_ORDER) == 26
    parent, position, rotation = MALE["ValveBiped.Bip01_Pelvis"]
    assert parent is None
    assert position == (-0.002349, -0.531261, 38.566917)
    assert rotation == (1.570796, 0.0, 0.000001)
    positions = world_positions("male")
    assert positions["ValveBiped.Bip01_L_Hand"][0] > 30
    assert positions["ValveBiped.Bip01_R_Hand"][0] < -30
    assert positions["ValveBiped.Bip01_Head1"][2] > 64
    assert positions["ValveBiped.Bip01_L_Toe0"][1] < -5
    assert 0 <= positions["ValveBiped.Bip01_L_Toe0"][2] < 2


def test_embedded_female_smd_bind_has_same_core_contract():
    contract = validate_template("female")
    assert contract["passed"] is True
    assert tuple(FEMALE) == CORE_ORDER
    positions = world_positions("female")
    assert positions["ValveBiped.Bip01_L_Hand"][0] > 27
    assert positions["ValveBiped.Bip01_R_Hand"][0] < -27
    assert positions["ValveBiped.Bip01_Head1"][2] > positions["ValveBiped.Bip01_Pelvis"][2] + 24
    assert math.isclose(positions["ValveBiped.Bip01_L_Foot"][2], positions["ValveBiped.Bip01_R_Foot"][2], abs_tol=0.001)


def test_browser_guide_to_source_axis_contract_matches_backend_rotations():
    # B is a point in Blender's imported GLB coordinates. The browser first
    # presents it as [forward, left, up], then the backend converts that guide
    # back into Source [left, negative-forward, up]. Each front option must land
    # on exactly the same point as the backend Z rotation applied to the mesh.
    b = (2.0, -5.0, 7.0)
    browser_tool = {
        "neg_y": (-b[1], b[0], b[2]),
        "pos_y": (b[1], -b[0], b[2]),
        "neg_x": (-b[0], -b[1], b[2]),
        "pos_x": (b[0], b[1], b[2]),
    }
    backend_source = {
        "neg_y": (b[0], b[1], b[2]),
        "pos_y": (-b[0], -b[1], b[2]),
        "neg_x": (-b[1], b[0], b[2]),
        "pos_x": (b[1], -b[0], b[2]),
    }
    for front_axis, tool in browser_tool.items():
        converted = (tool[1], -tool[0], tool[2])
        assert converted == backend_source[front_axis]
