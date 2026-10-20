#!/usr/bin/env python3
"""Generate the installer recipe for the VERIFIED subset of the HD player bodies.

Drives the characters worker's own ``tools/characters/build_characters.py`` (imported, never modified) with
``--only <base display ids>`` into a private cache folder, with its base payload (spells/mace/ItemDisplayInfo)
replaced by an empty folder so the result holds ONLY model/texture files and the three player DBCs. Their reports and
payload folder are not touched. Then writes ``hd-characters-next.json`` (a user-supplied-source recipe that pins every
payload file by size and SHA-256, so the installer verifies the bytes before packing).

    python gen_characters.py --ids 49,50,51,52 --cache <dir> --out-recipe <file.json>
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
MAIN = Path(".")
BC_PATH = MAIN / "tools/characters/build_characters.py"
EXE_SHA = "6382e9ae654295293b82d48fb07a1aa999132775bda10c6c6f80d973d2c1ddfb"
EXE_SHA_1 = "3b46bad92e7771afa61c93658fd850937834a40f3671af8b1a3e1d2aeb8b63c7"  # official v0.103.1 (data patches identical)


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def load_bc():
    spec = importlib.util.spec_from_file_location("build_characters_ro", BC_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(BC_PATH.parent))
    spec.loader.exec_module(mod)
    return mod


def build_payload(ids: list[int], cache: Path) -> Path:
    bc = load_bc()
    payload = cache / "payload"
    if payload.exists():
        shutil.rmtree(payload)
    cache.mkdir(parents=True, exist_ok=True)
    empty = cache / "empty-base"
    empty.mkdir(exist_ok=True)
    bc.OUT = cache / "out"
    bc.OUT.mkdir(exist_ok=True)
    bc.PAYLOAD = payload
    bc.REPORTS = cache / "reports"
    bc.BASE_PAYLOAD = empty
    old = sys.argv
    sys.argv = ["build_characters.py", "--build", "--only", ",".join(map(str, ids))]
    try:
        rc = bc.main()
    finally:
        sys.argv = old
    if rc:
        raise SystemExit(f"build_characters failed with {rc}")
    return payload


def write_recipe(payload: Path, ids: list[int], out: Path) -> dict:
    files, outputs = [], []
    for p in sorted(payload.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(payload).as_posix()
        files.append({"name": rel, "bytes": p.stat().st_size, "sha256": sha(p)})
        outputs.append({"slot": "patch-0.MPQ", "path": rel.replace("/", "\\"), "from": "src:" + rel, "sha256": files[-1]["sha256"]})
    sample = [outputs[0], outputs[len(outputs) // 2], outputs[-1]]
    dbcs = [o for o in outputs if o["path"].lower().endswith(".dbc")]
    recipe = {
        "schema": "as-recipe/1", "id": "hd-characters-next", "version": "0.1.0",
        "title": f"HD player bodies, verified subset (display ids {','.join(map(str, ids))})",
        "description": "Local, generated recipe: the characters worker's HD player models, textures and the three player DBCs "
                       "restricted to the races the lab marked VERIFIED. The payload was built from the community VanillaHD "
                       "Patch-A commit pinned in build_characters.py; every file is pinned here by size and SHA-256.",
        "status": "experimental", "default": False,
        "requires": {"game": {"exe_sha256": [EXE_SHA, EXE_SHA_1], "versions": ["0.103.0", "0.103.1"]}, "tools": ["mpq-packer"], "disk_mb": 1500},
        "sources": {"chars": {"kind": "user-supplied",
                              "instructions": "Run next/gen_characters.py (or next/next_build.py) to regenerate the payload folder, then pass --source chars=<folder>.",
                              "files": files}},
        "steps": [{"id": "src", "op": "user-source", "source": "chars"}],
        "outputs": {"mpq_files": outputs},
        "acceptance": [{"check": "mpq-has", "slot": "patch-0.MPQ", "path": o["path"], "sha256": o["sha256"]} for o in sample + dbcs],
        "provenance": {"builder": "tools/characters/build_characters.py --only", "display_ids": ids,
                       "note": "local generated recipe; contains Blizzard-derived data hashes only"},
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(recipe, indent=1) + "\n", encoding="utf-8")
    return {"files": len(files), "bytes": sum(f["bytes"] for f in files), "dbcs": [d["path"] for d in dbcs]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", required=True, help="comma list of base player display ids")
    ap.add_argument("--cache", required=True, type=Path)
    ap.add_argument("--out-recipe", required=True, type=Path)
    a = ap.parse_args()
    ids = [int(x) for x in a.ids.split(",") if x]
    payload = build_payload(ids, a.cache)
    info = write_recipe(payload, ids, a.out_recipe)
    print(json.dumps({"payload": str(payload), **info}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
