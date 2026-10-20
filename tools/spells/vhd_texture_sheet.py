"""Pixel diff native vs same-name VanillaHD textures + contact sheets (RGB on checker, alpha shown separately) for viewing."""
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(r".")
A = ROOT / "analysis/spells"
BS = chr(92)
cmp_ = json.load(open(A / "vhd-compare.json"))["textures"]
V = A / "vanillahd"
idx = {str(p.relative_to(V)).replace("\\", "/").lower(): p for p in V.rglob("*.blp") if ".git" not in p.parts}


def on_checker(im, s=96):
    im = im.convert("RGBA").resize((s, s), Image.LANCZOS)
    bg = Image.new("RGBA", (s, s), (40, 40, 48, 255))
    d = ImageDraw.Draw(bg)
    for y in range(0, s, 12):
        for x in range(0, s, 12):
            if (x // 12 + y // 12) % 2:
                d.rectangle((x, y, x + 11, y + 11), fill=(70, 70, 80, 255))
    return Image.alpha_composite(bg, im).convert("RGB")


rows = []
for t, r in cmp_.items():
    if not r.get("vhd") or r.get("identical"):
        continue
    ni = Image.open(A / "baseline/blp" / t.replace(BS, "__")).convert("RGBA")
    vi = Image.open(idx[t.replace(BS, "/")]).convert("RGBA")
    s = 64
    a = np.asarray(ni.resize((s, s), Image.BOX), np.float32)
    b = np.asarray(vi.resize((s, s), Image.BOX), np.float32)
    ea = a[..., :3] * a[..., 3:] / 255
    eb = b[..., :3] * b[..., 3:] / 255
    rows.append((float(np.abs(ea - eb).mean()), t, ni, vi))
rows.sort(key=lambda x: -x[0])
json.dump([(round(d, 2), t) for d, t, _, _ in rows], open(A / "vhd-texture-diff.json", "w"), indent=0)
print("count", len(rows), "diff>8:", sum(1 for r in rows if r[0] > 8), "diff>3:", sum(1 for r in rows if r[0] > 3))
per = 24
for si in range(0, min(len(rows), 72), per):
    chunk = rows[si:si + per]
    cols = 4
    W = cols * (96 * 2 + 12 + 150)
    sheet = Image.new("RGB", (W, ((len(chunk) + cols - 1) // cols) * 112), (20, 20, 24))
    dr = ImageDraw.Draw(sheet)
    for i, (d, t, ni, vi) in enumerate(chunk):
        x = (i % cols) * (96 * 2 + 12 + 150)
        y = (i // cols) * 112
        sheet.paste(on_checker(ni), (x, y))
        sheet.paste(on_checker(vi), (x + 100, y))
        dr.text((x + 202, y), f"{d:.1f}\n{t.split(BS)[-1][:20]}\n{ni.size[0]}->{vi.size[0]}", fill=(220, 220, 220))
    sheet.save(A / f"vhd-sheet-{si // per}.png")
print("sheets written")
