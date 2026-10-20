#!/usr/bin/env python3
"""Stage a lab preview = hardlinked characters-lab Data chain + one overlay patch-9.MPQ packed from a payload dir.
  stage_overlay_preview.py <payload dir> <preview name>      (never touches previews/characters-v0.103.0)"""
import os, shutil, subprocess, sys
from pathlib import Path
R = Path(__file__).resolve().parents[2]
WS = R.parent
payload, name = Path(sys.argv[1]), sys.argv[2]
lab = WS / "previews" / "characters-lab-v0.103.0"
out = WS / "previews" / name
if out.exists():
    raise SystemExit(f"exists: {out}")
mpq = payload.parent / (payload.name + ".MPQ")
if mpq.exists():
    mpq.unlink()
subprocess.run([sys.executable, str(R / "tools/pack-mpq.py"), str(payload), str(mpq)], check=True)
(out / "Data").mkdir(parents=True)
for f in (lab / "Data").iterdir():
    if f.is_file():
        os.link(f, out / "Data" / f.name)
shutil.copy2(mpq, out / "Data" / "patch-9.MPQ")
shutil.copy2(lab / "AzerothSurvivors.exe", out / "AzerothSurvivors.exe")
shutil.copytree(lab / "config", out / "config")
shutil.copytree(lab / "saves", out / "saves")
print("staged", out, mpq.stat().st_size)
