# Height maps for textures: how it would plug in, and what it costs

Analysis done against this tree (not from memory). The point of writing it down: the pattern
already exists for normal maps, so height is an additive change rather than new plumbing.

## The pattern that is already there

- `D3D7/MyDirectDrawSurface7.cpp:370` - `ResolveResourcePath( TextureName, "_NORMAL.DDS", gameName )`
  finds a texture's companion file by name plus suffix.
- `MyDirectDrawSurface7.cpp:238` - the per-mod folder is `Normalmaps_<gameName>`, so each mod
  carries its own set instead of fighting over one directory.
- The normal map hangs off the surface (`surface->GetNormalmap()`) and reaches the pixel shader as
  `srv[1]` next to the diffuse in `srv[0]` (`D3D11GraphicsEngine.cpp` several sites).
- `MaterialInfo::Buffer` already carries a persisted `DisplacementFactor` (default 0.1), so the
  per-material strength knob exists and survives a restart.

So a height channel is: `ResolveResourcePath( TextureName, "_HEIGHT.DDS", gameName )`, folder
`system\GD3D11\Textures\Replacements\Heightmaps_<gameName>\` (resolved by `ResolveResourcePath`, with
`_work\Data\Textures\Replacements\` as the VDFS-side root that has no game subdirectory), a
`GetHeightmap()` on the surface, and `srv[2]` bound to the **domain**
shader in the tessellated draw. No format change, no new loader, no new ini section.

## Where the data comes from

1. **Integrate the normal map** - the normals encode the surface slope, so a least-squares Poisson
   solve (FFT) recovers a height field. The solve is the best *integrable* field, which is exactly
   why it is the right tool: it projects away the non-integrable noise that naive integration turns
   into spikes.
2. **Diffuse luminance** - cheap and decent for brick, stone, wood and metal grime, useless where
   brightness is not height (painted highlights, baked shadows, decals).
3. **Hybrid** - luminance for low frequencies, integrated normals for high. Recommended.

Baker is offline, so it can be slow: Python + numpy, periodic boundaries for tiling textures,
output DDS BC4 (single channel, 0.5 byte per pixel).

## The cost that actually constrains this

- **Memory in a 32-bit process**: 512x512 BC4 is 128 kB, 256x256 is 32 kB. Thousands of textures at
  full resolution is over a gigabyte and is not viable; a few hundred opted-in materials are tens of
  megabytes. So: only materials that ship a height file get one, capped by an LRU budget, resolution
  never above the diffuse.
- **Mesh density, not map quality, is the ceiling.** Gothic walls are often one quad per face; the
  tessellation factor is per patch, not per texel, so a wall tiled 4x needs a factor high enough to
  give the brick rows somewhere to live. If the mesh cannot carry it, no height map will save it.
- **No tangents.** The world vertex shader leaves the tangent at zero on purpose (`PS falls back to
  ddx/ddy`), so displacement must go along the interpolated normal. That is correct for height and
  one less thing to plumb.

## The experiment that decides everything

Take a dozen representative textures - brick wall, stone wall, floor tiles, rubble, wood planks -
bake heights, tessellate at factor 8 and 16, and compare against the same frames at factor 8 without
displacement. If bricks do not visibly read better than normal mapping alone on Gothic's single-quad
walls, the whole family of work is not worth it and tessellation stays a silhouette feature.

## Licensing

A height set baked from another mod's textures is a derivative work. Ship the **tool** and let users
bake locally, or ask the pack's authors first. Do not redistribute baked data.

## Where the maps come from (GPU route, measured)

Nothing in this mod ships a normal or height map, so they have to be generated. Two GPU routes were
tried on a brick swatch with an RTX 3080:

- **Depth Anything V2** (`depth-anything/Depth-Anything-V2-Small-hf`, via transformers): **wrong tool.**
  A monocular depth model predicts *scene* geometry, and a material texture has none, so it returned a
  smooth vertical gradient across the whole image - a bowl where the wall is flat - and nothing at the
  brick joints. Its output is unusable for relief, no matter how it is normalised.
- **Marigold Normals** (`prs-eth/marigold-normals-v0-1`, via `diffusers.MarigoldNormalsPipeline`):
  **right tool.** 8 steps, one ensemble, <1 s per 512x512 texture on the 3080. On the same swatch it
  produced a running-bond brick pattern with flat faces, recessed joints and bevelled edges, mean
  (137, 123, 255), i.e. a plausible tangent-space normal map.

`tools/genmaps_gpu.py` runs the normal route over a directory of textures and writes
`<name>_NORMAL.DDS` (uncompressed R8G8B8A8, X/Y/Z in RGB, green flipped to the DirectX convention the
engine decodes with `.xyz * 2 - 1`). Heights come from `tools/bake_heightmap.py` on those normal maps
(Poisson solve), so displacement and shading share one field instead of two guesses:

    tools/genmaps_gpu.py --input system/TexDump --out-normal /tmp/n
    for f in /tmp/n/*_NORMAL.DDS; do tools/bake_heightmap.py "$f" "/tmp/h/$(basename ${f%_NORMAL.DDS})_HEIGHT.DDS"; done

The textures themselves come from `GD3D11_TEXDUMP=1`, which writes every texture the game loads to
`system\TexDump\<name>.DDS` (see the dump note in `docs/tessellation.md`).
