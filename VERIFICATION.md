# Ember Guided Character Builder 2.2.0 verification

Date: 25 July 2026

## Target failure

The 2.1.1 Jack Hegarty build succeeded everywhere except the surface colour. The reported
symptoms were:

```text
White shirt texture appears across skin and trousers
Skin colours appear as triangular fragments on clothing
Dark areas land on unrelated faces
The model remains recognisable, so geometry and rig are correct
```

Those are the signature of a damaged UV map, not a colour, gamma, VMT or lighting problem.
The build log confirms the cause: 499,994 triangles were reduced to roughly 32,000 and the
UV coordinates and texture from the original topology were then reused. Collapse
decimation merges vertices without regard for UV islands, so every merged seam vertex
fuses loops from different atlas regions and the surviving triangles sample the wrong part
of the texture.

## Corrective implementation

1. A full resolution textured copy of the imported GLB is taken before reduction.
2. The reduced mesh has every imported UV layer removed and a new atlas generated on its
   own topology, packed inside 0 to 1 with an island margin scaled to the texture size.
3. The colour is baked from the high resolution copy onto the reduced mesh with Cycles
   selected to active, diffuse pass, direct and indirect light disabled, using cage
   extrusion and a ray distance limit.
4. A missed bake escalates the projection envelope and retries, up to three attempts.
5. Transparency is transferred with a second projected emission pass when the source
   materials use it.
6. The bake target is cleared to a magenta sentinel first, so unwritten texels are exactly
   identifiable. After validation the sentinel is replaced with the mean baked colour at
   full alpha so it can never reach the exported TGA.
7. The baked material is rendered in Blender from the front and back before StudioMDL is
   launched, and both renders are served to the Build page.
8. The build stops with the failing rule names if any validation rule fails.
9. LOD meshes are split along their UV seams before reduction, so collapse cannot merge
   loops across islands at lower detail either.

## Validation rules

Every rule lives in `blender/bake_validation.py`, which imports no Blender modules and is
therefore executed directly by the test suite.

| Rule | Rejects |
| --- | --- |
| `uv_coordinates_finite` | NaN or infinite UV values |
| `uv_inside_atlas` | Islands outside 0 to 1 |
| `islands_do_not_overlap` | Stacked islands, measured as summed UV area against occupied atlas area |
| `atlas_usage_sufficient` | An atlas collapsed into a pinhole |
| `every_triangle_has_bake_coverage` | Triangles that sample empty atlas |
| `bake_has_colour_variation` | A flat single colour bake |
| `no_large_unpainted_regions` | Holes inside the islands |
| `baked_material_rendered_in_blender` | A bake that was never visually rendered |

Island overlap is measured by comparing the exact summed triangle UV area against the atlas
area the islands actually occupy. Counting texels touched by more than one triangle does
not work, because every connected mesh shares edges and vertices between neighbouring
triangles.

## Automated results

* Development tests: 65 passed
* Bundled standard library self test: 12 passed
* Python syntax checks: passed
* `web/app.js` ES module syntax check: passed
* Non overlapping atlas accepted, stacked atlas rejected: passed
* Shared edges between neighbouring triangles reported as no overlap: passed
* Non finite and out of atlas UV coordinates rejected: passed
* Uncovered triangles detected against a partially painted atlas: passed
* Flat fill rejected, real colour variation accepted: passed
* Interior unpainted region detected: passed
* Produced artefact lists green when populated, problem lists red when populated: passed
* Stub Blender harness executed the complete bake stage, including the retry escalation and
  the transparency pass: passed

## Runtime boundary

This environment has no Blender, no StudioMDL and no Garry's Mod, so the bake was exercised
against a stub Blender API rather than the real Cycles baker. The pure validation rules,
the interface fix and the stage ordering are proven here. The actual baked texture for the
target character must still be produced by the user's Windows Blender, and the build must
still reach `installed_unverified` before the game writes its runtime report.

The first thing to check after the next build is the **Baked Texture Proof** card on the
Build page. It shows the reduced mesh wearing the newly baked atlas, rendered in Blender,
before any Source tool touched it.
