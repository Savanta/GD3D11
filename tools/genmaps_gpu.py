#!/usr/bin/env python3
"""Predict normal maps for world textures with Marigold Normals (diffusion, GPU).

Why a normal model and not a depth model: these are *material* textures, not photographs of a scene.
Measured on a brick swatch, Depth Anything V2 returned a smooth vertical gradient across the whole
image - it invented a bowl where the wall is flat - because a monocular depth model predicts scene
geometry and there is none here. Marigold Normals is trained for surface normals, which is exactly
the quantity a texture carries.

The height map is *not* produced here: feed the generated `_NORMAL.DDS` to
`tools/bake_heightmap.py` (Poisson solve of the slope field), so one normal set yields both the relief
the domain stage displaces and the lighting detail, from a single source and a single curve.

Usage:
    tools/genmaps_gpu.py --input system/TexDump --out-normal /tmp/n --limit 4 --preview /tmp/p
    for f in /tmp/n/*_NORMAL.DDS; do
        tools/bake_heightmap.py "$f" "/tmp/h/$(basename "${f%_NORMAL.DDS}")_HEIGHT.DDS"
    done
"""

import argparse
import importlib.util
import pathlib
import sys

import numpy as np
import torch
from PIL import Image


def _load_bake_module():
    here = pathlib.Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location("bake_heightmap", here / "bake_heightmap.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bake = _load_bake_module()


def normals_to_rgba(normals: np.ndarray, flip_green: bool) -> np.ndarray:
    """Tangent-space normals in [-1, 1] -> RGBA8 with X, Y, Z in RGB.

    Uncompressed on purpose: the renderer decodes a normal as `.xyz * 2 - 1`. A BC5/DXT5nm map keeps X
    in alpha and would decode to garbage through that path.
    """
    n = np.asarray(normals, dtype=np.float32)[..., :3]
    # Marigold emits OpenGL-style normals (green = up in image space); the engine wants DirectX.
    if flip_green:
        n[..., 1] *= -1.0
    out = np.empty((n.shape[0], n.shape[1], 4), dtype=np.uint8)
    out[..., :3] = np.clip(np.round((n * 0.5 + 0.5) * 255.0), 0, 255)
    out[..., 3] = 255
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", required=True, help="directory of textures (DDS dumps, PNG, JPG, TGA)")
    ap.add_argument("--out-normal", required=True, help="directory for <name>_NORMAL.DDS")
    ap.add_argument("--model", default="prs-eth/marigold-normals-v0-1")
    ap.add_argument("--steps", type=int, default=8, help="denoising steps (8 is the model's default)")
    ap.add_argument("--ensemble", type=int, default=1, help="ensemble runs per texture (more = sharper, slower)")
    ap.add_argument("--limit", type=int, default=0, help="only the first N textures (0 = all)")
    ap.add_argument("--no-flip-green", action="store_true", help="keep OpenGL-style green")
    ap.add_argument("--preview", default="", help="optional directory for PNG previews")
    ap.add_argument("--device", default="cuda")
    args = ap.parse_args()

    src_dir = pathlib.Path(args.input)
    files = sorted(p for p in src_dir.iterdir()
                   if p.suffix.lower() in (".dds", ".png", ".jpg", ".jpeg", ".tga", ".bmp"))
    if args.limit:
        files = files[:args.limit]
    if not files:
        print(f"no textures in {src_dir}", file=sys.stderr)
        return 2

    pathlib.Path(args.out_normal).mkdir(parents=True, exist_ok=True)
    if args.preview:
        pathlib.Path(args.preview).mkdir(parents=True, exist_ok=True)

    device = "cuda" if torch.cuda.is_available() and args.device == "cuda" else "cpu"
    dtype = torch.float16 if device == "cuda" else torch.float32

    from diffusers import MarigoldNormalsPipeline
    pipe = MarigoldNormalsPipeline.from_pretrained(args.model, torch_dtype=dtype).to(device)

    done = 0
    for path in files:
        name = path.stem
        try:
            image = bake.load_dds(str(path)) if path.suffix.lower() == ".dds" else Image.open(path).convert("RGB")
        except Exception as exc:                   # noqa: BLE001 - one bad texture must not stop a batch
            print(f"  skip {path.name}: {exc}", file=sys.stderr)
            continue

        output = pipe(image, num_inference_steps=args.steps, ensemble_size=args.ensemble)
        prediction = np.asarray(output.prediction, dtype=np.float32)
        if prediction.ndim == 4:                   # (batch, h, w, 3)
            prediction = prediction[0]

        # The pipeline works at its own resolution; bring the field back to the texture's.
        w, h = image.size
        if prediction.shape[0] != h or prediction.shape[1] != w:
            prediction = np.stack([
                np.asarray(Image.fromarray(prediction[..., c].astype(np.float32), "F").resize((w, h), Image.BILINEAR))
                for c in range(3)
            ], axis=-1)

        rgba = normals_to_rgba(prediction, flip_green=not args.no_flip_green)
        bake.write_dds_rgba(str(pathlib.Path(args.out_normal) / f"{name}_NORMAL.DDS"), rgba.tobytes(), w, h)

        if args.preview:
            Image.fromarray(rgba[..., :3], "RGB").save(pathlib.Path(args.preview) / f"{name}_normal.png")

        done += 1
        print(f"  {done}/{len(files)}: {name}", flush=True)

    print(f"gotowe: {done} normalnych -> {args.out_normal}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
