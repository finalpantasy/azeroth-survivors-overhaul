"""Pixel difference between native BLPs and Retail/Forever same-FDID BLPs (same size only), via Pillow."""
import io
import json
import sys
from pathlib import Path

from PIL import Image, ImageChops

A = Path(r"./analysis/spells")
cmp_ = json.load(open(A / "casc-legacy-compare.json"))
rows = []
for name, r in cmp_.items():
    nb = A / "baseline/blp" / name.replace("/", "__")
    try:
        ni = Image.open(nb).convert("RGBA")
    except Exception as e:  # noqa: BLE001
        rows.append((name, "native-decode-fail", str(e)))
        continue
    for prof in ("retail", "forever"):
        f = A / "casc" / f"legacy-{prof}" / f"{r['fdid']}.bin"
        if not f.exists():
            continue
        try:
            ri = Image.open(f).convert("RGBA")
        except Exception as e:  # noqa: BLE001
            rows.append((name, prof, "decode-fail " + str(e)))
            continue
        if ri.size != ni.size:
            rows.append((name, prof, f"size {ni.size}->{ri.size}"))
            continue
        d = ImageChops.difference(ni, ri)
        st = sum(sum(p) for p in d.getdata()) / (ni.size[0] * ni.size[1] * 4)
        rows.append((name, prof, round(st, 2)))
json.dump(rows, open(A / "pixel-diff.json", "w"), indent=0)
sizes = [x for x in rows if isinstance(x[2], str) and x[2].startswith("size")]
nums = [x for x in rows if isinstance(x[2], float) or isinstance(x[2], int)]
print("total", len(rows), "size-changed", len(sizes), "same-size", len(nums), "same-size zero diff", sum(1 for x in nums if x[2] == 0))
big = sorted(nums, key=lambda x: -x[2])[:25]
for x in big:
    print(x)
print("fail", [x for x in rows if isinstance(x[2], str) and "fail" in x[2]][:5])
