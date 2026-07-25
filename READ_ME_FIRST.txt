EMBER GUIDED GMOD CHARACTER BUILDER 2.2.5

Close Garry's Mod and every old Builder window before updating.

Apply this 2.2.5 update to the existing V2 tool folder. The complete workspace, original GLB
and locked guide are preserved. Nothing needs to be marked again.

Restart the Builder and confirm Service v2.2.5. Open the project and select Build + Install.

WHAT CHANGED

2.1.1 reduced the mesh to 32,000 triangles and then reused the UV coordinates and texture
that belonged to the 500,000 triangle import. Those coordinates no longer matched the
reduced triangles, which is why skin appeared on clothing and the shirt appeared across
trousers.

2.2.5 keeps a full resolution textured copy of the import, builds a brand new UV atlas on
the reduced mesh, and bakes the colour from the high resolution copy onto it. The build now
stops if the atlas or the bake fails validation, instead of compiling a scrambled texture.

WHAT TO CHECK FIRST

After the build, look at the BAKED TEXTURE PROOF card on the Build page. It shows the front
and back of the reduced model wearing the newly baked texture, rendered inside Blender
before StudioMDL ran. If those renders look right, the texture placement is fixed. The same
images are saved in the project under generated as <slug>_texture_proof_front.png and
<slug>_texture_proof_back.png.

The bake stage adds roughly a minute to the build.

Fully restart Garry's Mod and enter Sandbox. Return to the Builder and select Read GMod
Runtime Check.

A project is never marked Complete until the current game session writes a passing runtime
report for the same project and build token.
