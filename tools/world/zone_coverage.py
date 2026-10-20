#!/usr/bin/env python3
"""W10: per playable map, how much of what the ADTs place is covered by VanillaHD Patch-B replacements."""
import json, re
from pathlib import Path

OUT = Path(r"./analysis/world")
inv = json.load(open(OUT / "world-inventory.json"))
pb = json.load(open(OUT / "patchb-world-files.json"))
pbm2 = {r["path"]: r for r in pb if r["ext"] == "m2" and r["in_game"] and r.get("magic") == "MD20" and r.get("ver") == 256}
pbwmo = {r["path"]: r for r in pb if r["ext"] == "wmo" and r["in_game"]}
pbblp = {r["path"]: r for r in pb if r["ext"] == "blp" and r["in_game"]}


def norm(p):
    p = p.lower()
    return p[:-4] + ".m2" if p.endswith((".mdx", ".mdl")) else p


rows = {}
for m, r in inv.items():
    dd = [(norm(p), n) for p, n in r["doodads"]]
    tot = sum(n for _, n in dd)
    hit = [(p, n) for p, n in dd if p in pbm2]
    ww = [(p.lower(), n) for p, n in r["wmos"]]
    whit = [(p, n) for p, n in ww if p in pbwmo]
    rows[m] = {
        "doodad_models": len(dd), "doodad_models_hd_available": len(hit),
        "doodad_adt_refs": tot, "doodad_adt_refs_hd": sum(n for _, n in hit),
        "wmo_models": len(ww), "wmo_models_hd_available": len(whit),
        "wmo_hd": [p for p, _ in whit],
        "tilesets": r["tilesets"], "tilesets_hd": r["tilesets_hd"],
    }
    print(m, {k: v for k, v in rows[m].items() if k != "wmo_hd"})
json.dump(rows, open(OUT / "zone-coverage.json", "w"), indent=1)
print("Patch-B replacements that match game paths: m2", len(pbm2), "wmo", len(pbwmo), "blp", len(pbblp))
