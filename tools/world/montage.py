#!/usr/bin/env python3
"""W10: side-by-side montage of lab frames, main (top row) vs world preview (bottom row), same shot indices.
Usage: python montage.py <main_run_dir> <world_run_dir> <out.png> [idx,idx,...] [--w 640]
Default indices 6,10,14,18,22 (wide A, B, C, D views of the case schedule). Needs Pillow (no -I)."""
import sys
from pathlib import Path
from PIL import Image

main_dir, world_dir, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
idx = [int(x) for x in sys.argv[4].split(",")] if len(sys.argv) > 4 and not sys.argv[4].startswith("--") else [6, 10, 14, 18, 22]
w = 640
if "--w" in sys.argv:
    w = int(sys.argv[sys.argv.index("--w") + 1])
rows = []
for d in (main_dir, world_dir):
    ims = []
    for i in idx:
        p = d / "shots" / ("shot-%03d.png" % i)
        im = Image.open(p).convert("RGB") if p.exists() else Image.new("RGB", (1280, 720), (60, 0, 60))
        ims.append(im.resize((w, int(im.height * w / im.width))))
    rows.append(ims)
h = rows[0][0].height
sheet = Image.new("RGB", (w * len(idx), h * 2))
for r, ims in enumerate(rows):
    for c, im in enumerate(ims):
        sheet.paste(im, (c * w, r * h))
sheet.save(out)
print(out, sheet.size)
