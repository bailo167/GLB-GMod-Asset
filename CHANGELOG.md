# Changelog

## 2.2.1, 25 July 2026

* Fixes the texture bake gate stalling at 99.109% coverage on the first real 32,000 triangle build. The missed triangles were smaller than one texel of UV area, so the baker rasterizes nothing for them and never casts a ray. Escalating the projection envelope could not change the result, which is why all three attempts reported the identical figure.
* Coverage is now measured per population. A triangle with at least one texel of UV footprint must hit painted texels directly, and failing that still stops the build. A triangle below one texel is measured against the texels beside it, which is what it samples when rendered.
* Reports `sub_texel_triangles` and fails a build where more than one triangle in ten is sub texel, because that means the chosen texture size is too small for the mesh.
* Dilates the baked atlas outward from every painted island before export, instead of flattening the gaps to one average colour. This fills isolated slivers with the colour of the surface beside them and protects against bilinear bleed in Source.
* The coverage verdict now measures the atlas that actually ships, after the bake margin and the dilation. The retry decision still reads the raw bake with no tolerance.
* Stops the bake retries as soon as a larger projection envelope recovers no further triangles.
* Preserves the alpha the bake produced. The previous flatten pass forced every texel opaque, which discarded the transparency transfer.
* Stops reading the deprecated `Material.use_nodes`, which warns on Blender 5.x and is removed in 6.0.

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
