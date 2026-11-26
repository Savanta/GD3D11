# Tessellation: what exists in this tree, and what is missing

Tessellation looks present here and is not. This note records the exact chain, because every link
was traced through the code and the conclusion is not obvious from any single file.

## What exists

- `D3D11HDShader` - a shader class that compiles and binds a hull and a domain shader
  (`LoadShader(hull, domain)`, entries `HSMain` / `DSMain`). It works: the ocean uses it.
- `Shaders/AdaptiveTesselation.h` - adaptive tessellation utilities (edge/normal/screen-space
  factors, patch culling). **Nothing includes it.**
- `Shaders/PNAEN_Tesselation.hlsl`, `Shaders/domain_PNAEN.hlsl` - a PN-triangle hull/domain pair
  for a **3-control-point** patch, so no adjacency index buffer is required: the existing triangle
  index buffer can be drawn as `D3D11_PRIMITIVE_TOPOLOGY_3_CONTROL_POINT_PATCHLIST`.
- `Shaders/WorldTess.hlsl` - the pair this fork uses for the static world mesh (see below).

## Where the rest of the scaffolding lives

`TesselationFactor` / `TesselationRange` and the per-material `TesselationShaderPair` (set by
`D2DEditorView.cpp` when the mesh editor smooths a mesh with SPACE) are in **SaiyansKing's tree**,
not in this one. Here tessellation is driven by two renderer settings instead, so it does not depend
on a material field that no loader writes.

## Where the hook lives (settled 26.11.2026)

The first implementation hooked `GothicAPI::DrawMeshInfo` and was measured never to run for the
visible world: `D3D11ForwardPlusRenderer.cpp` has **zero** calls to that function, and it is only
reached from the poly-strip / animated-texture path. That branch is gone.

The tessellated draw now lives in `D3D11GraphicsEngine::DrawWorldMesh`, which is where the visible
world passes: `DrawWorldMeshNaive` -> `DrawWorldMesh`, twice per frame in Forward+ (once as the
depth prepass, once for colour), drawing the wrapped 36-byte world mesh with `VS_ExPacked` at
`BaseIndexLocation + IndexOffset` with `R32_UINT` indices.

Three things that each cost a full debugging cycle, all measured on the instance:

1. **`DrawWorldMesh` unbinds HS/DS itself.** Right after `BindWrappedWorldMeshPacked` it calls
   `DSSetShader(nullptr)` / `HSSetShader(nullptr)` as part of its own "draw triangles, not patches"
   setup. A tessellation bind placed before those two lines issues every patch draw with no hull and
   no domain shader - which draws **nothing**. The symptom was the static world mesh (the terrain and
   its textures) disappearing while the log happily reported tessellated ranges, and the displacement
   setting having no effect because no shader was reading it. Bind after the resets.
2. **The prepass has to tessellate too.** The colour pass tests `GREATER_EQUAL` against the prepass
   depth, and the prepass' position-only stream (`MeshPositionBuffer` + `VS_ExDepth`) carries no
   normal, so a normal-driven displacement cannot run from it. With tessellation on, the packed
   stream stays bound for the whole prepass.
3. **The hull input signature must match `VS_ExPacked` exactly** - the same ten semantics, including
   `TANGENT` (TEXCOORD3) and the far-field term (TEXCOORD8). A mismatch is a device error, not a
   visual glitch.

## Status (26.11.2026)

**Proven in a world run** (line read from `Log.txt`, frame captured after `Done extracting world!`):

- `Tessellation: displacing world meshes (factor 8, scale 5, 848 ranges as 3-control-point patches)`
  - the line comes from the Forward+ draw itself, i.e. from the path the visible world takes.
- `TessellationDisplacement` 12 bulges the surfaces absurdly and 20 pushes the ground through the
  camera: the setting drives geometry, not just a counter. At 5.0 the bulge is present but subtle,
  which is why the first attempt concluded "no effect" - see the amplitude note below.
- The frame stays a world frame: terrain, its textures, props and HUD all render, with
  `AllowNormalmaps=1` and normal-mapped surfaces unaffected.
- With `Tessellation=0` the whole block is skipped, so the off path cannot regress anything.

Both settings are in the F11 menu under *World -> Tessellation* and are read every frame, so the
effect is live there; they persist as `[Display] Tessellation` / `TessellationDisplacement` in
`system/GD3D11/UserSettings.ini`.

**Known limits:**
- Per-edge factors are screen-space adaptive (`Factor` is the ceiling, `TessellationTargetPixels` the
  target edge length), so cost follows what is on screen. `AdaptiveTesselation.h`'s orientation
  (silhouette) and back-face helpers are still unused: back-face culling needs the view-space handedness
  confirmed on screen before it can be trusted, because a wrong sign culls the whole world.
- Shadows (`ShadowPass_DrawWorldMesh`) draw the undisplaced world, so a strongly displaced surface
  does not cast a matching shadow.
- The amplitude is a fraction of the patch's mean edge length. The term `2*x*y*z` peaks at 2/27 and
  is zero on every edge (that is what keeps patches watertight), so subdivision alone is invisible
  and a fixed unit scale reads as "the feature does nothing".

## Texture relief (26.11.2026)

The domain stage samples the material's **normal map** (`TX_Relief`, t0, with the engine's linear
sampler) per domain vertex and uses the map's tangent-space slope (`1 - n.z`) as a height proxy, so
the displaced surface follows the brick rows and rock grain instead of only the barycentric bump. A
material without a normal map gets the engine's white texture, which decodes to a flat normal and
therefore to exactly zero relief - no flag in the constant buffer and no branch in the draw.

The SRV is bound per patch draw, not per pass, because it changes with the material; the draw caches
the last bound view so a run of ranges sharing a texture costs one `DSSetShaderResources`.

That is the cheap end of the idea in `docs/heightmaps.md`: a real height channel (`_HEIGHT.DDS`
companions under `Heightmaps_<gameName>`) gives displacement that is not a slope proxy, and the
offline bake described there is what would make it physical rather than decorative.

## Next step

- Distance-adaptive factors from `AdaptiveTesselation.h`, so the factor falls off with distance instead
  of multiplying the triangle count everywhere.
- Shadows (`ShadowPass_DrawWorldMesh`) still draw the undisplaced world.
- Per-material strength: `MaterialInfo::Buffer::DisplacementFactor` already exists and persists, but the
  domain stage reads one global amplitude today - plumbing it into the patch cbuffer is the next step
  after the texture relief below.

## How to tell whether it works

1. `Log.txt` must contain the `Tessellation: displacing world meshes (...)` line **and** the frame
   must still show the terrain. The line alone is not proof: it is emitted when the draw is issued,
   which was also true while 848 patch draws went out with no shader bound.
2. The main menu is not a test. A frame that shows only bushes, palms and ruins with no ground means
   the static world mesh is not being drawn at all - that is the signature of the bug above.

## Driving the instance (measured)

- After a restart the game sits on its main menu; a static ~2.84 MB frame that also carries the
  engine's "Compiling shaders..." text while it warms up. **`F12` in that menu loads the quicksave.**
  Mouse clicks in the menus do nothing under the stream, and synthetic arrow keys are unreliable.
  The world is ready when `Log.txt` says `Loading world!` / `Done extracting world!` - never judge by
  frame size.
- Never move or delete the game's `saves/` as a diagnostic. Copy what is needed to a path outside
  the game tree; a `mv saves /tmp/x` followed by `rm -rf /tmp/x` is unrecoverable on this instance.
