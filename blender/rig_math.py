"""Pure geometry helpers for the guided humanoid weighting stage.

This module deliberately has no Blender dependency so the region classifier can be
unit tested outside Blender. Source humanoid coordinates are X left, negative Y forward and Z up.
"""
from __future__ import annotations

import math
from typing import Iterable, Mapping, Sequence

Vec3 = tuple[float, float, float]
Segment = tuple[Vec3, Vec3]


def sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def add(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def scale(a: Vec3, value: float) -> Vec3:
    return (a[0] * value, a[1] * value, a[2] * value)


def dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def length_squared(a: Vec3) -> float:
    return dot(a, a)


def length(a: Vec3) -> float:
    return math.sqrt(length_squared(a))


def point_segment_distance(point: Vec3, segment: Segment) -> float:
    a, b = segment
    ab = sub(b, a)
    denominator = length_squared(ab)
    if denominator <= 1e-12:
        return length(sub(point, a))
    t = max(0.0, min(1.0, dot(sub(point, a), ab) / denominator))
    closest = add(a, scale(ab, t))
    return length(sub(point, closest))


def _minimum_scaled_distance(
    point: Vec3,
    names: Iterable[str],
    segments: Mapping[str, Segment],
    radius: float,
) -> float:
    values = [point_segment_distance(point, segments[name]) / max(radius, 1e-6) for name in names if name in segments]
    return min(values) if values else 1e9


TORSO = (
    "ValveBiped.Bip01_Pelvis",
    "ValveBiped.Bip01_Spine",
    "ValveBiped.Bip01_Spine1",
    "ValveBiped.Bip01_Spine2",
    "ValveBiped.Bip01_Spine4",
    "ValveBiped.Bip01_Neck1",
)
HEAD = (
    "ValveBiped.Bip01_Neck1",
    "ValveBiped.Bip01_Head1",
)
LEFT_ARM = (
    "ValveBiped.Bip01_L_Clavicle",
    "ValveBiped.Bip01_L_UpperArm",
    "ValveBiped.Bip01_L_Forearm",
    "ValveBiped.Bip01_L_Hand",
)
RIGHT_ARM = tuple(name.replace("_L_", "_R_") for name in LEFT_ARM)
LEFT_LEG = (
    "ValveBiped.Bip01_L_Thigh",
    "ValveBiped.Bip01_L_Calf",
    "ValveBiped.Bip01_L_Foot",
    "ValveBiped.Bip01_L_Toe0",
)
RIGHT_LEG = tuple(name.replace("_L_", "_R_") for name in LEFT_LEG)

REGION_NAMES = {
    "torso": TORSO,
    "head": HEAD,
    "left_arm": LEFT_ARM,
    "right_arm": RIGHT_ARM,
    "left_leg": LEFT_LEG,
    "right_leg": RIGHT_LEG,
}


def classify_region(point: Vec3, segments: Mapping[str, Segment], height: float) -> str:
    """Choose one anatomical region before calculating bone weights.

    The first release compared every vertex against every bone. On generated meshes
    that lets torso vertices select an arm or leg through the opposite side of a thin
    surface. This classifier prevents cross limb contamination before blending.
    """
    h = max(float(height), 1.0)
    pelvis_z = segments.get("ValveBiped.Bip01_Pelvis", ((0.0, 0.0, h * 0.48),) * 2)[0][2]
    neck_z = segments.get("ValveBiped.Bip01_Neck1", ((0.0, 0.0, h * 0.82),) * 2)[0][2]

    scores = {
        "torso": _minimum_scaled_distance(point, TORSO, segments, h * 0.105),
        "head": _minimum_scaled_distance(point, HEAD, segments, h * 0.105),
        "left_arm": _minimum_scaled_distance(point, LEFT_ARM, segments, h * 0.058),
        "right_arm": _minimum_scaled_distance(point, RIGHT_ARM, segments, h * 0.058),
        "left_leg": _minimum_scaled_distance(point, LEFT_LEG, segments, h * 0.072),
        "right_leg": _minimum_scaled_distance(point, RIGHT_LEG, segments, h * 0.072),
    }

    # Strong anatomical gates keep distant capsules from winning on thin or hollow
    # generated geometry while leaving soft overlap around shoulders and hips.
    if point[2] < neck_z - h * 0.10:
        scores["head"] += 5.0
    if point[2] < pelvis_z + h * 0.05:
        scores["left_arm"] += 4.0
        scores["right_arm"] += 4.0
    if point[2] > pelvis_z + h * 0.20:
        scores["left_leg"] += 4.0
        scores["right_leg"] += 4.0

    # Source X is lateral, positive X is model left. A side penalty prevents one
    # arm or leg taking vertices from the opposite side near the chest or pelvis.
    side_penalty = 3.5
    if point[0] < -h * 0.01:
        scores["left_arm"] += side_penalty
        scores["left_leg"] += side_penalty
    elif point[0] > h * 0.01:
        scores["right_arm"] += side_penalty
        scores["right_leg"] += side_penalty

    return min(scores, key=scores.get)


def anatomical_influences(
    point: Sequence[float],
    segments: Mapping[str, Segment],
    height: float,
    max_influences: int = 3,
) -> list[tuple[str, float]]:
    """Return normalized weights restricted to one anatomical region."""
    p: Vec3 = (float(point[0]), float(point[1]), float(point[2]))
    region = classify_region(p, segments, height)
    names = REGION_NAMES[region]
    h = max(float(height), 1.0)
    radius = h * ({
        "torso": 0.085,
        "head": 0.080,
        "left_arm": 0.047,
        "right_arm": 0.047,
        "left_leg": 0.058,
        "right_leg": 0.058,
    }[region])

    candidates: list[tuple[str, float]] = []
    for name in names:
        segment = segments.get(name)
        if segment is None:
            continue
        distance = point_segment_distance(p, segment)
        normalized = max(distance / max(radius, 1e-6), 0.14)
        candidates.append((name, 1.0 / (normalized ** 4)))
    candidates.sort(key=lambda item: item[1], reverse=True)
    selected = candidates[:max(1, int(max_influences))]
    if not selected:
        return [("ValveBiped.Bip01_Pelvis", 1.0)]
    total = sum(weight for _name, weight in selected)
    return [(name, weight / total) for name, weight in selected]


def validate_influences(influences: Sequence[tuple[str, float]], max_influences: int = 3) -> None:
    if not influences or len(influences) > max_influences:
        raise ValueError("Invalid influence count")
    if any(not math.isfinite(weight) or weight <= 0 for _name, weight in influences):
        raise ValueError("Influences contain a non positive or non finite weight")
    if abs(sum(weight for _name, weight in influences) - 1.0) > 1e-5:
        raise ValueError("Influence weights do not sum to one")


def _percentile(sorted_values: Sequence[float], fraction: float) -> float:
    if not sorted_values:
        return 1.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    position = max(0.0, min(1.0, float(fraction))) * (len(sorted_values) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return float(sorted_values[lower])
    blend = position - lower
    return float(sorted_values[lower] * (1.0 - blend) + sorted_values[upper] * blend)


def robust_edge_deformation(
    edge_lengths: Iterable[tuple[float, float]],
    height: float,
) -> dict[str, float | int | bool]:
    """Summarise edge deformation without letting one microscopic edge block a build.

    Ratio-only checks are numerically unstable on decimated or generated meshes. An
    edge only a few thousandths of a Source unit long can report a 50x or 100x ratio
    after a harmless sub-unit movement. We therefore keep raw extrema for diagnosis,
    but gate on meaningful edges, robust percentiles, absolute change and outlier
    prevalence.
    """
    h = max(float(height), 1.0)
    meaningful_length = max(h * 0.00025, 0.01)
    absolute_outlier_change = max(h * 0.006, 0.12)
    ratios: list[float] = []
    meaningful_ratios: list[float] = []
    absolute_changes: list[float] = []
    severe_outliers = 0
    raw_min = math.inf
    raw_max = 0.0
    maximum_absolute_change = 0.0
    shortest_edge = math.inf
    longest_edge = 0.0
    samples = 0

    for before, after in edge_lengths:
        old = float(before)
        new = float(after)
        if not math.isfinite(old) or not math.isfinite(new) or old <= 1.0e-8 or new < 0.0:
            continue
        ratio = new / old
        change = abs(new - old)
        samples += 1
        raw_min = min(raw_min, ratio)
        raw_max = max(raw_max, ratio)
        maximum_absolute_change = max(maximum_absolute_change, change)
        shortest_edge = min(shortest_edge, old)
        longest_edge = max(longest_edge, old)
        ratios.append(ratio)
        absolute_changes.append(change)
        if old >= meaningful_length:
            meaningful_ratios.append(ratio)
            if (ratio < 0.08 or ratio > 5.0) and change > absolute_outlier_change:
                severe_outliers += 1

    if not ratios:
        return {
            "passed": True,
            "samples": 0,
            "meaningful_samples": 0,
            "meaningful_length": meaningful_length,
            "raw_minimum_ratio": 1.0,
            "raw_maximum_ratio": 1.0,
            "ratio_p001": 1.0,
            "ratio_p01": 1.0,
            "ratio_p99": 1.0,
            "ratio_p999": 1.0,
            "maximum_absolute_change": 0.0,
            "severe_outliers": 0,
            "severe_outlier_fraction": 0.0,
            "shortest_edge": 0.0,
            "longest_edge": 0.0,
        }

    meaningful = sorted(meaningful_ratios or ratios)
    severe_fraction = severe_outliers / max(len(meaningful_ratios), 1)
    p001 = _percentile(meaningful, 0.001)
    p01 = _percentile(meaningful, 0.01)
    p99 = _percentile(meaningful, 0.99)
    p999 = _percentile(meaningful, 0.999)
    passed = (
        p001 >= 0.035
        and p999 <= 12.0
        and severe_fraction <= 0.0025
        and maximum_absolute_change <= h * 0.35
    )
    return {
        "passed": passed,
        "samples": samples,
        "meaningful_samples": len(meaningful_ratios),
        "meaningful_length": meaningful_length,
        "raw_minimum_ratio": raw_min if math.isfinite(raw_min) else 1.0,
        "raw_maximum_ratio": raw_max,
        "ratio_p001": p001,
        "ratio_p01": p01,
        "ratio_p99": p99,
        "ratio_p999": p999,
        "maximum_absolute_change": maximum_absolute_change,
        "severe_outliers": severe_outliers,
        "severe_outlier_fraction": severe_fraction,
        "shortest_edge": shortest_edge if math.isfinite(shortest_edge) else 0.0,
        "longest_edge": longest_edge,
    }


def repair_localized_edge_outliers(
    rest: Sequence[Vec3],
    warped: Sequence[Vec3],
    edges: Sequence[tuple[int, int]],
    height: float,
    max_outlier_fraction: float = 0.01,
    iterations: int = 12,
) -> dict:
    """Repair a warp whose only defect is a small set of localized edge outliers.

    The per-bone bind warp assigns each vertex the blend of its own influences.
    At an anatomical region boundary two adjacent vertices can receive different
    influence sets, and on the wrong mesh that stretches the shared edge far
    beyond its rest length while every surrounding edge stays clean. The Bailey 2
    build measured 131 severe edges out of 35,998 sampled - 0.36%, all localized -
    and was rejected outright even though 99.6% of the surface warped cleanly.

    Rejecting that build helps nobody: the no-warp path is unavailable precisely
    because the model needs the warp. Instead, the displacement of each offending
    vertex is replaced by the average displacement of its neighbours, iterated
    until the field is locally smooth. The repair is only attempted when the
    severe edges are genuinely rare; a widespread explosion keeps failing.
    """
    h = max(float(height), 1.0)
    meaningful_length = max(h * 0.00025, 0.01)
    absolute_outlier_change = max(h * 0.006, 0.12)

    meaningful_edges = 0
    bad_vertices: set[int] = set()
    severe_edges = 0
    for first, second in edges:
        old = length(sub(rest[second], rest[first]))
        if old < meaningful_length or old <= 1.0e-8:
            continue
        meaningful_edges += 1
        new = length(sub(warped[second], warped[first]))
        ratio = new / old
        if (ratio < 0.08 or ratio > 5.0) and abs(new - old) > absolute_outlier_change:
            severe_edges += 1
            bad_vertices.add(int(first))
            bad_vertices.add(int(second))

    if severe_edges == 0:
        return {"attempted": False, "reason": "no_outliers", "severe_edges": 0, "outlier_vertices": 0, "repaired": None}
    fraction = severe_edges / max(meaningful_edges, 1)
    if fraction > max_outlier_fraction:
        return {
            "attempted": False,
            "reason": "outliers_not_localized",
            "severe_edges": severe_edges,
            "outlier_vertices": len(bad_vertices),
            "severe_edge_fraction": fraction,
            "repaired": None,
        }

    adjacency: dict[int, set[int]] = {}
    for first, second in edges:
        adjacency.setdefault(int(first), set()).add(int(second))
        adjacency.setdefault(int(second), set()).add(int(first))

    # Clean neighbours anchor the field: averaging a bad vertex only from other
    # bad vertices lets the spike bounce back and forth between them, so good
    # neighbours are preferred and every correction is applied immediately.
    displacement: list[Vec3] = [sub(warped[index], rest[index]) for index in range(len(rest))]
    for _iteration in range(max(1, int(iterations))):
        for index in sorted(bad_vertices):
            neighbours = adjacency.get(index, ())
            if not neighbours:
                continue
            good = [neighbour for neighbour in neighbours if neighbour not in bad_vertices]
            source_set = good or sorted(neighbours)
            total = (0.0, 0.0, 0.0)
            for neighbour in source_set:
                total = add(total, displacement[neighbour])
            displacement[index] = scale(total, 1.0 / len(source_set))

    repaired = [add(rest[index], displacement[index]) for index in range(len(rest))]
    return {
        "attempted": True,
        "reason": "localized",
        "severe_edges": severe_edges,
        "outlier_vertices": len(bad_vertices),
        "severe_edge_fraction": fraction,
        "iterations": max(1, int(iterations)),
        "repaired": repaired,
    }


def rescale_guide_landmarks(
    landmarks: dict,
    rigid_zones: list,
    mesh_height: float,
    tolerance: float = 0.04,
) -> dict:
    """Rescale a locked guide to the height the mesh is actually normalised to.

    The landmarks are stored in absolute Source units at whatever height the
    project used when the guide was locked. If the project's target height is
    later different, every landmark sits proportionally off the body: on the
    Jack Hegarty project the guide was locked at 72 units and the mesh was
    normalised to 64, so the head landmark floated eight units above the head
    and the skeleton conformance stage rejected the build with an average
    displacement of 5.1 units. The guide's own vertical extent tells us the
    scale it was locked at, so the mismatch is corrected here instead of being
    allowed to reach the conformance gate. Everything is scaled uniformly about
    the origin, which is the ground point in both spaces.
    """
    tops = [
        float(point[2])
        for point in landmarks.values()
        if isinstance(point, (list, tuple)) and len(point) == 3 and math.isfinite(float(point[2]))
    ]
    if not tops or float(mesh_height) <= 1e-6:
        return {"applied": False, "reason": "no_guide_height", "factor": 1.0}
    guide_height = max(tops)
    if guide_height <= 1e-6:
        return {"applied": False, "reason": "no_guide_height", "factor": 1.0}
    factor = float(mesh_height) / guide_height
    result = {
        "guide_height": guide_height,
        "mesh_height": float(mesh_height),
        "factor": factor,
    }
    if abs(factor - 1.0) <= tolerance:
        result.update({"applied": False, "reason": "already_matched"})
        return result
    if not 0.25 <= factor <= 4.0:
        # A factor this far out means the guide is not in Source units at all;
        # scaling it would hide a real corruption rather than fix a height edit.
        result.update({"applied": False, "reason": "implausible_factor"})
        return result
    for key, point in list(landmarks.items()):
        if isinstance(point, (list, tuple)) and len(point) == 3:
            landmarks[key] = [float(point[0]) * factor, float(point[1]) * factor, float(point[2]) * factor]
    zones_scaled = 0
    for zone in rigid_zones:
        if not isinstance(zone, dict):
            continue
        centre = zone.get("center")
        if isinstance(centre, (list, tuple)) and len(centre) == 3:
            zone["center"] = [float(centre[0]) * factor, float(centre[1]) * factor, float(centre[2]) * factor]
        try:
            zone["radius"] = float(zone.get("radius", 0.0)) * factor
        except (TypeError, ValueError):
            pass
        zones_scaled += 1
    result.update({"applied": True, "reason": "height_mismatch", "rigid_zones_scaled": zones_scaled})
    return result
