#!/usr/bin/env python3
"""Stage the isolated all-races character preview: previews/characters-v0.103.0.

Same pattern as analysis/renderer-rea/candidate-0.103.0/stage_preview.py: own copied config and saves,
read-only hardlinked archives (patch-1..8, patch.MPQ), separate EXE copy, never the main install.
Difference: Data/patch-0.MPQ is mod-source/characters-hd/characters-hd.MPQ (all 16 race/gender HD
models + CharSections coverage + the Human-scope spell/mace payload that patch-0 already carried).

  python tools/characters/stage_characters_preview.py --mpq mod-source/characters-hd/characters-hd.MPQ
"""
import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import tomllib
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT.parent
SRC = ROOT / "analysis" / "renderer-rea" / "candidate-0.103.0" / "stage_preview.py"

spec = importlib.util.spec_from_file_location("stage_preview", SRC)
sp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sp)

PREVIEW_NAME = "characters-v0.103.0"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mpq", required=True, help="characters-hd.MPQ to install as Data/patch-0.MPQ")
    args = ap.parse_args()
    mpq = (ROOT / args.mpq).resolve() if not Path(args.mpq).is_absolute() else Path(args.mpq)
    if not mpq.is_file():
        raise SystemExit(f"missing {mpq}")
    preview_parent = WORKSPACE / "previews"
    preview_root = preview_parent / PREVIEW_NAME
    if os.path.lexists(str(preview_root)):
        raise SystemExit(f"preview destination exists: {preview_root}")

    # reuse the graphics preview's pinned list; swap patch-0 for ours
    files = []
    for item in sp.PINNED_FILES:
        item = dict(item)
        if item["destination"] == "Data/patch-0.MPQ":
            item["source"] = mpq
            item["bytes"] = mpq.stat().st_size
            item["sha256"] = sp.sha256_file(mpq)
            item["method"] = "copy"
        else:
            item["source"] = ROOT / item["source"]
        files.append(item)
    for item in files:
        p = Path(item["source"])
        if not p.is_file():
            raise SystemExit(f"missing source {p}")
        if p.stat().st_size != item["bytes"]:
            raise SystemExit(f"size mismatch {p}")
    print("verifying hashes of", len(files), "files ...")
    for item in files:
        if item["destination"] != "Data/patch-0.MPQ" and sp.sha256_file(Path(item["source"])) != item["sha256"]:
            raise SystemExit(f"sha mismatch {item['source']}")

    config_source = ROOT / "benilla-config" / "config.toml"
    with config_source.open("r", encoding="utf-8", newline="") as f:
        orig = f.read()
    cfg = sp.configure_preview(orig)
    before = tomllib.loads(orig.lstrip("﻿")).get("cvars", {})
    after = tomllib.loads(cfg.lstrip("﻿")).get("cvars", {})
    if after != dict(before, SoundListenerAtCharacter="1"):
        raise SystemExit("config preview changed another CVar")
    # saves: copy of the graphics preview's own (already isolated) save folder; the live roster is never read
    save_source = preview_parent / "graphics-v0.103.0" / "saves"
    if not (save_source / "characters.txt").is_file():
        raise SystemExit(f"fixture roster missing: {save_source}")

    stage = Path(tempfile.mkdtemp(prefix=".characters-v0.103.0-stage-", dir=str(preview_parent)))
    try:
        (stage / "Data").mkdir()
        (stage / "config").mkdir()
        (stage / "saves").mkdir()
        (stage / "diagnostics").mkdir()
        staged = []
        for item in files:
            src = Path(item["source"])
            dest = stage / item["destination"]
            dest.parent.mkdir(parents=True, exist_ok=True)
            if item["method"] == "hardlink":
                os.link(str(src), str(dest))
            else:
                shutil.copy2(str(src), str(dest))
                if sp.sha256_file(dest) != item["sha256"]:
                    raise SystemExit("copy hash mismatch")
            staged.append({"path": item["destination"], "source": str(src.relative_to(WORKSPACE)).replace("\\", "/"),
                           "method": item["method"], "bytes": item["bytes"], "sha256": item["sha256"]})
        sp.write_text(stage / "config" / "config.toml", cfg)
        shutil.copytree(str(save_source), str(stage / "saves"), dirs_exist_ok=True)
        copied = sorted(str(p.relative_to(stage / "saves")).replace("\\", "/") for p in (stage / "saves").rglob("*") if p.is_file())
        manifest = {
            "schema_version": 1, "preview": PREVIEW_NAME,
            "staged_at_utc": datetime.now(timezone.utc).isoformat(),
            "files": staged,
            "config": {"source": "benilla-config/config.toml", "destination": "config/config.toml",
                       "preview_only_change": 'SoundListenerAtCharacter = "1"'},
            "saves": {"source": "previews/graphics-v0.103.0/saves (copy)", "destination": "saves", "files": copied},
            "limits": [
                "All 16 playable race/gender bodies replaced by community VanillaHD models (private model rows, base player displays only).",
                "CharSections rows are shared with NPCs of the same race/gender: NPC humanoids that use extended display info get the HD skin/face/hair textures on their old models.",
                "Data MPQs made as hardlinks must not be modified through the preview.",
                "Not installed into the main game; see analysis/characters/ for coverage, cost and defect reports.",
            ],
        }
        sp.write_text(stage / "manifest.json", json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        sp.write_text(stage / "README.md",
                      "# Azeroth Survivors characters preview v0.103.0\n\nAll 8 playable races x 2 genders with community HD "
                      "bodies (VanillaHD Patch-A, pinned commit) on top of the graphics preview base. Isolated config, saves "
                      "and diagnostics under this folder; archives patch-1..8/patch.MPQ are read-only hardlinks. Patch-0 is a "
                      "separate copy. Reports: AzerothSurvivors/analysis/characters/.\n")
        cmd = ('@echo off\r\nsetlocal\r\nset "PREVIEW_ROOT=%~dp0"\r\n'
               'for %%V in (WOW_NOSOUND WOW_CAPTURE WOW_CAPTURE_UI WOW_CAM_DUMP WOW_ROGUE_ARMORY WOW_ROGUE_GLUE WOW_ROGUE_TAPS '
               'WOW_ROGUE_KEY WOW_ROGUE_AUTOPLAY WOW_ROGUE_AUTOSTART WOW_ROGUE_AUTOPICK WOW_ROGUE_SHOTS WOW_ROGUE_SHOWCASE '
               'WOW_ROGUE_DEBUG_LOOT WOW_ROGUE_UPDATE_URL WOW_ROGUE_SINK WOW_ROGUE_SINK_AT) do set "%%V="\r\n'
               'set "WOW_DATA=%PREVIEW_ROOT%Data"\r\nset "BENILLA_HOME=%PREVIEW_ROOT%config"\r\n'
               'set "WOW_ROGUE_SAVE=%PREVIEW_ROOT%saves\\characters.txt"\r\nset "WOW_ROGUE_UPDATE_AUTO=0"\r\n'
               'if not exist "%PREVIEW_ROOT%diagnostics" mkdir "%PREVIEW_ROOT%diagnostics"\r\n'
               ':choose_journal\r\nset "WOW_FPS_JOURNAL=%PREVIEW_ROOT%diagnostics\\frame-times-%RANDOM%-%RANDOM%.csv"\r\n'
               'if exist "%WOW_FPS_JOURNAL%" goto choose_journal\r\ncd /d "%PREVIEW_ROOT%"\r\n'
               'start "" "AzerothSurvivors.exe"\r\nendlocal\r\n')
        (stage / "Play Characters Preview.cmd").write_bytes(cmd.encode("ascii"))
        os.rename(str(stage), str(preview_root))
    except BaseException:
        shutil.rmtree(str(stage), ignore_errors=True)
        raise
    print("staged", preview_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
