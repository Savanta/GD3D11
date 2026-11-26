#!/usr/bin/env python3
"""Bake a displacement height map for the tessellation domain stage.

The renderer reads '<texture>_HEIGHT.DDS' next to the other replacement maps (see
docs/tessellation.md and docs/heightmaps.md) and displaces the tessellated world surface by it.
This tool produces those files. It ships the *tool* on purpose: a height set baked from another
pack's textures is a derivative work, so users bake locally instead of redistributing data.

Two sources:
  * a tangent-space normal map (default): the normals are the surface slope, so the height is the
    least-squares solution of grad(h) = slope - a Poisson solve. That projection is what removes the
    non-integrable noise that plain integration turns into spikes.
  * a diffuse map (--from-diffuse): luminance as height. Cheap, good for brick/stone/wood, wrong
    wherever brightness is not height (painted highlights, baked shadows).

Output is a single-channel R8_UNORM DDS written with a DX10 header, which is what the engine's
DirectXTex loader and the domain shader (.r) expect.

Usage:
  tools/bake_heightmap.py texture_NORMAL.png texture_HEIGHT.DDS
  tools/bake_heightmap.py wall.png wall_HEIGHT.DDS --from-diffuse --strength 1.5
  tools/bake_heightmap.py tile_NORMAL.DDS tile_HEIGHT.DDS --solve-size 512 --iterations 400
"""

import argparse
import math
import struct
import sys
from array import array

from PIL import Image


def load_gray(path):
    im = Image.open(path)
    im.load()
    return im.convert("RGBA")


def normals_to_slope(px, w, h, flip_green):
    """dh/du, dh/dv per texel from a tangent-space normal map."""
    slope_u = array("f", [0.0] * (w * h))
    slope_v = array("f", [0.0] * (w * h))
    for y in range(h):
        row = y * w
        for x in range(w):
            r, g, b, _ = px[x, y]
            nx = r / 127.5 - 1.0
            ny = (g / 127.5 - 1.0) * (-1.0 if flip_green else 1.0)
            nz = b / 127.5 - 1.0
            # A near-tangent normal has no usable slope; clamping keeps the solve from exploding.
            inv = 1.0 / max(nz, 0.15)
            slope_u[row + x] = -nx * inv
            slope_v[row + x] = ny * inv
    return slope_u, slope_v


def poisson_solve(slope_u, slope_v, w, h, iterations, relaxation=1.6):
    """Solve lap(h) = div(slope) with mirror boundaries; SOR, in place.

    Divergence of the slope field is computed once; each iteration is one Jacobi step with
    over-relaxation. Mirror boundaries keep a tiling texture tileable and avoid the dark rim that
    a zero boundary would clamp into the result.
    """
    div = array("f", [0.0] * (w * h))
    for y in range(h):
        for x in range(w):
            i = y * w + x
            xl = y * w + (x - 1 if x > 0 else x + 1)
            xr = y * w + (x + 1 if x < w - 1 else x - 1)
            yu = (y - 1 if y > 0 else y + 1) * w + x
            yd = (y + 1 if y < h - 1 else y - 1) * w + x
            # Central differences of the slope, matching the Laplacian's 5-point stencil.
            du = (slope_u[xr] - slope_u[xl]) * 0.5 + (slope_v[yd] - slope_v[yu]) * 0.5
            div[i] = du

    height = array("f", [0.0] * (w * h))
    for _ in range(iterations):
        for y in range(h):
            row = y * w
            up = (y - 1 if y > 0 else y + 1) * w
            dn = (y + 1 if y < h - 1 else y - 1) * w
            for x in range(w):
                i = row + x
                xl = row + (x - 1 if x > 0 else x + 1)
                xr = row + (x + 1 if x < w - 1 else x - 1)
                target = (height[up + x] + height[dn + x] + height[xl] + height[xr] - div[i]) * 0.25
                height[i] += relaxation * (target - height[i])
    return height


def luminance_height(px, w, h):
    lum = array("f", [0.0] * (w * h))
    for y in range(h):
        row = y * w
        for x in range(w):
            r, g, b, _ = px[x, y]
            lum[row + x] = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0
    return lum


def normalize(values):
    lo, hi = min(values), max(values)
    span = hi - lo
    if span <= 1e-9:
        return array("f", [0.5] * len(values))
    return array("f", [ (v - lo) / span for v in values ])


def write_dds_r8(path, pixels_u8, w, h):
    """Single-channel DDS with a DX10 header (DXGI_FORMAT_R8_UNORM)."""
    DDSD_CAPS, DDSD_HEIGHT, DDSD_WIDTH, DDSD_PITCH, DDSD_PIXELFORMAT = 0x1, 0x2, 0x4, 0x8, 0x1000
    DDPF_FOURCC = 0x4
    flags = DDSD_CAPS | DDSD_HEIGHT | DDSD_WIDTH | DDSD_PITCH | DDSD_PIXELFORMAT
    header = struct.pack(
        "<7I44x",
        124, flags, h, w, w, 0, 0,          # size, flags, height, width, pitch, depth, mipmaps
    )
    pf = struct.pack("<2I4s20x", 32, DDPF_FOURCC, b"DX10")
    caps = struct.pack("<4I4x", 0x1000, 0, 0, 0)   # DDSCAPS_TEXTURE
    dx10 = struct.pack("<5I", 61, 3, 0, 1, 0)      # R8_UNORM, TEXTURE2D, no misc, 1 array slice
    with open(path, "wb") as f:
        f.write(b"DDS " + header + pf + caps + dx10)
        f.write(bytes(pixels_u8))


def main():
    ap = argparse.ArgumentParser(description="Bake a _HEIGHT.DDS for the tessellated world.")
    ap.add_argument("source", help="normal map (default) or diffuse map with --from-diffuse")
    ap.add_argument("output", help="path of the .dds to write")
    ap.add_argument("--from-diffuse", action="store_true", help="use luminance instead of the slope field")
    ap.add_argument("--solve-size", type=int, default=256, help="size of the Poisson solve (power of two)")
    ap.add_argument("--iterations", type=int, default=300, help="SOR iterations of the solve")
    ap.add_argument("--strength", type=float, default=1.0, help="scale of the height after normalisation")
    ap.add_argument("--flip-green", action="store_true",
                    help="invert the normal map's green channel (OpenGL-style maps)")
    args = ap.parse_args()

    src = load_gray(args.source)
    w, h = src.size
    px = src.load()

    if args.from_diffuse:
        values = normalize(luminance_height(px, w, h))
        print("%dx%d: luminance height" % (w, h))
    else:
        solve = max(16, min(args.solve_size, max(w, h)))
        sw = max(1, round(w * solve / max(w, h)))
        sh = max(1, round(h * solve / max(w, h)))
        small = src.resize((sw, sh), Image.BILINEAR)
        spx = small.load()
        su, sv = normals_to_slope(spx, sw, sh, args.flip_green)
        field = poisson_solve(su, sv, sw, sh, args.iterations)
        values = normalize(field)
        # The solve runs small (it is a global least-squares problem); the tessellated surface only
        # carries detail up to mesh density anyway, so a bilinear upscale loses nothing visible.
        img = Image.new("F", (sw, sh))
        img.putdata(list(values))
        img = img.resize((w, h), Image.BICUBIC)
        values = normalize(array("f", list(img.getdata())))
        print("%dx%d: Poisson height solved at %dx%d, %d iterations" % (w, h, sw, sh, args.iterations))

    out = array("B", [0] * (w * h))
    for i, v in enumerate(values):
        # 0.5 is the neutral surface: the shader displaces by (h - 0.5), so a flat area moves nothing.
        out[i] = max(0, min(255, int(round((0.5 + (v - 0.5) * args.strength) * 255.0))))
    write_dds_r8(args.output, out, w, h)
    print("zapisano:", args.output, "%dx%d R8_UNORM (%d B)" % (w, h, len(out)))


if __name__ == "__main__":
    sys.exit(main())
