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
- `TesselationFactor` / `TesselationRange` in `GothicGraphicsState.h` (20.0 / 8.0), exposed only
  through AntTweakBar under `General/`.
- `GothicAPI.h`: `std::string TesselationShaderPair` per material, and `D2DEditorView.cpp` sets it
  to `"PNAEN_Tesselation"` when the mesh editor smooths a mesh with SPACE.

## What is missing

- **Nothing reads `TesselationShaderPair`.** It has exactly two occurrences in the tree: its
  declaration and that one assignment. It is not in `MaterialInfo::Buffer`, so it does not even
  survive a restart.
- **`HDShaderID` has one entry, `HD_OceanTess`.** There is no world-mesh tessellation shader
  registered, so no material could select one even if something read the field.
- **The existing pair does not fit world geometry.** Its hull input is
  `TEXCOORD0 = view position, TEXCOORD1 = world normal, TEXCOORD2 = view normal`, while the world
  vertex shader (`VS_ExInstancedObj.hlsl`) writes `TEXCOORD4 = normal (view)`,
  `TEXCOORD5 = view position`, plus UVs, diffuse, tangent and two clip positions. Binding the pair
  to world meshes would be a signature mismatch, which is presumably why nobody did.
- **The domain shader cannot re-project without the view-projection matrix.** It interpolates the
  patch in object space, so the displaced position has to be projected again; that matrix is not
  reachable from the shader as the scaffolding stands, it needs a constant buffer fed from the
  render pass.

## What a working version needs

1. A hull/domain pair written against `VS_ExInstancedObj`'s output, in one file (the class takes
   one path and compiles both entries from it), including `AdaptiveTesselation.h` for the factors.
2. A `cbuffer` with the view-projection matrix, `TesselationFactor` and `TesselationRange`, pushed
   with `D3D11HDShader::UpdateBuffer` from the render pass.
3. `HDShaderID::HD_PNAEN` plus its `ShaderRegistry` entry, `with_category(ShaderCategory)`.
4. In the world-mesh draw: when tessellation is enabled and the material carries a pair, bind the
   HD shader, switch the topology to `3_CONTROL_POINT_PATCHLIST`, draw, then unbind and restore
   `TRIANGLELIST`. The switch must be gated by a setting that defaults to off, so a mistake cannot
   corrupt normal rendering.
5. The PN displacement itself: subdividing a flat triangle only costs triangles and changes nothing
   visible, so the value comes from displacing along the interpolated normal by the
   barycentric-weighted deviation of the control points - the classic PN-triangle term.

## How to tell whether it works

`hull shader:` and `domain shader:` lines in `Log.txt` **during a world run**. A run that reaches
the world without them proves nothing ran, whatever the settings say. The main menu is not a
valid test: it is drawn by the overlay at display resolution and is independent of render scale and
tessellation alike.
