#!/usr/bin/env python3
"""Extract textures from Gothic VDFS archives without the game.

Why this exists: GD3D11's `GD3D11_TEXDUMP=1` only dumps what the game actually loads, and getting a
loaded world on this pod proved unreliable (menu automation is flaky, and the game crashes right after
`Done extracting world!`). The archives hold the same images as `.TEX` entries - ZenGin wraps a DDS
blob and that blob is stored untouched - so the image comes out unchanged, and the entry NAME is the
name the engine uses when it resolves a `_HEIGHT.DDS` companion.

Two entry layouts exist in the wild and both are handled:

  * FLAT TABLE (measured in GTR and in the mod's own archives): fixed 80-byte records starting at the
    header's root offset - name[64] padded with spaces, then u32 type (1 = file, 2 = folder),
    u32 offset, u32 size, u32 reserved. Offsets are absolute from the file start.
  * LINKED TREE (classic GothicVDFS): u8 type, null-terminated name, then child/sibling pointers for
    folders or offset/size/timestamp for files.

The magic is searched rather than assumed at offset 0: the mod's archives put a one-line title in
front of it and GTR puts 256 spaces before it.

Usage:
    vdfs_extract.py <archive.vdf> <out-dir> [--match SUBSTR] [--limit N] [--list-only]
"""

import argparse
import pathlib
import struct
import sys

MAGIC = b"PSVDSC_V2.00"
DDS_MAGIC = b"DDS "
RECORD = 80


def parse_header(data: bytes):
    base = data.find(MAGIC, 0, 8192)
    if base < 0:
        base = data.find(MAGIC)
    if base < 0:
        raise ValueError("not a VDFS archive (no magic in the first 8 kB)")
    num_files, num_folders = struct.unpack_from("<II", data, base + 0x10)
    root = struct.unpack_from("<I", data, base + 0x20)[0]
    return num_files, num_folders, root


def read_name64(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].rstrip(b" ").decode("latin-1")


def entries_flat(data: bytes, root: int, count: int):
    """Fixed 80-byte records, validated by the caller against the header's file count."""
    out = []
    for i in range(count):
        off = root + i * RECORD
        if off + RECORD > len(data):
            break
        name = read_name64(data[off:off + 64])
        # The 16 bytes after the name are type-dependent: a FILE carries offset then size, a FOLDER
        # carries its child count and first-child offset. Measured on GTR: SPLASH.BMP reads
        # offset=0x022F0A56, size=0x49418, while _WORK reads count=2, offset=0. A record is a file
        # exactly when its size is positive and the range lies inside the archive.
        foff, fsize = struct.unpack_from("<II", data, off + 64)
        if not name or not all(32 <= ord(c) < 127 for c in name):
            break
        if fsize == 0 or foff + fsize > len(data):
            continue
        out.append((name, 1, foff, fsize))
    return out


def walk_tree(data: bytes, offset: int, prefix: str, out: list, depth: int = 0) -> None:
    """Classic linked tree: stops on anything that is not a valid entry."""
    if depth > 64:
        return
    pos, guard = offset, 0
    while pos and guard < 200000 and pos + 2 <= len(data):
        guard += 1
        etype = data[pos]
        if etype not in (0, 1):
            return
        end = data.find(b"\0", pos + 1)
        if end < 0:
            return
        name = data[pos + 1:end].decode("latin-1")
        if etype == 0:
            child, sibling = struct.unpack_from("<II", data, end + 1)
            walk_tree(data, child, prefix + name + "\\", out, depth + 1)
            pos = sibling
        else:
            foff, fsize = struct.unpack_from("<II", data, end + 1)
            if foff + fsize <= len(data):
                out.append((prefix + name, 1, foff, fsize))
            pos = struct.unpack_from("<I", data, end + 17)[0] if end + 21 <= len(data) else 0


def dds_length(blob: bytes) -> int:
    """Total DDS size from its own header (128 B header + mip chain)."""
    if len(blob) < 128 or not blob.startswith(DDS_MAGIC):
        return 0
    height, width = struct.unpack_from("<II", blob, 12)
    fourcc = blob[84:88]
    bits = struct.unpack_from("<I", blob, 88)[0]
    mips = struct.unpack_from("<I", blob, 28)[0]
    block = 8 if fourcc == b"DXT1" else 16 if fourcc in (b"DXT3", b"DXT5", b"ATI1", b"ATI2", b"BC5U") else 0
    total, w, h = 128, max(width, 1), max(height, 1)
    for _ in range(max(mips, 1)):
        if block:
            total += max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * block
        else:
            total += w * h * max(bits // 8, 1)
        w, h = max(w // 2, 1), max(h // 2, 1)
    return total


def collect(data: bytes):
    num_files, num_folders, root = parse_header(data)
    flat = entries_flat(data, root, num_files + num_folders)
    files = [(n, off, sz) for n, t, off, sz in flat if t == 1 and sz and off + sz <= len(data)]
    if num_files and len(files) >= num_files * 0.9:
        return files, f"tablica plaska ({len(flat)} rekordow przy {num_files} plikach)"
    tree = []
    walk_tree(data, root, "", tree)
    return tree, f"drzewo linkowane ({len(tree)} wpisow)"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("archive")
    ap.add_argument("outdir")
    ap.add_argument("--match", default="", help="tylko nazwy zawierajace ten fragment")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--list-only", action="store_true")
    a = ap.parse_args()

    path = pathlib.Path(a.archive)
    data = path.read_bytes()
    files, how = collect(data)
    print(f"{path.name}: {len(files)} plikow, uklad: {how}")

    tex = [f for f in files if f[0].lower().endswith(".tex") and a.match.lower() in f[0].lower()]
    print(f"  .TEX pasujacych: {len(tex)}")
    if a.list_only:
        for n, off, sz in tex[:15]:
            print(f"    {sz:9d}  {n}")
        return 0

    out = pathlib.Path(a.outdir)
    out.mkdir(parents=True, exist_ok=True)
    written = 0
    for name, off, size in tex:
        # Wpis .TEX to surowy ZTEX (naglowek 32 B + lancuch mipmap BC1/BC3), nie DDS i nie zlib -
        # zmierzone: 1024x1024 BC1 z 10 poziomami to dokladnie 699044 B payloadu. Konwersje do
        # nieskompresowanego DDS robi tools/ztex_extract.py, bo tylko takich pikseli chce Marigold.
        flatname = pathlib.Path(name.replace("\\", "/").split("/")[-1]).stem + ".TEX"
        (out / flatname).write_bytes(data[off:off + size])
        written += 1
        if a.limit and written >= a.limit:
            break
    print(f"  zapisane: {written} wpisow .TEX -> {out}")
    return 0 if written else 1


if __name__ == "__main__":
    sys.exit(main())
