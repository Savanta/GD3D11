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

- `Tessellation: displacing world meshes (factor 8, target 8 px, scale 0.3, 848 ranges as 3-control-point patches)`
  - the line comes from the Forward+ draw itself, i.e. from the path the visible world takes, and
  `target 8 px` means the per-edge factors are the adaptive ones.
- With `TessellationDisplacement` set in the tens the surfaces bulge absurdly and the ground pushes
  through the camera: the setting drives geometry, not just a counter. Around 0.3 the relief follows
  the textures and the frame is indistinguishable from a non-tessellated one except on close surfaces.
- The frame stays a world frame: terrain, its textures, props and HUD all render at the capped 60 FPS
  with the settings window open or closed, and no cracks appear at patch borders (the factors are
  symmetric per edge, and every patch is at least `MinFactor`).
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

The domain stage displaces along a **relief texture** (`TX_Relief`, t0, with the engine's linear
sampler), sampled per domain vertex, in one of three modes carried in the patch constant buffer:

- **mode 2 - height map**: `'<texture>_HEIGHT.DDS'` under `Heightmaps_<gameName>`, found by the same
  synchronous probe + async load + refcounted cache as the normal maps (`MyDirectDrawSurface7`).
  0.5 is the neutral surface and the shader displaces by `(h - 0.5) * 2`, so a flat stretch of the
  map moves nothing. This is real height.
- **mode 1 - normal map slope**: for materials that ship only a normal map, `1 - n.z` (half weight)
  is a *proxy*, not a height: it follows the texture grain but does not reconstruct shape.
- **mode 0 - white texture**: decodes to a flat normal, exactly zero relief. No branch in the draw.

The SRV and the mode are per material, so both ride the patch draw; the constant buffer is
re-uploaded only when the mode changes, and the pool hashes the payload.

`tools/bake_heightmap.py` produces those height maps: from a tangent-space normal map it solves
`lap(h) = div(slope)` (Poisson, SOR with mirror boundaries - the projection removes the
non-integrable noise that plain integration turns into spikes), or from a diffuse with
`--from-diffuse`. Output is a single-channel R8_UNORM DDS with a DX10 header. The tool ships, the
data does not: a height set baked from another pack's textures is a derivative work
(`docs/heightmaps.md`).

**Measured limit (26.11.2026): in this mod the relief channel never fires.** With
`[General] EnableDebugLog=1` the probe line above shows that only **10** textures ever reach
`MyDirectDrawSurface7::LoadAdditionalResources` in a whole session, and all ten are menu/UI
(`STARTSCREEN`, `FONT_*`, `PROGRESS*`, ...). No world texture is probed, so no world material can
carry a `_NORMAL.DDS` or a `_HEIGHT.DDS` - a file dropped into `Heightmaps_<gameName>` stays unused
(verified by placing one for a probed water texture and restarting). Consequence: modes 1 and 2 are
dead in practice here and the visible displacement is the PN bump alone. Feeding the relief from the
material's own texture, or finding why world textures bypass the probe, is the next real step -
`docs/heightmaps.md`'s whole premise rests on it.

## Next step

- **Get the relief channel to see world textures.** Everything else here is measurable; this is the one
  that decides whether `docs/heightmaps.md` is worth anything: today the additional-map probe only ever
  sees menu/UI textures, so no world material can carry a normal or height companion.
- Back-face culling from `AdaptiveTesselation.h` (`BackFaceCull`), which needs the view-space
  handedness confirmed on screen first - a wrong sign culls the whole world.
- Shadows (`ShadowPass_DrawWorldMesh`) still draw the undisplaced world.
- Per-material strength: `MaterialInfo::Buffer::DisplacementFactor` already exists and persists, but the
  domain stage reads one global amplitude today.
- PN-AEN silhouettes via the vendored `meshopt_generateTessellationIndexBuffer()` (needs an adjacency
  index buffer built with the wrapped world mesh, and a 4-control-point hull).

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
