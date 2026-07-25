"""Deterministic UV atlas and texture bake validation.

This module deliberately contains no Blender imports.  Every rule that decides
whether a rebuilt UV atlas and a baked texture are acceptable lives here as
plain Python so the desktop test suite can prove the rules without Blender.

Version 2.1.1 reused the UV coordinates that shipped inside the original GLB
after a heavy Decimate pass.  Collapsing a vertex that sits on a UV seam merges
loops belonging to different atlas islands, so the surviving triangles sample
unrelated parts of the texture.  That is what produced triangular fragments of
skin on clothing.  Version 2.2 rebuilds the atlas on the reduced mesh and bakes
the colour from the untouched high resolution import, and then proves the result
here before StudioMDL is ever launched.
"""

from __future__ import annotations

import math
from collections import deque
from typing import Any, Iterable, Sequence

Uv = Sequence[float]
Triangle = Sequence[Uv]

# Validation thresholds.  These are intentionally strict: a build that trips one
# of them produced a texture that would look wrong in Garry's Mod.
MAX_UV_OUTSIDE_RATIO = 0.002
MAX_ISLAND_OVERLAP_RATIO = 0.02
MIN_TRIANGLE_COVERAGE_RATIO = 0.995
MIN_COLOUR_STANDARD_DEVIATION = 0.01
MIN_DISTINCT_COLOURS = 8
MAX_UNPAINTED_ISLAND_RATIO = 0.02
MAX_LARGEST_UNPAINTED_RATIO = 0.01
MIN_ATLAS_USAGE_RATIO = 0.05


def _finite(value: float) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def uv_bounds_report(uvs: Iterable[Uv], tolerance: float = 1e-4) -> dict[str, Any]:
    """Prove every UV coordinate is a finite number inside the atlas."""
    count = 0
    non_finite = 0
    outside = 0
    min_u = min_v = float("inf")
    max_u = max_v = float("-inf")
    for uv in uvs:
        count += 1
        u, v = float(uv[0]), float(uv[1])
        if not (_finite(u) and _finite(v)):
            non_finite += 1
            continue
        min_u, max_u = min(min_u, u), max(max_u, u)
        min_v, max_v = min(min_v, v), max(max_v, v)
        if u < -tolerance or u > 1.0 + tolerance or v < -tolerance or v > 1.0 + tolerance:
            outside += 1
    if count == 0 or min_u == float("inf"):
        return {
            "count": count,
            "non_finite": non_finite,
            "outside": outside,
            "outside_ratio": 1.0 if count else 0.0,
            "min_u": 0.0, "max_u": 0.0, "min_v": 0.0, "max_v": 0.0,
            "all_finite": count > 0 and non_finite == 0,
            "inside_atlas": False,
        }
    outside_ratio = outside / count
    return {
        "count": count,
        "non_finite": non_finite,
        "outside": outside,
        "outside_ratio": outside_ratio,
        "min_u": min_u, "max_u": max_u, "min_v": min_v, "max_v": max_v,
        "all_finite": non_finite == 0,
        "inside_atlas": non_finite == 0 and outside_ratio <= MAX_UV_OUTSIDE_RATIO,
    }


def total_uv_area(triangles: Iterable[Triangle]) -> float:
    """Exact summed UV area of every triangle, as a fraction of the atlas."""
    total = 0.0
    for triangle in triangles:
        try:
            (x0, y0), (x1, y1), (x2, y2) = ((float(p[0]), float(p[1])) for p in triangle)
        except (TypeError, ValueError):
            continue
        if not all(_finite(value) for value in (x0, y0, x1, y1, x2, y2)):
            continue
        total += abs((x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)) * 0.5
    return total


def rasterize_triangles(triangles: Iterable[Triangle], resolution: int) -> list[int]:
    """Return a per pixel coverage count for the UV triangles.

    The counts are only ever read as a union mask.  Neighbouring triangles share
    edges and vertices, so a raw count above one proves nothing about island
    overlap; that is measured by comparing the exact summed UV area against this
    union in `island_report`.
    """
    resolution = max(8, int(resolution))
    counts = [0] * (resolution * resolution)
    for triangle in triangles:
        points = []
        skip = False
        for uv in triangle:
            u, v = float(uv[0]), float(uv[1])
            if not (_finite(u) and _finite(v)):
                skip = True
                break
            points.append((min(1.0, max(0.0, u)) * (resolution - 1), min(1.0, max(0.0, v)) * (resolution - 1)))
        if skip or len(points) != 3:
            continue
        (x0, y0), (x1, y1), (x2, y2) = points
        touched: set[int] = set()
        # Always mark the vertex texels so triangles thinner than one pixel,
        # which the barycentric scan would miss entirely, still register.
        for px, py in points:
            touched.add(int(round(py)) * resolution + int(round(px)))
        min_x, max_x = int(math.floor(min(x0, x1, x2))), int(math.ceil(max(x0, x1, x2)))
        min_y, max_y = int(math.floor(min(y0, y1, y2))), int(math.ceil(max(y0, y1, y2)))
        area = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
        if abs(area) > 1e-12:
            # Four samples per pixel. A single centre sample loses the partly
            # covered pixels along every island edge, and that missing area would
            # otherwise read back as island overlap on a perfectly packed atlas.
            for py in range(max(0, min_y), min(resolution - 1, max_y) + 1):
                for px in range(max(0, min_x), min(resolution - 1, max_x) + 1):
                    for offset_y in (0.25, 0.75):
                        for offset_x in (0.25, 0.75):
                            sx, sy = px + offset_x, py + offset_y
                            w0 = ((x1 - sx) * (y2 - sy) - (x2 - sx) * (y1 - sy)) / area
                            w1 = ((x2 - sx) * (y0 - sy) - (x0 - sx) * (y2 - sy)) / area
                            w2 = 1.0 - w0 - w1
                            if w0 >= -1e-6 and w1 >= -1e-6 and w2 >= -1e-6:
                                touched.add(py * resolution + px)
                                break
                        else:
                            continue
                        break
        for index in touched:
            if 0 <= index < len(counts):
                counts[index] += 1
    return counts


def island_report(counts: Sequence[int], resolution: int, uv_area: float | None = None) -> dict[str, Any]:
    """Summarise atlas usage and island overlap.

    Islands that sit on top of each other consume UV area without consuming
    atlas area, so the summed triangle area runs ahead of the union the raster
    measures.  That difference is the overlap, and it is immune to the shared
    edges and shared vertices every connected mesh has.
    """
    total = max(1, len(counts))
    covered = sum(1 for value in counts if value > 0)
    usage = covered / total
    area = float(uv_area) if uv_area is not None else 0.0
    overlap_ratio = max(0.0, (area - usage) / area) if area > 1e-9 else 0.0
    return {
        "resolution": int(resolution),
        "covered_pixels": covered,
        "uv_area": area,
        "overlap_area": max(0.0, area - usage),
        "overlap_ratio": overlap_ratio,
        "atlas_usage_ratio": usage,
        "islands_do_not_overlap": covered > 0 and overlap_ratio <= MAX_ISLAND_OVERLAP_RATIO,
        "atlas_usage_sufficient": usage >= MIN_ATLAS_USAGE_RATIO,
    }


def _sample_points(triangle: Triangle) -> list[tuple[float, float]]:
    (u0, v0), (u1, v1), (u2, v2) = ((float(p[0]), float(p[1])) for p in triangle)
    centroid = ((u0 + u1 + u2) / 3.0, (v0 + v1 + v2) / 3.0)
    points = [centroid]
    # Pull each corner a little towards the centroid so a sample never lands on
    # the island edge, where the bake margin rather than the surface is stored.
    for u, v in ((u0, v0), (u1, v1), (u2, v2)):
        points.append((u + (centroid[0] - u) * 0.35, v + (centroid[1] - v) * 0.35))
    return points


def triangle_coverage_report(
    triangles: Sequence[Triangle],
    painted: Sequence[int],
    resolution: int,
) -> dict[str, Any]:
    """Prove every mesh triangle samples baked pixels rather than empty atlas."""
    resolution = max(8, int(resolution))
    total = len(triangles)
    uncovered = 0
    for triangle in triangles:
        hit = False
        for u, v in _sample_points(triangle):
            if not (_finite(u) and _finite(v)):
                continue
            px = int(min(1.0, max(0.0, u)) * (resolution - 1))
            py = int(min(1.0, max(0.0, v)) * (resolution - 1))
            index = py * resolution + px
            if 0 <= index < len(painted) and painted[index]:
                hit = True
                break
        if not hit:
            uncovered += 1
    covered = total - uncovered
    ratio = covered / total if total else 0.0
    return {
        "triangles": total,
        "covered_triangles": covered,
        "uncovered_triangles": uncovered,
        "coverage_ratio": ratio,
        "every_triangle_has_bake_coverage": total > 0 and ratio >= MIN_TRIANGLE_COVERAGE_RATIO,
    }


def colour_report(pixels: Sequence[float], painted: Sequence[int]) -> dict[str, Any]:
    """Prove the bake holds real colour rather than one flat fill."""
    samples = 0
    total_luma = 0.0
    total_luma_sq = 0.0
    distinct: set[tuple[int, int, int]] = set()
    for index, is_painted in enumerate(painted):
        if not is_painted:
            continue
        base = index * 4
        if base + 2 >= len(pixels):
            continue
        r, g, b = float(pixels[base]), float(pixels[base + 1]), float(pixels[base + 2])
        if not (_finite(r) and _finite(g) and _finite(b)):
            continue
        luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
        samples += 1
        total_luma += luma
        total_luma_sq += luma * luma
        if len(distinct) < 4096:
            distinct.add((int(r * 31), int(g * 31), int(b * 31)))
    if samples == 0:
        return {
            "painted_samples": 0,
            "mean_luminance": 0.0,
            "standard_deviation": 0.0,
            "distinct_colours": 0,
            "has_colour_variation": False,
        }
    mean = total_luma / samples
    variance = max(0.0, total_luma_sq / samples - mean * mean)
    deviation = math.sqrt(variance)
    return {
        "painted_samples": samples,
        "mean_luminance": mean,
        "standard_deviation": deviation,
        "distinct_colours": len(distinct),
        "has_colour_variation": deviation >= MIN_COLOUR_STANDARD_DEVIATION and len(distinct) >= MIN_DISTINCT_COLOURS,
    }


def unpainted_region_report(
    counts: Sequence[int],
    painted: Sequence[int],
    resolution: int,
) -> dict[str, Any]:
    """Locate holes: atlas pixels an island claims that the bake never painted."""
    resolution = max(8, int(resolution))
    holes = [1 if counts[i] > 0 and not painted[i] else 0 for i in range(min(len(counts), len(painted)))]
    island_pixels = sum(1 for value in counts if value > 0)
    hole_pixels = sum(holes)
    largest = 0
    seen = bytearray(len(holes))
    for start in range(len(holes)):
        if not holes[start] or seen[start]:
            continue
        size = 0
        queue = deque([start])
        seen[start] = 1
        while queue:
            index = queue.popleft()
            size += 1
            y, x = divmod(index, resolution)
            for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if 0 <= ny < resolution and 0 <= nx < resolution:
                    neighbour = ny * resolution + nx
                    if neighbour < len(holes) and holes[neighbour] and not seen[neighbour]:
                        seen[neighbour] = 1
                        queue.append(neighbour)
        largest = max(largest, size)
    hole_ratio = hole_pixels / island_pixels if island_pixels else 1.0
    largest_ratio = largest / island_pixels if island_pixels else 1.0
    return {
        "island_pixels": island_pixels,
        "unpainted_island_pixels": hole_pixels,
        "unpainted_ratio": hole_ratio,
        "largest_unpainted_pixels": largest,
        "largest_unpainted_ratio": largest_ratio,
        "no_large_unpainted_regions": island_pixels > 0
        and hole_ratio <= MAX_UNPAINTED_ISLAND_RATIO
        and largest_ratio <= MAX_LARGEST_UNPAINTED_RATIO,
    }


def evaluate_bake(
    uv_bounds: dict[str, Any],
    islands: dict[str, Any],
    coverage: dict[str, Any],
    colour: dict[str, Any],
    unpainted: dict[str, Any],
    material_rendered: bool,
) -> dict[str, Any]:
    """Combine every rule into the single verdict the pipeline enforces."""
    checks = {
        "uv_coordinates_finite": bool(uv_bounds.get("all_finite")),
        "uv_inside_atlas": bool(uv_bounds.get("inside_atlas")),
        "islands_do_not_overlap": bool(islands.get("islands_do_not_overlap")),
        "atlas_usage_sufficient": bool(islands.get("atlas_usage_sufficient")),
        "every_triangle_has_bake_coverage": bool(coverage.get("every_triangle_has_bake_coverage")),
        "bake_has_colour_variation": bool(colour.get("has_colour_variation")),
        "no_large_unpainted_regions": bool(unpainted.get("no_large_unpainted_regions")),
        "baked_material_rendered_in_blender": bool(material_rendered),
    }
    failures = [name for name, value in checks.items() if not value]
    return {
        "passed": not failures,
        "failures": failures,
        "checks": checks,
        "uv_bounds": uv_bounds,
        "islands": islands,
        "coverage": coverage,
        "colour": colour,
        "unpainted": unpainted,
    }


def painted_mask_from_pixels(pixels: Sequence[float], sentinel: Sequence[float], tolerance: float = 0.02) -> list[int]:
    """Mark every texel the bake actually wrote.

    The bake target is cleared to a sentinel colour first, so any texel that no
    longer matches the sentinel carries real baked surface colour.
    """
    total = len(pixels) // 4
    mask = [0] * total
    sr, sg, sb, sa = (float(sentinel[i]) for i in range(4))
    # This runs once per bake attempt over every texel of a 1024 square atlas, so
    # the loop body stays flat rather than readable.
    for index in range(total):
        base = index * 4
        r = pixels[base]
        g = pixels[base + 1]
        b = pixels[base + 2]
        a = pixels[base + 3]
        if r != r or g != g or b != b or a != a:  # NaN never equals itself
            continue
        if (
            abs(r - sr) <= tolerance
            and abs(g - sg) <= tolerance
            and abs(b - sb) <= tolerance
            and abs(a - sa) <= tolerance
        ):
            continue
        mask[index] = 1
    return mask


def downsample_mask(mask: Sequence[int], source_resolution: int, target_resolution: int) -> list[int]:
    """Reduce a full size texel mask to the raster resolution used for islands."""
    source_resolution = max(1, int(source_resolution))
    target_resolution = max(1, int(target_resolution))
    if source_resolution == target_resolution:
        return list(mask)
    out = [0] * (target_resolution * target_resolution)
    scale = source_resolution / target_resolution
    for ty in range(target_resolution):
        sy0, sy1 = int(ty * scale), max(int(ty * scale) + 1, int((ty + 1) * scale))
        for tx in range(target_resolution):
            sx0, sx1 = int(tx * scale), max(int(tx * scale) + 1, int((tx + 1) * scale))
            hit = 0
            for sy in range(sy0, min(sy1, source_resolution)):
                row = sy * source_resolution
                for sx in range(sx0, min(sx1, source_resolution)):
                    if mask[row + sx]:
                        hit = 1
                        break
                if hit:
                    break
            out[ty * target_resolution + tx] = hit
    return out
