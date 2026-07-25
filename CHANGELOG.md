# Changelog

## 2.3.0, 25 July 2026

* Prop projects. Step 1 now offers an asset type: a Character runs the full guided rig pipeline, a Prop turns any scanned object into a spawnable, physically simulated Garry's Mod prop. Props skip the landmark guide entirely; the same reduction, atlas rebuild, bake and validation pipeline runs, then the model compiles as `$staticprop` with a convex hull collision model into `models/props/<id>`. The spawn menu gets a category with the model, and the runtime check verifies the model, materials, physics file and registration in game.
* NPC variants. A character project can now generate NextBot NPCs alongside the player model: Friendly, Hostile with melee, and Hostile armed with a pistol, SMG or shotgun. Armed variants bonemerge the stock HL2 world weapon into the right hand, chase the nearest player, and fire real bullets; hostile melee closes in and swings; the friendly variant wanders. All appear in the spawn menu and the Entities tab, die into ragdolls, and their registration is part of the runtime check. The option is on the Import form and the Build Settings card, so it can be turned on for an existing character without remarking anything.
* Fixed the settings save reporting Unknown API route. The Builder posted the new build settings while the service only answered PUT for that route.
* Fixed the Bailey 2 conformance rejection. The bind warp was 99.6% clean but 131 of 35,998 sampled edges stretched severely at anatomical region boundaries, and the whole build was refused even though every displacement and extent gate was green. When the only failure is a rare, localized set of edge outliers, the offending vertices now take the average displacement of their neighbours and the diagnostics run again on the repaired field; the build proceeds only if the repaired warp passes cleanly. A widespread explosion exceeds the repair's outlier budget and still fails.
* Planned next (not in this release): animation retargeting from community SMD libraries, multi-project batch export, atlas and weight debugging views, VTF compression options. See ROADMAP.md.

## 2.2.6, 25 July 2026

* Build settings can now change after a project is created. A Build Settings card on the Build page edits quality, texture size and Source height; the locked guide is preserved because every build rescales it to the chosen height. Identity fields such as the slug stay fixed.
* Fixed the unpainted region gate rejecting dense meshes. A real 48,000 triangle build was stopped with 3.773% of island area unpainted while the largest connected hole was 0.032%: thousands of pinprick texels from measuring the raw bake mask at raster resolution, not a bake failure. Only the largest connected hole decides the verdict now; the pinpricks are filled by the flood pass and the shipped atlas coverage is measured separately at full resolution. A genuine bake void is a large connected hole and still fails.

## 2.2.5, 25 July 2026

* Fixed the vertical texture orientation of every exported SMD. The exporter wrote `1.0 - v` for each vertex, but the SMD text format stores V in the same bottom origin convention Blender uses and StudioMDL performs the DirectX flip itself. The game therefore sampled the atlas vertically mirrored in every release since 2.0 while the Blender proof renders looked correct. This was the final difference between the proof and the game: the 2.2.4 build showed coherent patches of valid colour in mirrored atlas positions, with the flood filled gap colours in between.

## 2.2.4, 25 July 2026

The 2.2.3 build compiled, installed and passed every check, and the Blender proof renders were correct, but in game the model dissolved into pink and beige noise at distance and the left knee bent backwards while walking.

* Fixed the left knee. The generated IK chains gave the left foot a mirrored knee direction hint, but ValveBiped leg bones are not axis mirrored: Valve's own player QCs use the same hint for both feet. The left chain told the solver to bend the knee backwards whenever walking foot IK engaged. Both feet now use Valve's exact values.
* The VTF now ships a full mip chain instead of a single NOMIP level. Blender pre-filters textures automatically, which is why the proofs looked clean while the game, sampling a 1024 atlas of thousands of small UV islands with no mips, picked essentially arbitrary texels at distance.
* Island colours are flood filled across the entire gap area of the atlas. Over half the atlas is gap; it was previously filled with the model's average colour, which for a skin heavy character is pink, and distant sampling reached it. Every gap texel now carries the colour of its nearest island.
* The bake sentinel is a neutral grey with zero alpha rather than magenta, so nothing loud can ever appear even if a texel escapes.
* Removed the decimated distance LODs. Decimating the rebuilt atlas either merges island loops or cracks the split seams, and StudioMDL measured LOD1 diverging by 11,674 vertices. The 32k reference is used at every distance and always matches the proof render.

## 2.2.3, 25 July 2026

* Rescales the locked guide to the mesh height before anything reads it. The Jack Hegarty guide was locked at 72 inches while the project height was later 64, so every landmark sat 12.5% off the body and the skeleton conformance stage rejected the build with an average displacement of 5.1 units. The guide's own vertical extent identifies the scale it was locked at, and landmarks and rigid zones are scaled uniformly about the ground point.
* Differences within 4% are left alone, so hair or a hat above the head landmark never triggers a rescale, and a factor outside 0.25 to 4.0 is refused as corruption rather than hidden.
* The rescale is recorded in the build report as guide_rescale.

## 2.2.2, 25 July 2026

* Judges atlas adequacy by surface area instead of triangle count. The target character had 3,227 of 32,000 triangles below one texel, 10.08% by count but 0.685% of the surface, and the count based rule stopped a build whose measurable coverage was 99.979%. A decimated mesh always has a long tail of small triangles; what matters is how much of the surface cannot own a texel.
* Separates `atlas_resolution_adequate` from `every_triangle_has_bake_coverage`. Folding one into the other made the build report a rule that had in fact passed.
* Every validation failure now carries the measurement that caused it, rather than only the rule name.
* Writes a build report when the texture gate rejects a build, so the Builder can show the numbers and the proof renders that were already produced.
* The Baked Texture Proof card now loads the renders whether the bake passed or was rejected, which is when they are most useful.

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
