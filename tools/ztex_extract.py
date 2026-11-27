#!/usr/bin/env python3
"""Convert Gothic `ZTEX` textures (as stored in .vdf archives) to uncompressed DDS.

Why: the archives hold `.TEX` entries that start with the magic `ZTEX` - a 32-byte header and then a
raw BC1/BC3 mip chain, no compression wrapper (measured: a 1024x1024 BC1 texture with 10 mip levels
occupies 699044 B of payload, which is exactly the block sum). Nothing in the game or in Wine is needed
to read them, so textures can be processed on a box where the world will not load.

Output is RGBA8/BGRA8 uncompressed because that is what both consumers want: Marigold takes pixels and
GD3D11's normal-map path decodes `.xyz*2-1` from an uncompressed texture.

Usage:
    ztex_extract.py <input-dir-or-file> <out-dir> [--limit N] [--match SUBSTR]
"""

import argparse
import pathlib
import struct
import sys

import numpy as np

MAGIC = b"ZTEX"
HEADER = 32
# format field -> (kind, bytes per 4x4 block or bytes per pixel)
FORMATS = {8: ("BC1", 8), 16: ("BC3", 16), 32: ("RGBA8", 4)}


def parse_ztex(blob: bytes):
    if not blob.startswith(MAGIC):
        raise ValueError("not a ZTEX texture")
    mips, width, height, fmt = struct.unpack_from("<IIII", blob, 8)
    if fmt not in FORMATS:
        raise ValueError(f"nieznany format {fmt}")
    kind, unit = FORMATS[fmt]
    return kind, width, height, max(mips, 1), unit, blob[HEADER:]


def dds_header(width: int, height: int, mips: int) -> bytes:
    flags = 0x1 | 0x2 | 0x4 | 0x1000 | 0x20000 | 0x8       # caps|height|width|pixfmt|mipmapcount|pitch
    caps = 0x1000 | 0x8 | 0x400000                          # texture|complex|mipmap
    pf = struct.pack("<IIIIIIII", 32, 0x41, 0, 32, 0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000)
    return (b"DDS " + struct.pack("<IIIIIII", 124, flags, height, width, width * 4, 0, mips)
            + b"\0" * 44 + pf + struct.pack("<IIIII", caps, 0, 0, 0, 0))


def _expand565(c: np.ndarray) -> np.ndarray:
    r = ((c >> 11) & 0x1F).astype(np.uint32) * 255 // 31
    g = ((c >> 5) & 0x3F).astype(np.uint32) * 255 // 63
    b = (c & 0x1F).astype(np.uint32) * 255 // 31
    return np.stack([r, g, b], axis=-1)


def decode_bc1(data: bytes, width: int, height: int) -> np.ndarray:
    """BC1: 8 B per 4x4 block - two RGB565 endpoints plus 16 two-bit indices, row-major."""
    bw, bh = max(1, (width + 3) // 4), max(1, (height + 3) // 4)
    blocks = np.frombuffer(data[:bw * bh * 8], dtype=np.uint8).reshape(bh, bw, 8)
    c0 = blocks[..., 0:2].copy().view(np.uint16).reshape(bh, bw).astype(np.uint32)
    c1 = blocks[..., 2:4].copy().view(np.uint16).reshape(bh, bw).astype(np.uint32)
    e0, e1 = _expand565(c0), _expand565(c1)

    pal = np.empty((bh, bw, 4, 3), dtype=np.uint32)
    pal[:, :, 0] = e0
    pal[:, :, 1] = e1
    pal[:, :, 2] = (2 * e0 + e1) // 3
    pal[:, :, 3] = (e0 + 2 * e1) // 3

    idx = blocks[..., 4:8].copy().view(np.uint32).reshape(bh, bw)
    sel = np.stack([(idx >> (2 * i)) & 3 for i in range(16)], axis=-1)      # (bh, bw, 16)
    chosen = np.take_along_axis(pal, sel[..., None].repeat(3, axis=-1), axis=2)
    img = chosen.reshape(bh, 4, bw, 4, 3).transpose(0, 2, 1, 3, 4).reshape(bh * 4, bw * 4, 3)
    return img[:height, :width].astype(np.uint8)


def decode_bc3(data: bytes, width: int, height: int) -> np.ndarray:
    """BC3: 8 B alpha (two endpoints + 16 three-bit indices) then the 8 B BC1 block."""
    bw, bh = max(1, (width + 3) // 4), max(1, (height + 3) // 4)
    blocks = np.frombuffer(data[:bw * bh * 16], dtype=np.uint8).reshape(bh, bw, 16)

    rgb = decode_bc1(blocks[..., 8:].copy().tobytes(), bw * 4, bh * 4)

    a0 = blocks[..., 0].astype(np.uint32)
    a1 = blocks[..., 1].astype(np.uint32)
    six = blocks[..., 2:8].astype(np.uint64)
    packed = (six[..., 0] | (six[..., 1] << 8) | (six[..., 2] << 16)
              | (six[..., 3] << 24) | (six[..., 4] << 32) | (six[..., 5] << 40))
    aidx = np.stack([((packed >> (3 * i)) & 7) for i in range(16)], axis=-1)   # (bh, bw, 16)

    a0f, a1f = a0.astype(np.int32), a1.astype(np.int32)
    eight = np.stack([
        a0, a1,
        (6 * a0f + 1 * a1f) // 7, (5 * a0f + 2 * a1f) // 7, (4 * a0f + 3 * a1f) // 7,
        (3 * a0f + 4 * a1f) // 7, (2 * a0f + 5 * a1f) // 7, (1 * a0f + 6 * a1f) // 7,
    ], axis=-1).astype(np.uint32)
    six_step = np.stack([
        a0, a1,
        (4 * a0f + 1 * a1f) // 5, (3 * a0f + 2 * a1f) // 5, (2 * a0f + 3 * a1f) // 5,
        (1 * a0f + 4 * a1f) // 5, np.zeros_like(a0), np.full_like(a0, 255),
    ], axis=-1).astype(np.uint32)
    table = np.where((a0 > a1)[..., None], eight, six_step)                   # (bh, bw, 8)
    alpha = np.take_along_axis(table, aidx, axis=2)
    amap = alpha.reshape(bh, 4, bw, 4).transpose(0, 2, 1, 3).reshape(bh * 4, bw * 4)[:height, :width]
    return np.dstack([rgb[:height, :width], amap]).astype(np.uint8)


def convert(src: pathlib.Path, outdir: pathlib.Path) -> bool:
    blob = src.read_bytes()
    try:
        kind, width, height, mips, unit, payload = parse_ztex(blob)
    except ValueError:
        return False
    if kind == "BC1":
        img = decode_bc1(payload, width, height)
    elif kind == "BC3":
        img = decode_bc3(payload, width, height)
    else:
        img = np.frombuffer(payload[:width * height * 4], dtype=np.uint8).reshape(height, width, -1)[..., :4]
    if img.shape[2] == 3:                              # BC1 nie niesie alfy
        img = np.dstack([img, np.full(img.shape[:2] + (1,), 255, np.uint8)])
    bgra = img[..., [2, 1, 0, 3]]                     # DDS expects BGRA
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / (src.stem + ".DDS")).write_bytes(dds_header(width, height, 1) + bgra.tobytes())
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("outdir")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--match", default="")
    a = ap.parse_args()

    src = pathlib.Path(a.src)
    files = [src] if src.is_file() else sorted(
        p for p in src.rglob("*") if p.is_file() and a.match.lower() in p.name.lower())
    done = 0
    for p in files:
        if convert(p, pathlib.Path(a.outdir)):
            done += 1
        if a.limit and done >= a.limit:
            break
    print(f"przekonwertowane: {done} z {len(files)}")
    return 0 if done else 1


if __name__ == "__main__":
    sys.exit(main())
