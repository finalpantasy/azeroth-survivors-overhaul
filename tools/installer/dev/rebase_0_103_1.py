#!/usr/bin/env python3
"""One-off rebase of the installer recipes onto official v0.103.1 (read-only on the game; writes recipes/known_builds.json).

* water-shader-adt-liquid: every edit moved by the same shift (found by byte search in the new EXE), content identical ->
  rewritten as v0.2.0 for the 0.103.1 base; the 0.103.0 recipe is kept under recipes/legacy-0.103.0/ (not loaded).
* data recipes (MPQ only): the official patch-2..8 archives are byte-identical in 0.103.1, so they stay valid;
  their `requires.game` list is widened with the new EXE and 0.103.1.
* renderer-sharpen-ffx: NOT rebased here (PE header/section edits and a RIP-relative displacement change with the new layout);
  recipe stays as is (it refuses on the new EXE) and is reported to its owner.
* known_builds.json: official 0.103.1 + 0.103.1 with water.

    python rebase_0_103_1.py --old <exe 0.103.0> --new <exe 0.103.1> [--apply]
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
INST = HERE.parent
RECIPES = INST / "recipes"
OLD_BASE = "6382e9ae654295293b82d48fb07a1aa999132775bda10c6c6f80d973d2c1ddfb"
DATA_RECIPES = ["hd-spells-community", "hd-terrain-community", "retail-mace-kultiras-silver", "spells-retail-fx",
                "skinned-bloodelfmale-retail", "skinned-gnoll-forever", "skinned-golemharvest-retail",
                "skinned-kobold-forever", "skinned-murloc-forever", "skinned-raptor-forever"]


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def dump(p: Path, doc: dict) -> None:
    p.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--old", type=Path, required=True)
    ap.add_argument("--new", type=Path, required=True)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    old, new = a.old.read_bytes(), a.new.read_bytes()
    assert sha(old) == OLD_BASE
    new_base = sha(new)

    # --- water: shift every edit
    wpath = RECIPES / "water-shader-adt-liquid.json"
    water = json.loads(wpath.read_text(encoding="utf-8"))
    patch = water["outputs"]["exe_patches"][0]
    # the edits all lie in one contiguous .rdata interval: find the shift from the longest edit (exact bytes, unique), then
    # require that the WHOLE interval is byte-identical at that shift (so every edit, even short ones, is anchored)
    lo = min(e["offset"] for e in patch["edits"])
    hi = max(e["offset"] + e["length"] for e in patch["edits"])
    big = max(patch["edits"], key=lambda e: e["length"])
    seg = old[big["offset"]:big["offset"] + big["length"]]
    i = new.find(seg)
    assert i >= 0 and new.find(seg, i + 1) < 0, "anchor of the longest edit is not unique"
    shift = i - big["offset"]
    assert old[lo:hi] == new[lo + shift:hi + shift], "edited interval is not byte-identical at the shift"
    for e in patch["edits"]:
        assert sha(old[e["offset"]:e["offset"] + e["length"]]) == e["expect_sha256"], f"old bytes differ at {e['offset']}"
    patched = bytearray(new)
    for e in patch["edits"]:
        e["offset"] += shift
        assert new[e["offset"]:e["offset"] + e["length"]] == old[e["offset"] - shift:e["offset"] - shift + e["length"]]
        patched[e["offset"]:e["offset"] + e["length"]] = base64.b64decode(e["new_b64"])
    new_water = sha(bytes(patched))
    patch["expect_exe_sha256"] = new_base
    patch["result_exe_sha256"] = new_water
    water["version"] = "0.2.0"
    water["requires"]["game"] = {"exe_sha256": [new_base], "versions": ["0.103.1"]}
    for acc in water.get("acceptance", []):
        if acc.get("check") == "exe-sha256":
            acc["equals"] = new_water
    note = (f" Rebased to v0.103.1 (0.2.0): all {len(patch['edits'])} edits moved by +{shift} bytes, content unchanged; "
            f"the 0.1.0 recipe for v0.103.0 is kept in recipes/legacy-0.103.0/.")
    water["provenance"] = (water["provenance"] + note) if isinstance(water.get("provenance"), str) else {**water.get("provenance", {}), "rebased": note.strip()}
    print("water: shift", shift, "edits", len(patch["edits"]), "result", new_water)

    # --- data recipes: widen requires
    widened = []
    for rid in DATA_RECIPES:
        p = RECIPES / f"{rid}.json"
        r = json.loads(p.read_text(encoding="utf-8"))
        g = r["requires"]["game"]
        for s in (new_base, new_water):
            if s not in g["exe_sha256"]:
                g["exe_sha256"].append(s)
        if "0.103.1" not in g["versions"]:
            g["versions"].append("0.103.1")
        widened.append((p, r))

    # --- known_builds
    kp = INST / "known_builds.json"
    kb = json.loads(kp.read_text(encoding="utf-8"))
    base0 = next(g for g in kb["game_builds"] if g["exe_sha256"] == OLD_BASE)
    if not any(g["exe_sha256"] == new_base for g in kb["game_builds"]):
        kb["game_builds"].insert(0, {"version": "0.103.1", "label": "official v0.103.1", "exe_sha256": new_base, "exe_bytes": len(new),
                                     "modified": False, "official_patches": base0["official_patches"],
                                     "note": "official data patch-2..8 byte-identical to 0.103.0 (updater manifest checked 2026-10-19)"})
        kb["game_builds"].append({"version": "0.103.1", "label": "v0.103.1 + water-shader recipe (installer-patched)",
                                  "exe_sha256": new_water, "exe_bytes": len(new), "modified": True, "base_exe_sha256": new_base})
    if not a.apply:
        print("dry run: nothing written")
        return 0
    legacy = RECIPES / "legacy-0.103.0"
    legacy.mkdir(exist_ok=True)
    if not (legacy / wpath.name).exists():
        shutil.copy2(wpath, legacy / wpath.name)
    dump(wpath, water)
    for p, r in widened:
        dump(p, r)
    dump(kp, kb)
    print("written:", wpath.name, [p.name for p, _ in widened], kp.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
