#!/usr/bin/env python3
"""Regression checks for the skinned converter.  python tools/skinned-converter/test_skinned.py

1. skinned_step.py's PINNED hashes match the files on disk, and the installer registry pins skinned_step.py.
2. For every installer recipe skinned-<name>-<profile>.json whose inputs are in the local cache, a fresh conversion
   produces the exact model/BLP hashes the recipe expects (skipped when the cache is absent: it holds Blizzard files).
3. The written model reads back with the same vertex/bone/sequence counts.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
import m2ir  # noqa: E402
import skinned_convert  # noqa: E402
import skinned_step  # noqa: E402

WORK = ROOT / "analysis" / "skinned-converter" / "work"


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> int:
    fails = 0
    for rel, want in skinned_step.PINNED.items():
        if sha(HERE / rel) != want:
            print(f"FAIL pin {rel}")
            fails += 1
    reg = json.loads((ROOT / "tools" / "installer" / "tools" / "registry.json").read_text(encoding="utf8"))
    if reg["tools"]["skinned-converter"]["sha256"] != sha(HERE / "skinned_step.py"):
        print("FAIL registry hash of skinned_step.py")
        fails += 1
    for ir in sorted((ROOT / "tools" / "installer" / "recipes").glob("skinned-*.json")):
        rec = json.loads(ir.read_text(encoding="utf8"))
        _, name, profile = rec["id"].split("-")
        cache = WORK / f"cache-{profile}"
        if not all((cache / f["name"]).is_file() for f in rec["sources"][profile]["files"]):
            print(f"skip {name}: inputs not in the local cache")
            continue
        conv = json.loads((HERE / "recipes" / f"{name}.json").read_text(encoding="utf8"))
        tmp = Path(tempfile.mkdtemp(prefix=f"skinned-{name}-"))
        try:
            data = ROOT / "Data"
            work = ROOT / "analysis" / "skinned-converter" / "work"
            _, m, back, keep = skinned_convert.convert(conv, cache, tmp / "out", data, work, pack=False)
            for e in rec["steps"][2]["expect_outputs"]:
                got = sha(tmp / "out" / e["path"])
                ok = got == e["sha256"]
                fails += not ok
                print(("ok  " if ok else "FAIL") + f" {name} {e['path']}")
            ok = len(back.vertices) == len(m.vertices) and len(back.bones) == len(m.bones) and len(back.sequences) == len(keep)
            fails += not ok
            print(("ok  " if ok else "FAIL") + f" {name} readback counts")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    print("PASS" if not fails else f"{fails} FAILURES")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
