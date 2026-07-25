# Changelog

## 2.2.0, 25 July 2026

* Keeps a full resolution textured copy of the imported GLB before any reduction.
* Generates a completely new non overlapping UV atlas on the reduced mesh and discards the imported UV layers.
* Bakes the visible colour from the untouched high resolution model onto the reduced mesh with Cycles selected to active, using the diffuse colour pass with direct and indirect light disabled.
* Escalates cage extrusion and ray distance automatically when a bake attempt leaves triangles uncovered.
* Transfers source transparency with a second projected pass when the source materials use it.
* Validates the result before StudioMDL: finite UVs, UVs inside the atlas, non overlapping islands, sufficient atlas usage, bake coverage on every triangle, real colour variation and no large unpainted regions.
* Renders the baked material in Blender from the front and the back and shows both in the Builder Build page.
* Fails the build with the failing rule names rather than compiling a scrambled texture.
* Splits LOD meshes along their UV seams before reduction so distance LODs keep sampling the correct atlas region.
* Replaces the magenta bake sentinel with the mean baked colour so it can never reach the exported TGA or trigger a false translucent material.
* Fixes the Builder validation row that marked every non empty array red. Produced artefact lists such as `compiled_files` are now green when populated; problem lists such as `missing_vtf_references` remain red when populated.

## 2.1.1, 25 July 2026

* Replaces the single maximum edge ratio conformance gate with robust edge diagnostics.
* Evaluates guide to stock conformance before changing any vertex.
* Preserves the original mesh when the guide is already stock compatible and the candidate warp contains localized edge discontinuities.
* Adds a deterministic no warp exact stock bind mode for normal T pose and A pose humanoids.
* Applies the same robust edge diagnostics to the posed deformation probe.
* Adds regression coverage for the Jack Hegarty 70.886 edge ratio failure and a genuine widespread exploding mesh case.
* Preserves every existing workspace file when applied as a hotfix.

## 2.1.0, 25 July 2026

* Replaced the old generated skeleton basis with exact stock SMD bind transforms.
* Removed evaluated armature mesh export and automatic bone heat weighting.
* Added deterministic anatomical region weighting with three normalized influences.
* Added automatic verified installation, dedicated client player selector registration and runtime validation.
* Added safe base only Source materials.

## 2.0.4, 25 July 2026

* Replaced the hanging VTEX path with a validated internal VTF 7.2 writer.
