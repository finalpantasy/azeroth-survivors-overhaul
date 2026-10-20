#!/usr/bin/env python3
"""Installer entry point for the skinned converter (as-recipe/1 `run-tool` step, tool id `skinned-converter`).

The installer runs exactly one hash-pinned script with `python -I`, so this file pins everything it imports or reads:
the converter modules and the conversion recipes next to it must match PINNED before anything is imported. Changing
any of them means re-running `python skinned_step.py --print-pins`, pasting the result into PINNED, and then updating
this script's hash in installer/tools/registry.json (the registry pins this file, this file pins the rest).

    skinned_step.py --source-dir <casc-export step> --game-dir <game-extract step> --output-dir <out>

--source-dir holds the exported FileDataIDs as <fdid>.bin (model, skins, external .anim, BLPs). The conversion recipe
is the one pinned recipe whose source model FileDataID is present there (exactly one must match). --game-dir holds the
native model being replaced (template for PlayableAnimationLookup) and DBFilesClient/AnimationData.dbc. Output:
<out>/payload/<native paths> plus conversion-report.json. No MPQ is packed: the installer packs the slot itself.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PINNED = {
    "decimate.py": "2669522a5131a540ffb9b43894c922cad00f3b765dd42267a36f8f6adcc55e1f",
    "m2ir.py": "6aff8e742506277fa245a61cf1f6ff17bd7624277efe68cd2e654d40ed3cdbb3",
    "skinned_convert.py": "665571215345c4454dd079fb17b63be18684d03a4c00f27095d7648ff956c3ac",
    "recipes/bloodelfmale.json": "ec9f4ecfc71b1899da28d93006aa77e43c2bd0b19280040aaf170d284fa65b28",
    "recipes/golemharvest.json": "df246e2a288f564e345942f990cbc0c79227f397ac059ac4e7bf695bc22b12d9",
    "recipes/gnoll.json": "fae212d691232099a7286281d5ae223669c97c7cdb5013551326c6d3c2b24938",
    "recipes/kobold.json": "9970ed8374adb507304cbd4598f052e529a28e25adbbcd59b4bca32c08f1cf9b",
    "recipes/murloc.json": "2902c8002db19cd896585fbfd32ee45808043c120e39c11b2a117d41425194f6",
    "recipes/raptor.json": "51c34e7b72c1e0807cccb280134193ab875fcaad594fb9d9fa31fc63dad92da0",
}


def digest(rel: str) -> str:
    return hashlib.sha256((HERE / rel).read_bytes()).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-dir", type=Path)
    ap.add_argument("--game-dir", type=Path)
    ap.add_argument("--output-dir", type=Path)
    ap.add_argument("--print-pins", action="store_true")
    a = ap.parse_args()
    if a.print_pins:
        print(json.dumps({rel: digest(rel) for rel in PINNED}, indent=4))
        return 0
    for rel, want in PINNED.items():
        got = digest(rel)
        if got != want:
            print(f"skinned_step: {rel} hash {got[:12]} does not match the pinned {want[:12] or '(unset)'}", file=sys.stderr)
            return 2
    sys.path.insert(0, str(HERE))
    import m2ir
    import skinned_convert

    recipes = [json.loads((HERE / rel).read_text(encoding="utf8")) for rel in PINNED if rel.startswith("recipes/")]
    match = [r for r in recipes if (a.source_dir / f"{int(r['source']['model'])}.bin").is_file()]
    if len(match) != 1:
        print(f"skinned_step: {len(match)} pinned recipes match the exported files (need exactly 1)", file=sys.stderr)
        return 2
    a.output_dir.mkdir(parents=True, exist_ok=True)
    try:
        report, *_ = skinned_convert.convert(match[0], a.source_dir, a.output_dir, None, None, profile=None,
                                             previews=False, game_dir=a.game_dir, pack=False)
    except m2ir.ConvertError as exc:
        print(f"skinned_step: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"recipe": match[0]["name"], "model_sha256": report["output"]["model_sha256"],
                      "cost": report["output"]["cost"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
