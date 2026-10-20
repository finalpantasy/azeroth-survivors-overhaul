#!/usr/bin/env python3
"""Check a converter output dir offline: decode its BLPs with the engine's own reader (mpq-inspect blp, from the
output MPQ), read the WRITTEN model bytes back, render key animations, and measure per-sequence vertex excursion
(exploding-vertex check: max distance of any skinned vertex from the bind-pose bounding-box centre, over 8 samples
per sequence, relative to the bind-pose radius). Writes preview-<name>.png and preview-check.json into the out dir.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import m2ir  # noqa: E402
import render_preview as R  # noqa: E402

MPQ_INSPECT = HERE.parent / "mpq-inspect.exe"
NAMES = {0: "stand", 4: "walk", 5: "run", 16: "attack", 17: "attack1H", 1: "death", 9: "wound", 51: "cast", 52: "castdir"}


def decode_blp(mpq: Path, internal: str, png: Path):
    r = subprocess.run([str(MPQ_INSPECT), str(mpq), "blp", internal, str(png)], capture_output=True, text=True)
    if r.returncode != 0:
        return None, (r.stdout + r.stderr).strip()[-200:]
    return np.array(Image.open(png).convert("RGBA")), (r.stdout.strip() or "decoded")


def check(out: Path, model_bytes: bytes | None = None, label="converted", skin_choice=0, size=260):
    rep = json.loads((out / "conversion-report.json").read_text(encoding="utf8"))
    internal = rep["output"]["model_path"]
    mpq = out / rep["output"]["mpq"]
    if model_bytes is None:
        model_bytes = (out / "payload" / Path(internal.replace("\\", "/"))).read_bytes()
    m = m2ir.read_v256(model_bytes, rep["recipe"])
    pngdir = out / "png"
    pngdir.mkdir(exist_ok=True)
    decoded, tex_by_path = {}, {}
    for path in rep["output"]["textures"]:
        arr, msg = decode_blp(mpq, path, pngdir / (path.replace("\\", "_") + ".png"))
        decoded[path] = msg
        tex_by_path[path.lower()] = arr
    skins = [p for p in rep["output"]["textures"] if not any(p.lower() == (t["path"] or "").lower() for t in m.textures)]
    skin_arr = tex_by_path.get(skins[skin_choice].lower()) if skins else None
    btex = {}
    for i, b in enumerate(m.batches):
        ti = m.texture_lookup[b[8]] if b[8] < len(m.texture_lookup) else -1
        if ti < 0 or ti >= len(m.textures):
            continue
        t = m.textures[ti]
        if b[9] < len(m.texture_unit_lookup) and m.texture_unit_lookup[b[9]] not in (0, 1):
            continue  # env-map layer: skip in the preview
        btex[i] = tex_by_path.get((t["path"] or "").lower()) if t["type"] == 0 else skin_arr
    # bind pose reference
    nv = len(m.vertices) // 48
    bind = np.frombuffer(m.vertices, np.uint8).reshape(nv, 48)[:, :12].copy().view("<f4").reshape(nv, 3).astype(float)
    centre = (bind.max(0) + bind.min(0)) / 2
    rad = float(np.linalg.norm(bind - centre, axis=1).max())
    excursion = []
    for si, s in enumerate(m.sequences):
        worst = 0.0
        for k in range(8):
            p, _, _ = R.skinned_positions(m, si, s["duration"] * k / 7.0)
            if not np.isfinite(p).all():
                worst = float("inf")
                break
            worst = max(worst, float(np.linalg.norm(p - centre, axis=1).max()))
        excursion.append({"seq": si, "anim": s["id"], "variation": s["variation"], "max_radius_ratio": round(worst / rad, 3)})
    frames = []
    ids = [s["id"] for s in m.sequences]
    for aid in (0, 4, 5, 16, 17, 51, 1):
        if aid in ids:
            si = ids.index(aid)
            d = m.sequences[si]["duration"]
            frames.append((f"{NAMES.get(aid, aid)} {aid}", si, d * (0.95 if aid == 1 else 0.45)))
    sheet = out / f"preview-{label}.png"
    stats = R.contact_sheet(m, frames, btex, sheet, size=size)
    res = {"model_path": internal, "decoded_textures": decoded, "bind_radius": round(rad, 3),
           "bind_box": [bind.min(0).round(3).tolist(), bind.max(0).round(3).tolist()],
           "worst_excursion_ratio": max(e["max_radius_ratio"] for e in excursion) if excursion else 0,
           "excursion": excursion, "sheet": sheet.name, "stats": stats}
    (out / f"preview-check-{label}.json").write_text(json.dumps(res, indent=1) + "\n", encoding="utf8")
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("--skin", type=int, default=0)
    ap.add_argument("--size", type=int, default=260)
    a = ap.parse_args()
    r = check(a.out, skin_choice=a.skin, size=a.size)
    print(json.dumps({k: r[k] for k in ("decoded_textures", "bind_box", "worst_excursion_ratio", "sheet")}, indent=1))


if __name__ == "__main__":
    main()
