"""Compare native baseline spell BLPs with the same-FDID Retail/Forever files (analysis/spells/casc/legacy-*)."""
import json
import sys
from pathlib import Path

ROOT = Path(r".")
A = ROOT / "analysis/spells"
sys.path.insert(0, str(ROOT / "tools/spells"))
from extract_baseline import blp_header  # noqa: E402

BS = chr(92)
fd = json.load(open(A / "legacy-fdids.json"))
out = {}
for name, fid in fd.items():
    nb = (A / "baseline/blp" / name.replace("/", "__")).read_bytes()
    row = {"fdid": fid, "native": blp_header(nb)}
    for prof in ("retail", "forever"):
        f = A / "casc" / f"legacy-{prof}" / f"{fid}.bin"
        if f.exists():
            b = f.read_bytes()
            row[prof] = blp_header(b)
            row[prof]["identical"] = b == nb
    out[name] = row
json.dump(out, open(A / "casc-legacy-compare.json", "w"), indent=1)
for prof in ("retail", "forever"):
    pres = [r for r in out.values() if prof in r]
    ident = [r for r in pres if r[prof]["identical"]]
    bigger = [n for n, r in out.items() if prof in r and r[prof].get("w", 0) * r[prof].get("h", 0) > r["native"]["w"] * r["native"]["h"]]
    diff = [n for n, r in out.items() if prof in r and not r[prof]["identical"]]
    print(prof, "present", len(pres), "identical", len(ident), "different", len(diff), "higher-res", len(bigger))
    for n in sorted(bigger):
        r = out[n]
        print("   up", n, f"{r['native']['w']}x{r['native']['h']} -> {r[prof]['w']}x{r[prof]['h']}")
    for n in sorted(set(diff) - set(bigger))[:40]:
        r = out[n]
        print("   diff-same-or-lower", n, f"{r['native']['w']}x{r['native']['h']} {r['native']['bytes']} -> {r[prof].get('w')}x{r[prof].get('h')} {r[prof].get('bytes')}")
