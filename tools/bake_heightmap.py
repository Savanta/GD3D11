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

The source may be a PNG/JPG, a normal-map DDS, or one of the dumps the renderer writes with
GD3D11_TEXDUMP=1 (plain BGRA8), so the chain is: dump the texture in-game, bake it here, drop the
_HEIGHT.DDS into the replacement root the probe reads.

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


def load_dds(path):
    """Read the uncompressed DDS variants this project deals with, without Pillow's DDS support.

    The renderer's texture dump (GD3D11_TEXDUMP=1) writes plain BGRA8 with colour masks, and the baker's
    own output is a DX10 R8_UNORM. Pillow opens neither, so both are decoded here.
    """
    data = open(path, "rb").read()
    if data[:4] != b"DDS ":
        raise ValueError(f"{path}: not a DDS file")

    height, width = struct.unpack_from("<II", data, 12)
    pf_flags, fourcc = struct.unpack_from("<II", data, 80)
    payload = 128

    if pf_flags & 0x4 and fourcc == struct.unpack("<I", b"DX10")[0]:
        dxgi_format = struct.unpack_from("<I", data, 128)[0]
        payload = 148
        if dxgi_format == 61:      # R8_UNORM (the format this tool writes)
            px = data[payload:payload + width * height]
            if len(px) < width * height:
                raise ValueError(f"{path}: truncated R8 payload")
            return Image.frombytes("L", (width, height), px)
        if dxgi_format not in (28, 87):   # R8G8B8A8_UNORM, B8G8R8A8_UNORM
            raise ValueError(f"{path}: unsupported DXGI format {dxgi_format}")
        order = "RGBA" if dxgi_format == 28 else "BGRA"
    else:
        r_mask = struct.unpack_from("<I", data, 92)[0]
        order = "RGBA" if r_mask == 0x000000ff else "BGRA"

    px = data[payload:payload + width * height * 4]
    if len(px) < width * height * 4:
        raise ValueError(f"{path}: truncated payload")
    # Pillow has no raw "BGRA" decoder for RGB, only for RGBA, so decode four channels and drop alpha.
    return Image.frombytes("RGBA", (width, height), px, "raw", order).convert("RGB")


def load_gray(path):
    if str(path).lower().endswith(".dds"):
        im = load_dds(path)
    else:
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


def write_dds_rgba(path, pixels_u8, w, h):
    """Four-channel DDS with a DX10 header (DXGI_FORMAT_R8G8B8A8_UNORM).

    Normal maps go out uncompressed on purpose: the renderer decodes a tangent-space normal as
    `.xyz * 2 - 1`, which is exactly what an uncompressed X/Y/Z-coded map gives. A BC5/BC3 (DXT5nm)
    map stores X in alpha and would decode to garbage through that path.
    """
    DDSD_CAPS, DDSD_HEIGHT, DDSD_WIDTH, DDSD_PITCH, DDSD_PIXELFORMAT = 0x1, 0x2, 0x4, 0x8, 0x1000
    DDPF_FOURCC = 0x4
    flags = DDSD_CAPS | DDSD_HEIGHT | DDSD_WIDTH | DDSD_PITCH | DDSD_PIXELFORMAT
    header = struct.pack("<7I44x", 124, flags, h, w, w * 4, 0, 0)
    pf = struct.pack("<2I4s20x", 32, DDPF_FOURCC, b"DX10")
    caps = struct.pack("<4I4x", 0x1000, 0, 0, 0)
    dx10 = struct.pack("<5I", 28, 3, 0, 1, 0)      # R8G8B8A8_UNORM, TEXTURE2D
    with open(path, "wb") as f:
        f.write(b"DDS " + header + pf + caps + dx10)
        f.write(bytes(pixels_u8))


def height_to_normals(height, scale, wrap=True):
    """Central differences of a single-channel height field -> tangent-space normals.

    y points down in image space, so the green channel is flipped once here; that is the DirectX
    convention the renderer's normal maps use.
    """
    w, h = height.size
    px = height.load()
    out = bytearray(w * h * 4)
    for y in range(h):
        ym = (y - 1) % h if wrap else max(y - 1, 0)
        yp = (y + 1) % h if wrap else min(y + 1, h - 1)
        for x in range(w):
            xm = (x - 1) % w if wrap else max(x - 1, 0)
            xp = (x + 1) % w if wrap else min(x + 1, w - 1)
            dx = (px[xp, y] - px[xm, y]) / 255.0 * scale
            dy = (px[x, yp] - px[x, ym]) / 255.0 * scale
            nx, ny, nz = -dx, dy, 1.0
            inv = 1.0 / math.sqrt(nx * nx + ny * ny + nz * nz)
            i = (y * w + x) * 4
            out[i + 0] = max(0, min(255, int(round((nx * inv * 0.5 + 0.5) * 255))))
            out[i + 1] = max(0, min(255, int(round((ny * inv * 0.5 + 0.5) * 255))))
            out[i + 2] = max(0, min(255, int(round((nz * inv * 0.5 + 0.5) * 255))))
            out[i + 3] = 255
    return out


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
    ap.add_argument("--normals", metavar="PATH",
                    help="also write a tangent-space normal map (R8G8B8A8 DDS) derived from the height")
    ap.add_argument("--normal-scale", type=float, default=2.0,
                    help="slope gain of the generated normal map")
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

    if args.normals:
        # One height drives both: the displacement the domain stage applies and the shading detail a
        # normal map would add. Generating the normals from the same field is what keeps them agreei ng.
        height_img = Image.frombytes("L", (w, h), bytes(out))
        normals = height_to_normals(height_img, args.normal_scale)
        write_dds_rgba(args.normals, normals, w, h)
        print("zapisano:", args.normals, "%dx%d R8G8B8A8 normals (%d B)" % (w, h, len(normals)))


if __name__ == "__main__":
    sys.exit(main())
