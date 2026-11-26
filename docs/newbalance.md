# Running this renderer inside Gothic II: New Balance

Notes from bringing the renderer up on a New Balance install (Linux + Wine + DXVK, streamed
through Sunshine). Everything here was measured, not assumed; the point of writing it down is that
each item cost a cycle to discover.

## The package must travel as a unit

The renderer compiles its shaders **from the game's disk** (`system/GD3D11/shaders`). A binary
without its matching shader set does not degrade - it renders a black screen. Numbers from
26.11.2026: this tree ships **272** shaders against the SK9-era **92**, because the pipeline moved
to compute (tiled light culling, TAA, DoF, SAO, bloom). So deploy the whole package: the proxy
`ddraw.dll`, its runtime libraries, `GD3D11/Bin`, `GD3D11/shaders` and `GD3D11/D3D12` (Agility SDK).

Copy **over** the existing tree, never clean it: New Balance ships shaders of its own
(`PS_Ghost.hlsl`, `PS_LinDepth.hlsl`, `SMAA.fx`, `SMAA.fxh`). The new renderer does not know them,
they simply stay unused - but deleting them is not our call to make.

## What must NOT come from the upstream package

A full upstream release also carries `GD3D11/Textures` (1383 files) and `GD3D11/Meshes` (175).
Those are stock copies; New Balance curates its own skies and models. Copying the package wholesale
replaces the mod's art with the vanilla sets. Take the binaries and shaders, leave the data, and add
only the individual files the log actually asks for.

## The one file master needs that NB lacks

Measured: the new pipeline asks for

    system\GD3D11\textures\bluenoise-rgba-512-bgra.dds

(blue noise, used by the screen-space effects). New Balance does not ship it, and the missing file
is non-fatal but degrades those effects. Place it under **both** spellings - `GD3D11/textures/` and
`GD3D11/Textures/` - because the log asks in lower case and Linux is case-sensitive.

The release published from this fork already contains it, so a deploy from our artifact does not
hit this at all.

## Verifying a deploy (in this order)

1. `Info: GD3D11 Version ...` in `system/Log.txt` must name the build you deployed - and master logs
   with timestamps and levels, unlike SK9's `Info:` prefix, so the format itself tells you which
   line of development is loaded.
2. `grep -c 'Failed to load texture'` must be `0`.
3. A run that enters the world (the main menu is not enough - it needs no world shaders) must render
   roughly 2.8 MB per 1920x1080 frame. A flat frame around 13 kB means the game is drawing in a
   window, not full screen.

## Resolution: two of them, and they must agree

The renderer keeps its own resolution (`[Display] Width/Height` in `GD3D11/UserSettings.ini`) while
Gothic keeps another in `[VIDEO] zVidResFullscreenX/Y` of `GOTHIC.INI`. When they disagree the game
lays out its interface in a different resolution than the picture and maps the cursor through its
own virtual screen, which is what `known_issues.md` describes as "the Inventory is out of screen"
and "does not properly respond to mouse input".

`GOTHIC.INI` is the single source of truth here: on load the game's value wins for the renderer, a
change made from the F11 menu is written back into it, and NB's launcher already forces that file
at every start - so the renderer follows the launcher instead of fighting it.

## Known-good combination

- renderer: this fork at `git-111e3f71f` (2026-11-26) plus the resolution fix
- package: published under our releases, verified by sha256 before unpacking
- game data: New Balance, its own `Textures`/`Meshes` untouched
