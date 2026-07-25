# Ember Guided GMod Character Builder 2.2.2

A local Windows workbench for turning a humanoid GLB into a guided Garry's Mod player model and spawnable ragdoll.

The browser talks only to the local Python service at `127.0.0.1`. Imported GLBs and project state remain inside the local `workspace` directory.

## Repository layout

| Path | Contents |
| --- | --- |
| `app.py` | The local HTTP service and every API route |
| `ember_gmod/` | Build jobs, project storage, toolchain detection, VTF writer, installer, runtime Lua |
| `blender/` | The Blender pipeline, the UV atlas and bake validation rules, the SMD skeleton, the TGA writer |
| `web/` | The browser workbench: guide viewer, build page, toolchain page |
| `tests/` | Development test suite, run with pytest |
| `self_test.py` | Standard library only self test, run on the user's Windows machine by `verify_v2_guided.bat` |
| `tools/build_release.py` | Packages the Windows release zip |
| `dist/` | Released Windows packages |

### Working on it

```bash
python -m pytest tests      # development suite
python self_test.py         # what the shipped verifier runs
python tools/build_release.py
```

`tools/build_release.py` writes `dist/ember-guided-gmod-character-builder-<version>-windows.zip`
from `ember_gmod/__init__.py.__version__`. The archive is deterministic, so an unchanged
tree rebuilds byte for byte.

## Required software

1. Windows 10 or Windows 11, 64 bit
2. Python 3.10 or newer
3. Blender 4.2 or newer
4. Garry's Mod installed through Steam

## Current workflow

1. Run `verify_v2_guided.bat`.
2. Run `start.bat`.
3. Confirm the Toolchain paths.
4. Import a humanoid GLB.
5. Orient the model and lock the landmark guide.
6. Select **Build + Install**.
7. Restart Garry's Mod completely.
8. Enter Sandbox.
9. Select **Read GMod Runtime Check**.

## 2.2.0 correction: texture rebaking

The 2.1.1 Jack Hegarty build produced a correct skeleton, working animations, a working
player selector and a working ragdoll, but the surface colour was scrambled. Triangular
fragments of skin appeared on clothing, the shirt texture appeared across trousers and
skin, and dark areas landed on unrelated faces.

That is not brightness, gamma, VMT or lighting. It is a damaged UV map. The pipeline did
this:

```text
Original GLB, 499,994 triangles
  -> Blender Decimate to 32,000 triangles
  -> reuse the original UV coordinates and the original texture
```

Collapse decimation merges vertices without regard for UV islands. Every merged vertex
that sat on a UV seam fuses loops belonging to different regions of the atlas, so the
surviving triangles sample the wrong part of the texture. At a 16:1 reduction that happens
thousands of times.

Version 2.2.0 stops reusing the imported mapping:

```text
Keep a full resolution textured copy of the imported GLB
  -> create the reduced Garry's Mod mesh
  -> generate a completely new non overlapping UV atlas on the reduced mesh
  -> bake the visible colour from the untouched high resolution model onto it
     (Cycles selected to active, diffuse colour pass, no direct or indirect light)
  -> export that newly baked texture and its matching UV coordinates
```

The bake projects from the low resolution target onto the detailed source using cage
extrusion and a ray distance limit. If any triangle is missed, the build automatically
retries with a larger projection envelope before it gives up.

### What the build now proves before StudioMDL runs

| Check | Rule |
| --- | --- |
| `uv_coordinates_finite` | No NaN or infinite UV values |
| `uv_inside_atlas` | Every island sits inside 0 to 1 |
| `atlas_islands_do_not_overlap` | Summed UV area matches the atlas area the islands actually occupy |
| `atlas_usage_sufficient` | The atlas is not collapsed into a pinhole |
| `every_triangle_has_bake_coverage` | Every mesh triangle samples baked texels, not empty atlas |
| `atlas_resolution_adequate` | No more than a quarter of the surface area is in triangles smaller than a texel |
| `bake_has_colour_variation` | The bake holds real colour, not one flat fill |
| `no_large_unpainted_regions` | No hole inside the islands |
| `baked_material_rendered_in_blender` | Front and back renders of the baked material are produced |

Any failure stops the build with the failing rule names. The rules live in
`blender/bake_validation.py` with no Blender imports, so the desktop test suite proves them
directly.

The two proof renders are written to `generated/<slug>_texture_proof_front.png` and
`..._back.png`, and the Build page shows them under **Baked Texture Proof**.

### Also in 2.2.0

* LOD meshes are split along their UV seams before reduction, so distance LODs cannot
  suffer the same collapse damage.
* Transparency is transferred with a second projected pass when the source material uses
  it, so cut out hair and clothing do not become solid blocks.
* Fixed the Builder interface bug that painted the `compiled files` row red. The old rule
  treated every non empty array as a failure, so four correctly compiled model files looked
  like a failed check. Lists that name problems are red when populated; lists that name
  produced artefacts are red when empty.

### 2.2.1 follow up

The first real build stalled at 99.109% triangle coverage, identical on all three
projection attempts. That invariance was the diagnosis: 285 of 32,000 triangles had a UV
footprint smaller than one texel, so the baker rasterized nothing for them and never cast a
ray. No cage extrusion could have recovered them.

Coverage is now measured per population:

* **At least one texel of UV footprint** — must hit painted texels directly. A miss here is
  a real bake failure and still stops the build.
* **Below one texel** — measured against the texels beside it, because that is what the
  triangle samples when rendered. Counted and reported as `sub_texel_triangles`.

Atlas adequacy is judged by surface area, not by triangle count. The target character had
3,227 of 32,000 triangles below one texel, which is 10.08% by count but 0.685% of the
surface. A decimated mesh always has a long tail of small triangles, and counting them says
nothing about how the model looks. The build stops on `atlas_resolution_adequate` only when
more than a quarter of the surface cannot own a texel, which does mean the texture size is
too small.

Every failure names the measurement that caused it, and a rejected bake still writes its
report and proof renders so they can be inspected in the Builder.

The atlas is also dilated outward from every painted island before export rather than
having its gaps flattened to one average colour, so isolated slivers take the colour of the
surface beside them. That doubles as bleed protection against bilinear filtering in Source.

## 2.1.1 correction

Version 2.1.0 evaluated every mesh through a per bone guide to stock bind warp and blocked the build when one edge ratio exceeded a fixed limit. The Jack Hegarty run proved that logic was unsuitable for generated and decimated meshes: the complete character moved only 4.16 Source units at most and retained valid overall dimensions, while one microscopic edge produced a ratio of 70.886.

Version 2.1.1:

1. Evaluates the candidate bind warp without modifying the mesh.
2. Uses meaningful edge length, absolute edge change, percentiles and outlier prevalence rather than one raw ratio.
3. Preserves the original reference topology when the guide is already close to the exact stock bind and the candidate contains localized edge discontinuities.
4. Applies the warp only when its complete diagnostics pass.
5. Uses the same robust diagnostics in the posed deformation probe.
6. Still blocks genuine widespread collapse or explosion before StudioMDL.

For the reported Jack Hegarty measurements, the selected path is expected to be:

```text
original_mesh_exact_stock_bind
```

This means the original mesh is exported without the problematic per bone warp, while the exact stock SMD skeleton and deterministic three influence weights remain in use.

## Installation

A successful build installs the generated Lua, materials and model files directly into Garry's Mod and also creates a managed addon folder. Every direct file is SHA256 checked after copying.

The player model is registered in the stock player model list and through a dedicated client autorun for third party player model selectors.

## Completion rule

Compilation and installation are not treated as final proof. A project becomes complete only when the running game writes a passing runtime report for the current project and build token.

## Existing projects

Minor updates and hotfixes must preserve the entire `workspace` directory. Existing GLBs, landmarks and locked guides do not need to be recreated. Old generated output is deleted and rebuilt.

## Runtime boundary

The package can verify its Python code, project storage, generated contracts, texture writer and synthetic installation locally. Only the user's Windows Blender, StudioMDL and Garry's Mod session can prove the final target character in game.
