#!/usr/bin/env python3
"""Stage the isolated gear preview (previews/gear-v0.103.0): main v0.103.0 exe + main archives (hardlinks) + a patch-0 that is
main's patch-0 plus the converted gear payload and an ItemDisplayInfo.dbc with the display edits.  The main install is only read.

  stage_gear_preview.py --build analysis/asset-coverage/gear/r1-build --build .../r3-build [--replace]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]            # .../AzerothSurvivors
WORKSPACE = PROJECT.parent           # .../Azeroth Survivors
sys.path.insert(0, str(PROJECT / "tools" / "installer"))
from asinstall.dbc import Dbc  # noqa: E402

INSPECT = PROJECT / "tools" / "mpq-inspect.exe"
PACKER = PROJECT / "tools" / "pack-mpq.py"
MAIN_EXE_SHA = "6382e9ae654295293b82d48fb07a1aa999132775bda10c6c6f80d973d2c1ddfb"
DBC_NAME = "DBFilesClient\\ItemDisplayInfo.dbc"


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f"{cmd[:3]} failed: {r.stdout[-300:]} {r.stderr[-300:]}")
    return r.stdout


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", type=Path, action="append", required=True, help="gear_batch output dir (payload/, display-edits.json)")
    ap.add_argument("--name", default="gear-v0.103.0")
    ap.add_argument("--replace", action="store_true", help="delete and rebuild an existing preview this tool made")
    a = ap.parse_args()
    root = WORKSPACE / "previews" / a.name
    if root.exists():
        marker = root / "manifest.json"
        if not (a.replace and marker.is_file() and json.loads(marker.read_text(encoding="utf8")).get("tool") == "stage_gear_preview"):
            raise SystemExit(f"{root} exists; use --replace only for a preview this tool staged")
        shutil.rmtree(root)
    main_exe = PROJECT / "AzerothSurvivors.exe"
    if sha256(main_exe) != MAIN_EXE_SHA:
        raise SystemExit("main exe hash differs from the pinned v0.103.0 hash")
    main_data = PROJECT / "Data"
    tmp = WORKSPACE / "previews" / f".{a.name}-stage"
    if tmp.exists():
        shutil.rmtree(tmp)
    (tmp / "Data").mkdir(parents=True)
    for d in ("config", "saves", "diagnostics"):
        (tmp / d).mkdir()
    # 1. main patch-0 contents (mace + spells + base DBC) -> merge dir
    merge = tmp / "_merge"
    listing = run([str(INSPECT), str(main_data / "patch-0.MPQ"), "list"])
    names = [l.split(None, 1)[1].strip() for l in listing.splitlines() if l.strip() and l.split(None, 1)[0].isdigit() and len(l.split(None, 1)) == 2 and "." in l.split(None, 1)[1]]
    for n in names:
        dest = merge / Path(n.replace("\\", "/"))
        dest.parent.mkdir(parents=True, exist_ok=True)
        run([str(INSPECT), str(main_data / "patch-0.MPQ"), "extract", n, str(dest)])
    base_dbc = merge / "DBFilesClient" / "ItemDisplayInfo.dbc"
    # 2. gear payloads
    report = {"builds": [], "edits": []}
    for b in a.build:
        b = b.resolve()
        for f in (b / "payload").rglob("*"):
            if f.is_file():
                rel = f.relative_to(b / "payload")
                dest = merge / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dest)
        edits = json.loads((b / "display-edits.json").read_text(encoding="utf8"))
        rep = json.loads((b / "gear-report.json").read_text(encoding="utf8"))["summary"]
        report["builds"].append({"dir": str(b), **rep})
        report["edits"].extend(edits)
    # 3. DBC edits on top of main's (already mace-patched) ItemDisplayInfo
    dbc = Dbc(base_dbc.read_bytes(), "ItemDisplayInfo.dbc")
    for e in report["edits"]:
        col = e.get("column", 1)
        before = dbc.get_string(e["display"], col)
        if not before:
            raise SystemExit(f"display {e['display']} column {col} is empty; refusing to guess")
        e["before"] = before
        dbc.set_string(e["display"], col, e["new_basename"])
    base_dbc.write_bytes(dbc.tobytes())
    # 4. pack the merged patch-0
    out0 = tmp / "Data" / "patch-0.MPQ"
    run([sys.executable, str(PACKER), str(merge), str(out0)])
    shutil.rmtree(merge)
    # 5. exe copy, hardlinked archives, config, saves
    shutil.copy2(main_exe, tmp / "AzerothSurvivors.exe")
    for p in sorted(main_data.glob("*.MPQ")):
        if p.name.lower() == "patch-0.mpq":
            continue
        os.link(p, tmp / "Data" / p.name)
    cfg = (WORKSPACE / "previews" / "characters-v0.103.0" / "config" / "config.toml")
    shutil.copy2(cfg, tmp / "config" / "config.toml")
    src_saves = WORKSPACE / "previews" / "characters-v0.103.0" / "saves"
    if src_saves.is_dir():
        shutil.copytree(src_saves, tmp / "saves", dirs_exist_ok=True)
    (tmp / "Play Gear Preview.cmd").write_text(
        (WORKSPACE / "previews" / "characters-v0.103.0" / "Play Characters Preview.cmd").read_text(encoding="utf8"), encoding="utf8")
    manifest = {"tool": "stage_gear_preview", "preview": a.name, "exe_sha256": MAIN_EXE_SHA, "patch0_sha256": sha256(out0),
                "patch0_bytes": out0.stat().st_size, "display_edits": report["edits"], "builds": report["builds"],
                "note": "Retail gear on native displays; main archives are read-only hardlinks; patch-0 is a separate merged copy."}
    (tmp / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf8")
    (tmp / "README.md").write_text(f"# Gear preview {a.name}\n\nMain v0.103.0 with converted Retail weapons/shields on native displays (see manifest.json). "
                                   "Isolated config, saves, diagnostics. Data/patch-1..8 and patch.MPQ are hardlinks: do not modify.\n", encoding="utf8")
    os.rename(tmp, root)
    print(json.dumps({"preview": str(root), "patch0_bytes": (root / "Data" / "patch-0.MPQ").stat().st_size, "edits": len(report["edits"])}, indent=1))


if __name__ == "__main__":
    main()
