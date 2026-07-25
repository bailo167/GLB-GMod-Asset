"""Prove the v2.2 UV atlas and texture bake rules without Blender."""

from blender.bake_validation import (
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

SENTINEL = (1.0, 0.0, 1.0, 0.0)


def square(x0, y0, size):
    return [
        ((x0, y0), (x0 + size, y0), (x0 + size, y0 + size)),
        ((x0, y0), (x0 + size, y0 + size), (x0, y0 + size)),
    ]


def test_uv_bounds_rejects_non_finite_coordinates():
    report = uv_bounds_report([(0.1, 0.2), (float("nan"), 0.5)])
    assert report["non_finite"] == 1
    assert report["all_finite"] is False
    assert report["inside_atlas"] is False


def test_uv_bounds_rejects_coordinates_outside_the_atlas():
    inside = uv_bounds_report([(0.0, 0.0), (1.0, 1.0), (0.5, 0.5)])
    assert inside["all_finite"] and inside["inside_atlas"]
    # The damaged v2.1.1 atlas produced coordinates far outside 0 to 1 after the
    # decimate pass collapsed seam vertices.
    outside = uv_bounds_report([(0.5, 0.5), (4.2, -3.1)])
    assert outside["outside"] == 1
    assert outside["inside_atlas"] is False


def summarise(triangles, resolution):
    return island_report(rasterize_triangles(triangles, resolution), resolution, total_uv_area(triangles))


def test_non_overlapping_islands_pass_and_stacked_islands_fail():
    resolution = 64
    separate = square(0.05, 0.05, 0.4) + square(0.55, 0.55, 0.4)
    clean = summarise(separate, resolution)
    assert clean["overlap_ratio"] == 0.0
    assert clean["islands_do_not_overlap"] is True

    stacked = square(0.05, 0.05, 0.4) + square(0.05, 0.05, 0.4)
    broken = summarise(stacked, resolution)
    assert broken["overlap_ratio"] > 0.4
    assert broken["islands_do_not_overlap"] is False


def test_shared_edges_between_neighbouring_triangles_are_not_overlap():
    # Every connected mesh shares edges and vertices between triangles. Counting
    # those texels twice made v2.1.1 style pixel counting useless, so a fan of
    # touching triangles must report no overlap at all.
    resolution = 128
    fan = []
    for index in range(8):
        x = 0.05 + index * 0.11
        fan.extend(square(x, 0.05, 0.11))
    report = summarise(fan, resolution)
    assert report["overlap_ratio"] == 0.0
    assert report["islands_do_not_overlap"] is True


def test_atlas_usage_is_reported_and_a_pinhole_atlas_is_rejected():
    resolution = 64
    pinhole = summarise(square(0.0, 0.0, 0.02), resolution)
    assert pinhole["atlas_usage_sufficient"] is False
    healthy = summarise(square(0.02, 0.02, 0.9), resolution)
    assert healthy["atlas_usage_sufficient"] is True


def test_total_uv_area_ignores_non_finite_triangles():
    assert total_uv_area(square(0.0, 0.0, 1.0)) == 1.0
    assert total_uv_area([((0.0, 0.0), (float("inf"), 0.0), (0.0, 1.0))]) == 0.0


def test_painted_mask_only_marks_texels_the_bake_wrote():
    pixels = list(SENTINEL) + [0.4, 0.3, 0.2, 1.0] + list(SENTINEL) + [0.9, 0.8, 0.7, 1.0]
    mask = painted_mask_from_pixels(pixels, SENTINEL)
    assert mask == [0, 1, 0, 1]


def test_triangle_coverage_detects_triangles_the_bake_never_reached():
    resolution = 32
    triangles = square(0.05, 0.05, 0.4) + square(0.55, 0.55, 0.4)
    painted = [0] * (resolution * resolution)
    for y in range(resolution):
        for x in range(resolution):
            if x < resolution // 2 and y < resolution // 2:
                painted[y * resolution + x] = 1
    report = triangle_coverage_report(triangles, painted, resolution)
    assert report["measurable_triangles"] == 4
    assert report["covered_triangles"] == 2
    assert report["uncovered_measurable_triangles"] == 2
    assert report["every_triangle_has_bake_coverage"] is False

    everything = [1] * (resolution * resolution)
    full = triangle_coverage_report(triangles, everything, resolution)
    assert full["coverage_ratio"] == 1.0
    assert full["every_triangle_has_bake_coverage"] is True


def test_sub_texel_triangles_are_measured_against_neighbouring_texels():
    # A triangle smaller than one texel is never rasterized by the baker, so no
    # ray is cast for it and it can never hit a painted texel directly. This is
    # what stalled the first real 32k triangle build at 99.109% on every attempt.
    resolution = 64
    sliver = 0.4 / resolution  # well under one texel of UV area
    triangles = [((0.5, 0.5), (0.5 + sliver, 0.5), (0.5 + sliver, 0.5 + sliver))]
    painted = [0] * (resolution * resolution)

    isolated = triangle_coverage_report(triangles, painted, resolution)
    assert isolated["sub_texel_triangles"] == 1
    assert isolated["measurable_triangles"] == 0
    assert isolated["uncovered_sub_texel_triangles"] == 1

    # After the bake margin and the atlas dilation, the texels beside it carry
    # colour, which is exactly what the triangle samples when rendered.
    centre = int(0.5 * (resolution - 1))
    painted[(centre + 1) * resolution + centre] = 1
    beside = triangle_coverage_report(triangles, painted, resolution, neighbourhood=1)
    assert beside["sub_texel_triangles"] == 1
    assert beside["uncovered_sub_texel_triangles"] == 0
    assert beside["covered_triangles"] == 1

    # With no tolerance, which is how the retry decision reads the raw bake, it
    # is still uncovered.
    assert triangle_coverage_report(triangles, painted, resolution, neighbourhood=0)["covered_triangles"] == 0


def test_a_few_sub_texel_slivers_do_not_fail_an_otherwise_covered_mesh():
    # The real build reported 285 of 32,000 triangles uncovered, unchanged across
    # three escalating projection envelopes, because they were sub texel.
    resolution = 64
    triangles = []
    for index in range(10):
        triangles.extend(square(0.02 + index * 0.09, 0.05, 0.08))
    sliver = 0.4 / resolution
    triangles.append(((0.5, 0.7), (0.5 + sliver, 0.7), (0.5 + sliver, 0.7 + sliver)))
    painted = [1] * (resolution * resolution)
    report = triangle_coverage_report(triangles, painted, resolution)
    assert report["sub_texel_triangles"] == 1
    assert report["measurable_coverage_ratio"] == 1.0
    assert report["every_triangle_has_bake_coverage"] is True
    assert report["atlas_resolution_adequate"] is True


def test_sub_texel_adequacy_is_judged_by_surface_area_not_triangle_count():
    # The real build had 3227 of 32000 triangles below one texel, 10.08% by
    # count, but well under 1% of the surface. Counting them stopped a build
    # whose measurable coverage was 99.979%.
    resolution = 64
    triangles = []
    for index in range(9):
        triangles.extend(square(0.02 + index * 0.1, 0.05, 0.09))
    sliver = 0.3 / resolution
    for index in range(30):
        base = 0.02 + index * 0.03
        triangles.append(((base, 0.8), (base + sliver, 0.8), (base + sliver, 0.8 + sliver)))
    painted = [1] * (resolution * resolution)
    report = triangle_coverage_report(triangles, painted, resolution)
    assert report["sub_texel_ratio"] > 0.10          # a sixth of the triangles
    assert report["sub_texel_area_ratio"] < 0.01     # but a rounding error of the surface
    assert report["atlas_resolution_adequate"] is True
    assert report["every_triangle_has_bake_coverage"] is True


def test_an_atlas_genuinely_too_small_for_the_mesh_is_rejected():
    resolution = 16
    sliver = 0.4 / resolution
    triangles = [
        ((0.05 + (index % 8) * 0.11, 0.05 + (index // 8) * 0.11),
         (0.05 + (index % 8) * 0.11 + sliver, 0.05 + (index // 8) * 0.11),
         (0.05 + (index % 8) * 0.11 + sliver, 0.05 + (index // 8) * 0.11 + sliver))
        for index in range(40)
    ] + square(0.5, 0.5, 0.05)
    painted = [1] * (resolution * resolution)
    report = triangle_coverage_report(triangles, painted, resolution)
    assert report["sub_texel_area_ratio"] > 0.25
    assert report["atlas_resolution_adequate"] is False


def test_a_real_bake_miss_still_fails_even_with_neighbourhood_tolerance():
    resolution = 32
    triangles = square(0.55, 0.55, 0.4)
    painted = [0] * (resolution * resolution)
    for index in range(resolution * 4):
        painted[index] = 1
    report = triangle_coverage_report(triangles, painted, resolution, neighbourhood=2)
    assert report["measurable_triangles"] == 2
    assert report["uncovered_measurable_triangles"] == 2
    assert report["every_triangle_has_bake_coverage"] is False


def test_colour_report_rejects_a_flat_fill_and_accepts_real_variation():
    flat_pixels: list[float] = []
    for _ in range(64):
        flat_pixels.extend([0.5, 0.5, 0.5, 1.0])
    flat = colour_report(flat_pixels, [1] * 64)
    assert flat["standard_deviation"] == 0.0
    assert flat["has_colour_variation"] is False

    varied_pixels: list[float] = []
    for index in range(64):
        value = index / 63.0
        varied_pixels.extend([value, 1.0 - value, (index % 8) / 7.0, 1.0])
    varied = colour_report(varied_pixels, [1] * 64)
    assert varied["distinct_colours"] >= 8
    assert varied["has_colour_variation"] is True


def test_unpainted_region_report_finds_holes_inside_the_islands():
    resolution = 32
    counts = rasterize_triangles(square(0.05, 0.05, 0.9), resolution)
    painted = [1 if value else 0 for value in counts]
    clean = unpainted_region_report(counts, painted, resolution)
    assert clean["unpainted_island_pixels"] == 0
    assert clean["no_large_unpainted_regions"] is True

    holed = list(painted)
    for y in range(8, 20):
        for x in range(8, 20):
            holed[y * resolution + x] = 0
    broken = unpainted_region_report(counts, holed, resolution)
    assert broken["largest_unpainted_pixels"] >= 100
    assert broken["no_large_unpainted_regions"] is False


def test_downsample_mask_keeps_painted_blocks():
    source = [0] * 16
    source[0] = 1
    reduced = downsample_mask(source, 4, 2)
    assert reduced == [1, 0, 0, 0]
    assert downsample_mask(source, 4, 4) == source


def test_evaluate_bake_lists_every_failing_rule():
    passing = evaluate_bake(
        {"all_finite": True, "inside_atlas": True},
        {"islands_do_not_overlap": True, "atlas_usage_sufficient": True},
        {"every_triangle_has_bake_coverage": True, "atlas_resolution_adequate": True},
        {"has_colour_variation": True},
        {"no_large_unpainted_regions": True},
        True,
    )
    assert passing["passed"] is True
    assert passing["failures"] == []
    assert passing["failure_detail"] == []

    failing = evaluate_bake(
        {"all_finite": True, "inside_atlas": False},
        {"islands_do_not_overlap": False, "atlas_usage_sufficient": True},
        {"every_triangle_has_bake_coverage": True, "atlas_resolution_adequate": True},
        {"has_colour_variation": False},
        {"no_large_unpainted_regions": True},
        False,
    )
    assert failing["passed"] is False
    assert set(failing["failures"]) == {
        "uv_inside_atlas",
        "islands_do_not_overlap",
        "bake_has_colour_variation",
        "baked_material_rendered_in_blender",
    }


def test_a_small_atlas_does_not_report_the_coverage_rule_as_the_failure():
    # The 2.2.1 build failed with ["every_triangle_has_bake_coverage"] when the
    # coverage rule had in fact passed at 99.979%. The two rules are separate.
    verdict = evaluate_bake(
        {"all_finite": True, "inside_atlas": True},
        {"islands_do_not_overlap": True, "atlas_usage_sufficient": True},
        {
            "every_triangle_has_bake_coverage": True,
            "atlas_resolution_adequate": False,
            "sub_texel_area_ratio": 0.4,
            "sub_texel_triangles": 3227,
        },
        {"has_colour_variation": True},
        {"no_large_unpainted_regions": True},
        True,
    )
    assert verdict["failures"] == ["atlas_resolution_adequate"]
    assert "40.000%" in verdict["failure_detail"][0]
    assert "3227" in verdict["failure_detail"][0]


def test_failure_detail_carries_the_measurement_that_tripped_each_rule():
    verdict = evaluate_bake(
        {"all_finite": True, "inside_atlas": True, "count": 96000},
        {"islands_do_not_overlap": True, "atlas_usage_sufficient": True},
        {
            "every_triangle_has_bake_coverage": False,
            "atlas_resolution_adequate": True,
            "measurable_triangles": 28773,
            "uncovered_measurable_triangles": 900,
            "measurable_coverage_ratio": 0.9687,
        },
        {"has_colour_variation": True},
        {"no_large_unpainted_regions": True},
        True,
    )
    detail = verdict["failure_detail"][0]
    assert "900 of 28773" in detail
    assert "96.870%" in detail


def test_scattered_pinholes_pass_while_a_real_void_still_fails():
    # The Bailey 1 build was rejected with 3.773% of island area unpainted while
    # the largest connected hole was 0.032%: thousands of border pinpricks from
    # measuring the raw mask at raster resolution, not a bake failure. Only the
    # largest connected hole decides the verdict now.
    resolution = 64
    counts = rasterize_triangles(square(0.02, 0.02, 0.95), resolution)
    painted = [1 if value else 0 for value in counts]
    island = [index for index, value in enumerate(counts) if value]
    # Punch isolated single-texel pinholes across ~4% of the island.
    for position, index in enumerate(island):
        if position % 25 == 0:
            painted[index] = 0
    pinpricked = unpainted_region_report(counts, painted, resolution)
    assert pinpricked["unpainted_ratio"] > 0.02
    assert pinpricked["largest_unpainted_ratio"] < 0.01
    assert pinpricked["no_large_unpainted_regions"] is True

    # A genuine void is a large connected hole and still stops the build.
    voided = [1 if value else 0 for value in counts]
    for y in range(10, 30):
        for x in range(10, 30):
            voided[y * resolution + x] = 0
    real = unpainted_region_report(counts, voided, resolution)
    assert real["largest_unpainted_ratio"] > 0.01
    assert real["no_large_unpainted_regions"] is False
