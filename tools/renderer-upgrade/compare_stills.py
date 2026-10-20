#!/usr/bin/env python3
"""Compare two lab still runs (baseline vs candidate): image metrics, frame times, and side-by-side crops.

  python compare_stills.py <base_run_dir> <cand_run_dir> --label fog [--out DIR]

World shots (not loading screens) are paired by closest content, since shot counts differ per run. World ROIs exclude the UI. Metrics per ROI, as the
median over matched shots: mean luma, mean saturation, RMS contrast, high-frequency detail (Laplacian
std) and mean |cand - base|. Run it on two baseline-class runs to get the noise floor. Frame times are
medians over world rows of fps.csv (entities > 34000, first 3 world rows skipped). Numbers only point at
where to look; the crops are what decide.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from pathlib import Path

import numpy as np
from PIL import Image

# 1280x720 lab frames. ROIs avoid the unit frames, minimap, chat and action bars.
ROIS = {"near": (240, 380, 1040, 560), "mid": (160, 200, 1100, 380), "far": (100, 90, 1000, 200)}


def load(p: Path) -> np.ndarray:
    return np.asarray(Image.open(p).convert("RGB"), dtype=np.float32) / 255.0


def metrics(img: np.ndarray, base: np.ndarray | None) -> dict:
    luma = img @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    sat = img.max(axis=2) - img.min(axis=2)
    lap = (4 * luma[1:-1, 1:-1] - luma[:-2, 1:-1] - luma[2:, 1:-1] - luma[1:-1, :-2] - luma[1:-1, 2:])
    m = {"luma": float(luma.mean()), "sat": float(sat.mean()), "contrast": float(luma.std()), "hf": float(lap.std())}
    if base is not None:
        m["absdiff"] = float(np.abs(img - base).mean())
    return m


def is_world(p: Path) -> bool:
    """Loading screens are pillar-boxed (black left edge); world frames are not."""
    img = np.asarray(Image.open(p).convert("L"), dtype=np.float32)
    return float(img[100:600, 0:30].mean()) > 6.0


def thumb(p: Path) -> np.ndarray:
    return np.asarray(Image.open(p).convert("L").crop((0, 80, 1080, 560)).resize((135, 60)), dtype=np.float32)


def pipe_warm(run: Path) -> str:
    """The game's pipeline pre-warm result: drained (seconds) or timed out (driver cache cold)."""
    import re
    text = re.sub(r"\[[0-9;]*m", "", (run / "stderr.log").read_text(encoding="utf-8", errors="replace"))
    m = re.search(r"pipeline warm: (\d+) rigs drained in ([0-9.]+)s", text)
    if m:
        return f"drained {m.group(1)} in {m.group(2)} s"
    m = re.search(r"pipeline warm: TIMED OUT with (\d+) pipelines pending", text)
    return f"TIMED OUT ({m.group(1)} pending)" if m else "no pipe_warm line"


def frame_times(run: Path) -> dict:
    rows = []
    with open(run / "fps.csv", encoding="utf-8") as f:
        lines = [l for l in f if not l.startswith("#")]
    for r in csv.DictReader(lines):
        try:
            if float(r["entities"]) > 34000:
                rows.append(r)
        except (ValueError, KeyError):
            pass
    rows = rows[3:]
    out = {"world_rows": len(rows), "pipe_warm": pipe_warm(run)}
    for k in ("mean_ms", "p95_ms", "cpu_ms", "main_ms", "gpu_ms", "gpu_opaque", "gpu_static", "gpu_transp", "gpu_post"):
        vals = [float(r[k]) for r in rows if r.get(k) not in (None, "")]
        out[k] = round(statistics.median(vals), 3) if vals else None
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("base", type=Path)
    ap.add_argument("cand", type=Path)
    ap.add_argument("--label", required=True)
    ap.add_argument("--out", type=Path, default=Path("./analysis/renderer-upgrade/compare/v3"))
    a = ap.parse_args()
    bs = [p for p in sorted((a.base / "shots").glob("shot-*.png")) if is_world(p)]
    cs = [p for p in sorted((a.cand / "shots").glob("shot-*.png")) if is_world(p)]
    if not bs or not cs:
        raise SystemExit("no world shots")
    # Shot counts and timing differ between runs, so pair each candidate shot with the baseline shot
    # whose downsampled world view is closest (same camera phase, same pulse of the HUD text).
    thumbs = {p: thumb(p) for p in bs}
    pairs = []
    for pc in cs:
        tc = thumb(pc)
        pb = min(bs, key=lambda p: float(np.abs(thumbs[p] - tc).mean()))
        pairs.append((pb, pc))
    per = {k: {"base": [], "cand": []} for k in ROIS}
    for pb, pc in pairs:
        b, c = load(pb), load(pc)
        for k, (x0, y0, x1, y1) in ROIS.items():
            per[k]["base"].append(metrics(b[y0:y1, x0:x1], None))
            per[k]["cand"].append(metrics(c[y0:y1, x0:x1], b[y0:y1, x0:x1]))
    summary = {}
    for k in ROIS:
        s = {}
        for key in ("luma", "sat", "contrast", "hf"):
            mb = statistics.median(m[key] for m in per[k]["base"])
            mc = statistics.median(m[key] for m in per[k]["cand"])
            s[key] = {"base": round(mb, 4), "cand": round(mc, 4), "delta_pct": round(100 * (mc - mb) / max(mb, 1e-6), 1)}
        s["absdiff"] = round(statistics.median(m["absdiff"] for m in per[k]["cand"]), 4)
        summary[k] = s
    res = {"label": a.label, "base": str(a.base), "cand": str(a.cand), "pairs": len(pairs), "rois": summary,
           "frame_times": {"base": frame_times(a.base), "cand": frame_times(a.cand)}}
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / f"{a.label}.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    # Visuals from the middle matched shot: full side by side, 2x ROI crops, and a x8 difference map.
    pb, pc = pairs[len(pairs) // 2]
    b, c = Image.open(pb).convert("RGB"), Image.open(pc).convert("RGB")
    full = Image.new("RGB", (b.width * 2, b.height))
    full.paste(b, (0, 0)); full.paste(c, (b.width, 0))
    full.save(a.out / f"{a.label}-full.png")
    for k, box in ROIS.items():
        w, h = box[2] - box[0], box[3] - box[1]
        crop = Image.new("RGB", (w * 2, h * 4))
        crop.paste(b.crop(box).resize((w * 2, h * 2), Image.NEAREST), (0, 0))
        crop.paste(c.crop(box).resize((w * 2, h * 2), Image.NEAREST), (0, h * 2))
        crop.save(a.out / f"{a.label}-{k}-2x.png")
    d = np.clip(np.abs(np.asarray(c, np.float32) - np.asarray(b, np.float32)) * 8, 0, 255).astype(np.uint8)
    Image.fromarray(d).save(a.out / f"{a.label}-diff-x8.png")
    print(json.dumps({"label": a.label, "pairs": len(pairs),
                      **{k: {m: v["delta_pct"] for m, v in s.items() if isinstance(v, dict)} | {"absdiff": s["absdiff"]}
                         for k, s in summary.items()},
                      "ms": {w: {k: res["frame_times"][w][k] for k in ("mean_ms", "main_ms", "gpu_ms", "pipe_warm")} for w in ("base", "cand")}}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
