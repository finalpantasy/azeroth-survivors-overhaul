#!/usr/bin/env python3
"""W10: generate the as-recipe/1 recipe `hd-world-community` from the payload report (build_world_overlay.py).

Every output is a pinned VanillaHD/Patch-B file (git commit a112a137...), verified by sha256 by the installer's
`user-source` step; nothing is bundled. Writes tools/world/recipes/hd-world-community.json (the installer owner
copies it into tools/installer/recipes/) and validates it with the installer's own validator.
Usage: python -I make_world_recipe.py [--report <payload-report.json>] [--out <dir>]
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
COMMIT = "a112a137f3183d53cdd4e88c94f9c81fbeaa294a"
EXE = ["6382e9ae654295293b82d48fb07a1aa999132775bda10c6c6f80d973d2c1ddfb"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", default=str(ROOT / "analysis" / "world" / "payload-world-hd-report.json"))
    ap.add_argument("--out", default=str(HERE / "recipes"))
    ap.add_argument("--status", default="experimental")
    a = ap.parse_args()
    rep = json.load(open(a.report))
    files, outputs = [], []
    for f in sorted(rep["manifest"], key=lambda f: f["path"].lower()):
        src = f["src"].replace(BS, "/")
        name = "Patch-B/" + src
        origin = "https://raw.githubusercontent.com/VanillaHD/Patch-B/%s/%s" % (COMMIT, src.replace(" ", "%20"))
        files.append({"name": name, "bytes": f["bytes"], "sha256": f["sha256"], "origin": origin})
        outputs.append({"slot": "patch-1.MPQ", "path": f["path"], "from": "src:" + name, "sha256": f["sha256"]})
    doc = {
        "schema": "as-recipe/1", "id": "hd-world-community", "version": "0.1.0",
        "title": "HD world: doodad models, doodad/WMO textures (VanillaHD Patch-B)",
        "description": "Replaces %d doodad models and %d WMO files with their VanillaHD versions and adds %d textures, as same-name overlays in "
                       "Data/patch-1.MPQ (shared with hd-terrain-community). Only files whose game path already exists are replaced; models are "
                       "classic MD20 v256 and WMOs v17 (the engine's native formats). The files are NOT from your WoW install: you obtain the pinned "
                       "files yourself and the installer verifies every byte. Visual acceptance is per the world lab report; status stays "
                       "experimental until accepted." % (
                           sum(1 for f in rep["manifest"] if f["kind"] == "m2"),
                           sum(1 for f in rep["manifest"] if f["kind"].startswith("wmo")),
                           sum(1 for f in rep["manifest"] if f["kind"].startswith("blp"))),
        "status": a.status,
        "requires": {"game": {"exe_sha256": EXE, "versions": ["0.103.0"]}, "tools": ["mpq-packer"], "disk_mb": 1500},
        "sources": {"vanillahd": {
            "kind": "user-supplied",
            "instructions": "Get the pinned community files into one folder laid out as <folder>/Patch-B/<path> (VanillaHD/Patch-B at commit "
                            + COMMIT + "). Easiest: python tools/fetch_community_sources.py --recipe hd-world-community --dest <folder> --yes "
                            "(an explicit, separate download of only these hash-pinned files). The same folder can serve hd-terrain-community. "
                            "Then pass --source vanillahd=<folder>. The repository states no license; review it before use. The installer never "
                            "redistributes these files.",
            "files": files}},
        "steps": [{"id": "src", "op": "user-source", "source": "vanillahd"}],
        "outputs": {"mpq_files": outputs},
        "acceptance": [{"check": "mpq-has", "slot": "patch-1.MPQ", "path": o["path"], "sha256": o["sha256"]}
                       for o in outputs if o["path"].lower().endswith((".m2", ".wmo"))],
        "provenance": {"repo": "VanillaHD/Patch-B", "commit": COMMIT, "commit_date": "2025-08-25",
                       "archive_zip_sha256_local_copy": "918f70f0d157861e0912c333da75538cad1db96f0d8af94027c551f80b0d8098",
                       "selection_tool": "tools/world/build_world_overlay.py", "note": "pinned community files; no license in the repository"},
    }
    validate_recipe(doc, None, load_registry())
    out = Path(a.out)
    out.mkdir(exist_ok=True)
    p = out / "hd-world-community.json"
    p.write_text(json.dumps(doc, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    print("wrote", p, p.stat().st_size, "bytes;", len(files), "files")


if __name__ == "__main__":
    main()
