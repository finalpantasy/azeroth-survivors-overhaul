#!/usr/bin/env python3
"""Find higher-resolution look-alikes for the native spell textures.

Candidates: Retail/Forever BLP features (casc_features.py output) plus VanillaHD Patch-A spell BLPs (local sparse checkout).
Feature: 16x16 RGBA. Score = mean abs diff of emission (rgb*a) plus alpha when both sides carry alpha, in 0..255 units.
Writes analysis/spells/matches.json: per native texture the best HD candidates (res >= 2x area, same aspect), with scores.
"""
import io
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(r".")
A = ROOT / "analysis/spells"
BS = chr(92)


def feat_of(im):
    return np.frombuffer(im.convert("RGBA").resize((16, 16), Image.BOX).tobytes(), np.uint8).reshape(16, 16, 4).astype(np.float32)


def emission(f):
    return f[..., :3] * (f[..., 3:4] / 255.0)


def score(nf, nalpha, cf, calpha):
    e = np.abs(emission(nf) - emission(cf)).mean()
    if nalpha and calpha:
        e = 0.7 * e + 0.3 * np.abs(nf[..., 3] - cf[..., 3]).mean()
    return float(e)


def main():
    base = json.load(open(A / "baseline.json"))
    native = {}
    for t, h in base["textures"].items():
        p = A / "baseline/blp" / t.replace(BS, "__")
        im = Image.open(p)
        native[t] = {"f": feat_of(im), "w": h["w"], "h": h["h"], "alpha": h["alpha_depth"] > 0, "bytes": h["bytes"]}
    cands = []
    lf = {}
    for ln in open(ROOT / "analysis/asset-coverage/work/listfile-filtered.csv", encoding="utf8"):
        i, _, p = ln.rstrip("\n").partition(";")
        lf[int(i)] = p
    for prof in ("retail", "forever"):
        f = A / "casc" / f"features-{prof}.json"
        if not f.exists():
            continue
        d = json.load(open(f))["results"]
        for fid, r in d.items():
            if "f" in r:
                cands.append({"src": prof, "fdid": int(fid), "name": lf.get(int(fid), ""), "w": r["w"], "h": r["h"], "alpha": r["alpha"] > 0, "bytes": r["bytes"],
                              "f": np.frombuffer(bytes.fromhex(r["f"]), np.uint8).reshape(16, 16, 4).astype(np.float32)})
    V = A / "vanillahd"
    for p in V.rglob("*.blp"):
        if ".git" in p.parts:
            continue
        try:
            im = Image.open(p)
            hdr = p.read_bytes()[:20]
            cands.append({"src": "vanillahd", "fdid": 0, "name": str(p.relative_to(V)).replace("\\", "/").lower(), "w": im.size[0], "h": im.size[1],
                          "alpha": hdr[10] > 0, "bytes": p.stat().st_size, "f": feat_of(im)})
        except Exception:  # noqa: BLE001
            pass
    print("native", len(native), "candidates", len(cands))
    out = {}
    for t, n in native.items():
        asp = n["w"] / n["h"]
        rows = []
        for c in cands:
            if c["w"] * c["h"] < 2 * n["w"] * n["h"]:
                continue
            if abs(c["w"] / c["h"] - asp) > 0.01:
                continue
            rows.append((score(n["f"], n["alpha"], c["f"], c["alpha"]), c))
        rows.sort(key=lambda x: x[0])
        out[t] = {"native": {k: n[k] for k in ("w", "h", "alpha", "bytes")},
                  "best": [{"score": round(s, 2), "src": c["src"], "fdid": c["fdid"], "name": c["name"], "w": c["w"], "h": c["h"], "alpha": c["alpha"], "bytes": c["bytes"]} for s, c in rows[:5]]}
    json.dump(out, open(A / "matches.json", "w"), indent=1)
    import collections
    buckets = collections.Counter()
    for t, r in out.items():
        s = r["best"][0]["score"] if r["best"] else 999
        buckets["<3" if s < 3 else "<6" if s < 6 else "<10" if s < 10 else "<16" if s < 16 else ">=16"] += 1
    print(buckets)


if __name__ == "__main__":
    main()
