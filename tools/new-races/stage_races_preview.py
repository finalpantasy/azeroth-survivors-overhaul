#!/usr/bin/env python3
"""Stage the isolated new-races preview: previews/races-v0.103.0 (or --name).

Pattern of analysis/renderer-rea/candidate-0.103.0/stage_preview.py: own copied config and saves, read-only
hardlinked shipped archives (patch.MPQ, patch-0..8 from the main install), separate patched EXE copy, plus
Data/patch-9.MPQ = mod-source/new-races/races-goblin.MPQ. Never writes the main install or live saves.

  python tools/new-races/stage_races_preview.py --exe <patched exe> --mpq mod-source/new-races/races-goblin.MPQ
"""
import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT.parent
SRC = ROOT / "analysis" / "renderer-rea" / "candidate-0.103.0" / "stage_preview.py"
spec = importlib.util.spec_from_file_location("stage_preview", SRC)
sp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sp)

SHIPPED = ["patch.MPQ"] + [f"patch-{i}.MPQ" for i in range(9)]


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", required=True)
    ap.add_argument("--mpq", required=True)
    ap.add_argument("--name", default="races-v0.103.0")
    a = ap.parse_args()
    exe, mpq = Path(a.exe).resolve(), Path(a.mpq).resolve()
    dest = WORKSPACE / "previews" / a.name
    if os.path.lexists(dest):
        raise SystemExit(f"preview destination exists: {dest}")
    stage = Path(tempfile.mkdtemp(prefix=f".{a.name}-stage-", dir=str(dest.parent)))
    try:
        for sub in ("Data", "config", "saves", "diagnostics"):
            (stage / sub).mkdir()
        files = []
        shutil.copy2(exe, stage / "AzerothSurvivors.exe")
        files.append({"path": "AzerothSurvivors.exe", "method": "copy", "sha256": sha(stage / "AzerothSurvivors.exe")})
        for n in SHIPPED:
            s = ROOT / "Data" / n
            os.link(s, stage / "Data" / n)
            files.append({"path": f"Data/{n}", "method": "hardlink (read-only by rule)", "bytes": s.stat().st_size})
        shutil.copy2(mpq, stage / "Data" / "patch-9.MPQ")
        files.append({"path": "Data/patch-9.MPQ", "method": "copy", "sha256": sha(stage / "Data" / "patch-9.MPQ")})
        orig = (ROOT / "benilla-config" / "config.toml").read_text(encoding="utf-8")
        sp.write_text(stage / "config" / "config.toml", sp.configure_preview(orig))
        save_source = WORKSPACE / "previews" / "graphics-v0.103.0" / "saves"
        shutil.copytree(save_source, stage / "saves", dirs_exist_ok=True)
        manifest = {"preview": a.name, "staged_at_utc": datetime.now(timezone.utc).isoformat(), "files": files,
                    "saves": "copy of previews/graphics-v0.103.0/saves (fixture; live saves never read)",
                    "limits": ["Goblin (ChrRaces 9) added by EXE patch (tools/new-races/patch_exe.py) and patch-9.MPQ DBC rows.",
                               "Hardlinked archives must not be modified through the preview.",
                               "Not installed into the main game."]}
        sp.write_text(stage / "manifest.json", json.dumps(manifest, indent=2) + "\n")
        cmd = ('@echo off\r\nsetlocal\r\nset "PREVIEW_ROOT=%~dp0"\r\n'
               'for %%V in (WOW_NOSOUND WOW_CAPTURE WOW_ROGUE_ARMORY WOW_ROGUE_TAPS WOW_ROGUE_AUTOPLAY WOW_ROGUE_AUTOSTART '
               'WOW_ROGUE_SHOTS WOW_ROGUE_UPDATE_URL) do set "%%V="\r\n'
               'set "WOW_DATA=%PREVIEW_ROOT%Data"\r\nset "BENILLA_HOME=%PREVIEW_ROOT%config"\r\n'
               'set "WOW_ROGUE_SAVE=%PREVIEW_ROOT%saves\\characters.txt"\r\nset "WOW_ROGUE_UPDATE_AUTO=0"\r\n'
               'cd /d "%PREVIEW_ROOT%"\r\nstart "" "AzerothSurvivors.exe"\r\nendlocal\r\n')
        (stage / "Play Races Preview.cmd").write_bytes(cmd.encode("ascii"))
        sp.write_text(stage / "README.md", "# Azeroth Survivors new-races preview v0.103.0\n\nGoblin added as a 9th "
                      "playable race (EXE patch + patch-9.MPQ). Isolated config/saves. See "
                      "AzerothSurvivors/analysis/new-races/FEASIBILITY.md.\n")
        os.rename(stage, dest)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    print("staged", dest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
