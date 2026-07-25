from __future__ import annotations

from blender.rig_math import anatomical_influences, classify_region, validate_influences


def segments():
    return {
        "ValveBiped.Bip01_Pelvis": ((0, 0, 36), (0, 0, 42)),
        "ValveBiped.Bip01_Spine": ((0, 0, 42), (0, 0, 47)),
        "ValveBiped.Bip01_Spine1": ((0, 0, 47), (0, 0, 52)),
        "ValveBiped.Bip01_Spine2": ((0, 0, 52), (0, 0, 57)),
        "ValveBiped.Bip01_Spine4": ((0, 0, 57), (0, 0, 60)),
        "ValveBiped.Bip01_Neck1": ((0, 0, 60), (0, 0, 64)),
        "ValveBiped.Bip01_Head1": ((0, 0, 64), (0, 0, 72)),
        "ValveBiped.Bip01_L_Clavicle": ((0, 0, 58), (8, 0, 58)),
        "ValveBiped.Bip01_L_UpperArm": ((8, 0, 58), (18, 0, 58)),
        "ValveBiped.Bip01_L_Forearm": ((18, 0, 58), (29, 0, 58)),
        "ValveBiped.Bip01_L_Hand": ((29, 0, 58), (34, 0, 58)),
        "ValveBiped.Bip01_R_Clavicle": ((0, 0, 58), (-8, 0, 58)),
        "ValveBiped.Bip01_R_UpperArm": ((-8, 0, 58), (-18, 0, 58)),
        "ValveBiped.Bip01_R_Forearm": ((-18, 0, 58), (-29, 0, 58)),
        "ValveBiped.Bip01_R_Hand": ((-29, 0, 58), (-34, 0, 58)),
        "ValveBiped.Bip01_L_Thigh": ((4, 0, 36), (4, 0, 20)),
        "ValveBiped.Bip01_L_Calf": ((4, 0, 20), (4, 0, 4)),
        "ValveBiped.Bip01_L_Foot": ((4, 0, 4), (4, -6, 1)),
        "ValveBiped.Bip01_L_Toe0": ((4, -6, 1), (4, -9, 1)),
        "ValveBiped.Bip01_R_Thigh": ((-4, 0, 36), (-4, 0, 20)),
        "ValveBiped.Bip01_R_Calf": ((-4, 0, 20), (-4, 0, 4)),
        "ValveBiped.Bip01_R_Foot": ((-4, 0, 4), (-4, -6, 1)),
        "ValveBiped.Bip01_R_Toe0": ((-4, -6, 1), (-4, -9, 1)),
    }


def test_anatomical_regions_never_cross_left_and_right_limbs():
    data = segments()
    cases = [
        ((14, 0, 58), "left_arm", "_L_"),
        ((-14, 0, 58), "right_arm", "_R_"),
        ((4, 0, 16), "left_leg", "_L_"),
        ((-4, 0, 16), "right_leg", "_R_"),
    ]
    for point, expected_region, side_marker in cases:
        assert classify_region(point, data, 72) == expected_region
        influences = anatomical_influences(point, data, 72, 3)
        validate_influences(influences, 3)
        assert len(influences) <= 3
        assert abs(sum(weight for _name, weight in influences) - 1) < 1e-8
        assert all(side_marker in name for name, _weight in influences)


def test_torso_weights_do_not_leak_into_limbs():
    data = segments()
    influences = anatomical_influences((0, 0, 50), data, 72, 3)
    validate_influences(influences, 3)
    assert all("_L_" not in name and "_R_" not in name for name, _weight in influences)

from blender.rig_math import robust_edge_deformation


def test_microscopic_edge_ratio_does_not_block_an_otherwise_safe_mesh():
    # Reproduces the Jack Hegarty failure shape: 48,000 sampled edges were normal,
    # while one microscopic decimation edge reported about 70.886x despite the
    # complete mesh moving only a few Source units.
    edges = [(0.65, 0.67)] * 47999
    edges.append((0.005, 0.354432))
    report = robust_edge_deformation(edges, 72.0)
    assert report["passed"] is True
    assert report["raw_maximum_ratio"] > 70
    assert report["severe_outliers"] == 0


def test_real_edge_explosion_is_still_rejected():
    edges = [(0.5, 0.52)] * 1000 + [(0.5, 20.0)] * 20
    report = robust_edge_deformation(edges, 72.0)
    assert report["passed"] is False
    assert report["severe_outliers"] >= 20
