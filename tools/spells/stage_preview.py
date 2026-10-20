#!/usr/bin/env python3
"""Stage previews/spells-v0.103.0 (isolated; never touches the installed main game).

Data/ = hardlinks to the installed official archives (patch.MPQ, patch-1..8) + a NEW patch-0.MPQ built from the
currently installed patch-0 contents (12 spell payload files, Retail mace, ItemDisplayInfo) overlaid with the spells
payload tree (analysis/spells/out/tree); paths already present in the installed patch-0 win unless --override.
exe = hardlink of the installed v0.103.0 exe. Re-run with --force to rebuild patch-0 only (the folder is replaced).
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(r".")
PREVIEW = Path(r"previews/spells-v0.103.0")
A = ROOT / "analysis/spells"
INSPECT = str(ROOT / "tools/mpq-inspect.exe")
BS = chr(92)


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", default=str(A / "out/tree"))
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--override", action="store_true", help="payload wins over installed patch-0 paths")
    a = ap.parse_args()
    if PREVIEW.exists():
        if not a.force:
            raise SystemExit("preview exists; use --force to rebuild patch-0 only")
        for p in (PREVIEW / "Data" / "patch-0.MPQ", PREVIEW / "manifest.json"):
            if p.exists():
                p.unlink()
    (PREVIEW / "Data").mkdir(parents=True, exist_ok=True)
    for sub in ("config", "saves", "diagnostics"):
        (PREVIEW / sub).mkdir(exist_ok=True)
    # hardlinked official/overlay archives and exe
    for name in ["patch.MPQ", "patch-1.MPQ"] + [f"patch-{n}.MPQ" for n in range(2, 9)]:
        d = PREVIEW / "Data" / name
        if not d.exists():
            os.link(ROOT / "Data" / name, d)
    exe = PREVIEW / "AzerothSurvivors.exe"
    if not exe.exists():
        os.link(ROOT / "AzerothSurvivors.exe", exe)
    # merged tree
    work = PREVIEW / "diagnostics" / "patch0-tree"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    listing = subprocess.run([INSPECT, str(ROOT / "Data/patch-0.MPQ"), "list"], capture_output=True, text=True).stdout
    installed = []
    for ln in listing.splitlines():
        parts = ln.split(None, 1)
        if len(parts) == 2 and parts[0].isdigit():
            installed.append(parts[1].strip())
    for p in installed:
        dst = work / p.replace(BS, "/")
        dst.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([INSPECT, str(ROOT / "Data/patch-0.MPQ"), "extract", p, str(dst)], check=True, capture_output=True)
    have = {p.lower().replace("/", BS) for p in installed}
    added, skipped = [], []
    tree = Path(a.tree)
    for f in tree.rglob("*"):
        if not f.is_file():
            continue
        rel = str(f.relative_to(tree)).replace("/", BS)
        if rel.lower() in have and not a.override:
            skipped.append(rel)
            continue
        dst = work / rel.replace(BS, "/")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(f, dst)
        added.append(rel)
    out = PREVIEW / "Data" / "patch-0.MPQ"
    subprocess.run([sys.executable, str(ROOT / "tools/pack-mpq.py"), str(work), str(out)], check=True)
    man = {"preview": "spells-v0.103.0", "patch0": {"bytes": out.stat().st_size, "sha256": sha(out)}, "installed_patch0_files": len(installed),
           "added": len(added), "skipped_already_installed": skipped, "tree": str(tree)}
    (PREVIEW / "manifest.json").write_text(json.dumps(man, indent=1))
    (PREVIEW / "README.md").write_text("# spells-v0.103.0 preview\nIsolated spell-upgrade preview (worker W9). Data/patch-1..8 + patch.MPQ are hardlinks to the installed official archives: read-only. patch-0.MPQ = installed patch-0 + analysis/spells payload. Not the main install.\n")
    print(json.dumps({k: v for k, v in man.items() if k != "skipped_already_installed"}), "skipped", skipped)


if __name__ == "__main__":
    main()
