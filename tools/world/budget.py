#!/usr/bin/env python3
"""W10: geometry/texture budget old vs new for the world payload, weighted by ADT placements.

Per map: placed doodad vertices (replaced models only) old -> new, per-ADT mean/max, texture bytes in the payload.
Uses analysis/world/adt-cache (inventory_world.py), patchb-vs-native.json (patchb_quality.py) and the payload report.
"""
import json, struct, sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from inventory_world import parse_adt, CACHE  # noqa: E402

W = Path(r"./analysis/world")
q = {e["path"]: e for e in json.load(open(W / "patchb-vs-native.json")) if e["ext"] == "m2" and e["new"] and e["old"]}
rep = json.load(open(W / "payload-world-hd-report.json"))
chosen = {f["path"].lower() for f in rep["manifest"] if f["kind"] == "m2"}


def norm(p):
    p = p.lower()
    return p[:-4] + ".m2" if p.endswith((".mdx", ".mdl")) else p


per_map = defaultdict(list)
for adt in sorted(CACHE.rglob("*.adt")):
    mp = adt.relative_to(CACHE).parts[2].lower()
    r = parse_adt(adt.read_bytes())
    old = new = n = tot = 0
    for name, c in r["m2_by_name"].items():
        k = norm(name)
        tot += c
        if k in chosen and k in q:
            e = q[k]
            old += c * e["old"]["verts"]; new += c * e["new"]["verts"]; n += c
    per_map[mp].append((adt.name, tot, n, old, new))
out = {}
for mp, rows in per_map.items():
    rows = [r for r in rows if r[1]]
    out[mp] = {
        "adts_with_doodads": len(rows),
        "placements": sum(r[1] for r in rows), "placements_replaced": sum(r[2] for r in rows),
        "replaced_verts_old": sum(r[3] for r in rows), "replaced_verts_new": sum(r[4] for r in rows),
        "per_adt_extra_verts_mean": round(sum(r[4] - r[3] for r in rows) / max(1, len(rows))),
        "per_adt_extra_verts_max": max((r[4] - r[3] for r in rows), default=0),
        "worst_adt": max(rows, key=lambda r: r[4] - r[3])[0] if rows else None,
    }
    print(mp, out[mp])
kinds = defaultdict(int)
for f in rep["manifest"]:
    kinds[f["kind"]] += f["bytes"]
out["_payload_bytes_by_kind"] = dict(kinds)
json.dump(out, open(W / "budget.json", "w"), indent=1)
