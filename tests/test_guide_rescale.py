"""The locked guide must follow the mesh when the target height changes.

The Jack Hegarty guide was locked while the project height was 72 inches. A
later build ran with the height at 64, so the mesh was normalised to 64 units
while every landmark stayed at the 72 unit scale. The head landmark floated
eight units above the head, the skeleton conformance stage measured an average
displacement of 5.1 units against a previous passing run of 2.8, and the build
was rejected. The pipeline now rescales the guide to the mesh height before
anything reads it.
"""

from blender.rig_math import rescale_guide_landmarks


def guide_at_72() -> dict[str, list[float]]:
    return {
        "head_top": [0.0, 0.0, 72.0], "neck_base": [0.0, 0.0, 59.0], "pelvis": [0.0, 0.0, 35.0],
        "shoulder_l": [11.0, 0.0, 57.0], "wrist_l": [29.0, 0.0, 55.0],
        "shoulder_r": [-11.0, 0.0, 57.0], "wrist_r": [-29.0, 0.0, 55.0],
        "ankle_l": [4.0, 0.0, 4.0], "toe_l": [4.0, -6.0, 1.0],
    }


def test_the_jack_hegarty_height_change_is_corrected():
    landmarks = guide_at_72()
    zones = [{"center": [0.0, 0.0, 66.0], "radius": 6.0, "bone": "ValveBiped.Bip01_Head1"}]
    result = rescale_guide_landmarks(landmarks, zones, 64.0)
    assert result["applied"] is True
    assert abs(result["factor"] - 64.0 / 72.0) < 1e-9
    # The head lands exactly at the top of the 64 unit mesh instead of eight
    # units above it, and lateral positions scale with it.
    assert abs(landmarks["head_top"][2] - 64.0) < 1e-6
    assert abs(landmarks["wrist_l"][0] - 29.0 * 64.0 / 72.0) < 1e-6
    assert abs(zones[0]["center"][2] - 66.0 * 64.0 / 72.0) < 1e-6
    assert abs(zones[0]["radius"] - 6.0 * 64.0 / 72.0) < 1e-6


def test_the_opposite_direction_also_corrects():
    landmarks = {key: [v * 64.0 / 72.0 for v in point] for key, point in guide_at_72().items()}
    result = rescale_guide_landmarks(landmarks, [], 72.0)
    assert result["applied"] is True
    assert abs(landmarks["head_top"][2] - 72.0) < 1e-6


def test_a_matching_guide_is_left_alone():
    landmarks = guide_at_72()
    result = rescale_guide_landmarks(landmarks, [], 72.0)
    assert result["applied"] is False
    assert result["reason"] == "already_matched"
    assert landmarks["head_top"][2] == 72.0

    # Hair or a hat can hold the mesh top slightly above the head landmark;
    # small differences are normal and must not trigger a rescale.
    slightly = rescale_guide_landmarks(guide_at_72(), [], 73.5)
    assert slightly["applied"] is False


def test_an_implausible_scale_is_refused_rather_than_hidden():
    # A guide in millimetres or an empty height means corruption, not a height
    # edit. Scaling it would mask the real problem.
    landmarks = guide_at_72()
    result = rescale_guide_landmarks(landmarks, [], 6.0)
    assert result["applied"] is False
    assert result["reason"] == "implausible_factor"
    assert landmarks["head_top"][2] == 72.0

    assert rescale_guide_landmarks({}, [], 64.0)["applied"] is False


def test_the_pipeline_rescales_before_anything_reads_the_guide():
    from pathlib import Path

    pipeline = (Path(__file__).resolve().parents[1] / "blender" / "pipeline.py").read_text(encoding="utf-8")
    rescale = pipeline.index("guide_rescale = rescale_guide_landmarks(")
    anatomy = pipeline.index("guide_anatomy = validate_source_guide(source_guide")
    bones = pipeline.index("source_bones = guided_source_bones(source_guide")
    zones = pipeline.index("apply_rigid_zones(obj, source_guide.get(")
    assert rescale < anatomy
    assert rescale < bones
    assert rescale < zones
    assert '"guide_rescale": guide_rescale' in pipeline
