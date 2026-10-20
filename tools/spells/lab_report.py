"""Summarise the latest lab-spells-<class>-* run per class: combat happened?, picks, effect draw counts, defects; contact sheet of combat frames.
Usage: lab_report.py [class ...]   -> analysis/spells/lab-report.json + analysis/spells/sheets/<class>.png
"""
import glob, json, re, subprocess, sys
from pathlib import Path
ROOT = Path(r".")
RT = ROOT / "analysis/runtime"
OUT = ROOT / "analysis/spells"
(OUT / "sheets").mkdir(exist_ok=True)
CLASSES = sys.argv[1:] or "mage warlock priest shaman druid paladin hunter rogue warrior".split()
ANSI = re.compile(r"\x1b\[[0-9;]*m")
rep = {}
for c in CLASSES:
    runs = sorted(glob.glob(str(RT / f"lab-spells-{c}-2*")))
    best = None
    for d in reversed(runs):
        so = Path(d, "stdout.log")
        if not so.exists():
            continue
        ev = [ln for ln in so.read_text(errors="replace").splitlines() if ln.startswith("rogue:")]
        started = any('"type":"start"' in e for e in ev)
        if started or best is None:
            best = (d, ev, started)
        if started:
            break
    if not best:
        rep[c] = {"status": "no run"}
        continue
    d, ev, started = best
    res = json.loads(Path(d, "result.json").read_text()) if Path(d, "result.json").exists() else {}
    err = ANSI.sub("", Path(d, "stderr.log").read_text(errors="replace"))
    tr = [int(m.group(1)) for m in re.finditer(r"trace: (\d+) draws", err)]
    idx = [int(m.group(1)) for m in re.finditer(r"trace: \d+ draws, \d+ merged, (\d+) indices", err)]
    picks = []
    for e in ev:
        if '"choices"' in e:
            try:
                picks.append([x["name"] for x in json.loads(e[6:])["choices"]])
            except Exception:
                pass
    defeat = next((json.loads(e[6:]) for e in ev if '"defeat"' in e), {})
    bad = [ln[:160] for ln in err.splitlines() if re.search(r"failed to load|cannot open|not found.*(blp|m2)|missing texture", ln, re.I)][:5]
    rep[c] = {"run": Path(d).name, "status": res.get("status"), "combat_started": started, "frames": res.get("frames"),
              "defects_hard": (res.get("defects") or {}).get("hard"), "advisory": res.get("advisory"), "levelup_choices": picks[:3],
              "defeat": {k: defeat.get(k) for k in ("time", "level", "kills", "damage")} if defeat else None,
              "effect_draws_max": max(tr) if tr else None, "effect_draws_avg": round(sum(tr) / len(tr), 1) if tr else None,
              "effect_indices_max": max(idx) if idx else None, "load_problems": bad}
    # combat frames = before the death panel (dark)
    from PIL import Image
    fs = sorted(glob.glob(d + "/shots/*.png"))
    small = [list(Image.open(f).convert("L").resize((48, 27)).getdata()) for f in fs]
    cut = len(fs)
    for i in range(len(fs) - 2, 0, -1):          # start of the trailing run of near-identical frames (death panel)
        if sum(abs(x - y) for x, y in zip(small[i], small[-1])) / 1296 < 2.0:
            cut = i
        else:
            break
    first = max(0, cut - 12)
    subprocess.run([sys.executable, str(ROOT / "tools/spells/sheet.py"), d, str(OUT / "sheets" / f"{c}.png"), str(first), "12"], check=True, capture_output=True)
    rep[c]["sheet_frames"] = [first, cut]
json.dump(rep, open(OUT / "lab-report.json", "w"), indent=1)
for c, r in rep.items():
    print(c, {k: r.get(k) for k in ("status", "combat_started", "effect_draws_max", "effect_draws_avg", "defeat", "defects_hard", "load_problems")})
