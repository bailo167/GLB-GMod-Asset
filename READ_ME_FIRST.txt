EMBER GUIDED GMOD CHARACTER BUILDER 2.3.0

Close Garry's Mod and every old Builder window before updating.

Apply this 2.3.0 update to the existing V2 tool folder. The complete workspace, original GLB
and locked guide are preserved. Nothing needs to be marked again.

Restart the Builder and confirm Service v2.3.0.

WHAT IS NEW

PROPS. Step 1 now asks whether the GLB is a Character or a Prop. A Prop is any scanned
object: it skips the landmark guide completely, runs the same mesh reduction, atlas rebuild
and texture bake, and compiles as a static prop with physics into models/props. It appears
in the spawn menu under its own category and can be spawned, pushed and thrown like any
Half-Life 2 prop.

NPC VARIANTS. A character project can now also generate NPCs: Friendly, Hostile, and
Hostile armed with a pistol, SMG or shotgun. They use the compiled player model, walk and
run with the standard animations, chase and attack players when hostile, and die into
ragdolls. Enable "Create NPC variants" on the Import form for a new project, or on the
Build Settings card for an existing one, then Build + Install again. They appear in the
spawn menu category and in the Entities tab.

CONFORMANCE REPAIR. A build that previously failed with "the stock skeleton conformance
stage rejected this mesh" because of a small number of severe edge outliers (the Bailey 2
failure) now repairs those vertices from their neighbours and revalidates instead of
refusing the build. A genuinely broken warp still fails.

Also fixed: saving Build Settings no longer reports Unknown API route.

WHAT TO CHECK FIRST

After a character build, look at the BAKED TEXTURE PROOF card on the Build page. After a
prop build, the same card shows the front and back of the reduced prop wearing the newly
baked texture.

Fully restart Garry's Mod and enter Sandbox. Return to the Builder and select Read GMod
Runtime Check.

A project is never marked Complete until the current game session writes a passing runtime
report for the same project and build token.
