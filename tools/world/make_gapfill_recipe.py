#!/usr/bin/env python3
"""W10: generate the as-recipe/1 recipe `hd-terrain-gapfill-retail` (31 ADT-referenced tilesets the game ships nowhere).

Source = the owner's own Retail 12.1.5.70448 (offline CASC); build pins are copied from the proven mace recipe.
Files come from analysis/world/payload-world-gapfill-report.json (hash of each export). Output -> Data/patch-1.MPQ
(shared with hd-terrain-community and hd-world-community). Nothing is bundled.
Usage: python -I make_gapfill_recipe.py [--out <dir>]
"""
import argparse, json, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
INSTALLER = ROOT / "tools" / "installer"
sys.path.insert(0, str(INSTALLER))
from asinstall.recipe import validate_recipe  # noqa: E402
from asinstall.steps import load_registry  # noqa: E402

BS = chr(92)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", default=str(ROOT / "analysis" / "world" / "payload-world-gapfill-report.json"))
    ap.add_argument("--out", default=str(HERE / "recipes"))
    ap.add_argument("--status", default="experimental")
    a = ap.parse_args()
    rep = json.load(open(a.report))
    mace = json.load(open(INSTALLER / "recipes" / "retail-mace-kultiras-silver.json"))
    src = mace["sources"]["retail"]
    files, outputs = [], []
    for f in sorted(rep, key=lambda f: f["path"]):
        name = "tileset-%d.blp" % f["fdid"]
        files.append({"file_data_id": f["fdid"], "name": name, "role": "tileset " + f["path"],
                      "bytes": f["bytes"], "sha256": f["sha256"]})
        outputs.append({"slot": "patch-1.MPQ", "path": f["path"], "from": "export:" + name, "sha256": f["sha256"]})
    doc = {
        "schema": "as-recipe/1", "id": "hd-terrain-gapfill-retail", "version": "0.1.0",
        "title": "Terrain gap-fill: %d tilesets the game does not ship (Retail)" % len(files),
        "description": "The game's ADTs reference %d tilesets that exist in no shipped archive (the terrain falls back to a "
                       "default texture there). This exports those exact tilesets from your own Retail %s install as "
                       "same-name BLPs in Data/patch-1.MPQ (shared with hd-terrain-community). Texture-only; no geometry, "
                       "no DBC. Status stays experimental until the world lab shows the affected tiles." % (len(files), src["version"]),
        "status": a.status,
        "requires": {"game": {"exe_sha256": mace["requires"]["game"]["exe_sha256"], "versions": ["0.103.0"]},
                     "tools": ["casc-export", "mpq-packer"], "disk_mb": 16},
        "sources": {"retail": {**{k: src[k] for k in ("kind", "product", "version", "build_number", "build_key", "cdn_key",
                                                       "locale", "build_config", "cdn_config")}, "files": files,
                               "note": "tilesets referenced by the game's ADTs and present in no shipped MPQ (analysis/world/zone-coverage.json)"}},
        "steps": [{"id": "export", "op": "casc-export", "source": "retail"}],
        "outputs": {"mpq_files": outputs},
        "acceptance": [{"check": "mpq-has", "slot": "patch-1.MPQ", "path": o["path"], "sha256": o["sha256"]} for o in outputs],
        "provenance": "Retail %s FileDataIDs listed per file; selection tool tools/world/inventory_world.py + zone_coverage.py; "
                      "probe evidence analysis/world/payload-world-gapfill-report.json." % src["version"],
    }
    validate_recipe(doc, None, load_registry())
    out = Path(a.out)
    out.mkdir(exist_ok=True)
    p = out / "hd-terrain-gapfill-retail.json"
    p.write_text(json.dumps(doc, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    print("wrote", p, p.stat().st_size, "bytes;", len(files), "files")


if __name__ == "__main__":
    main()
