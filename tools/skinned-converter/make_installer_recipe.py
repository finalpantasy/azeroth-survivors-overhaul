#!/usr/bin/env python3
"""Generate an as-recipe/1 installer recipe for one skinned-converter recipe, and prove it with the pinned step tool.

    python make_installer_recipe.py <name> --report <converter out dir> --cache <fdid cache> --data <game Data dir>
        --work <NEW scratch dir> --out <installer recipe .json>

1. Reads the converter report to list every FileDataID the conversion reads (model, skins up to the LOD, external
   .anim files, BLPs) and pins their size/SHA-256 from the local cache (as the casc source).
2. Reads the native template model and AnimationData.dbc from the game's OFFICIAL chain with the installer's own MPQ
   reader (patch-0/patch-1 excluded, exactly like the game-extract step) and pins their hashes.
3. Lays those files out as the installer would (<work>/src, <work>/base) and runs skinned_step.py on them with
   python -I; the produced files' hashes become expect_outputs, outputs.mpq_files and acceptance checks.
Status is written as `experimental` (opt-in) and `default: false`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
INSTALLER = HERE.parent / "installer"
sys.path.insert(0, str(INSTALLER))
from asinstall import mpq  # noqa: E402

FOREVER = {"kind": "casc", "product": "wowf", "version": "1.60.1.70245", "build_number": 70245,
           "build_key": "e8c4ef4e67bd1d94d820254984258fa8", "cdn_key": "f065eade5276a5ac05606e60a4c5a1f7",
           "locale": "enUS", "build_config": {"bytes": 83760, "sha256": "29237f0d511842f1d126cdc27989a74593b854ff424061b039c191dcc6ad306c"},
           "cdn_config": {"bytes": 59333, "sha256": "cd1efbd680adcc8f8a77dad07011920eaa6b38c32f53217d2acc5e37b44f2c26"}}
RETAIL = {"kind": "casc", "product": "wow", "version": "12.1.5.70448", "build_number": 70448,
          "build_key": "88d5e8ef05ec5a8446cdec93d142d7c6", "cdn_key": "f065eade5276a5ac05606e60a4c5a1f7",
          "locale": "enUS", "build_config": {"bytes": 87836, "sha256": "f80e4cb45b64a3e00f94db5b6eafc45dfdd03b0734282c1369bcff9443062ba1"},
          "cdn_config": {"bytes": 59333, "sha256": "cd1efbd680adcc8f8a77dad07011920eaa6b38c32f53217d2acc5e37b44f2c26"}}
CLIENT = {"forever": (FOREVER, "Classic (Forever 1.60.1.70245)"), "retail": (RETAIL, "Retail (12.1.5.70448)")}
EXE_SHA = ["6382e9ae654295293b82d48fb07a1aa999132775bda10c6c6f80d973d2c1ddfb"]
SLOT = "patch-0.MPQ"


def h(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("--report", type=Path, required=True)
    ap.add_argument("--cache", type=Path, required=True)
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    conv = json.loads((HERE / "recipes" / f"{a.name}.json").read_text(encoding="utf8"))
    rep = json.loads((a.report / "conversion-report.json").read_text(encoding="utf8"))
    src = rep["source"]
    profile = conv["source"]["profile"]
    pins, client = CLIENT[profile]
    roles = {src["model_fdid"]: "model"}
    for f in (src["skin_files"] if src["lod"] else []):
        roles[f] = "other LOD skin (vertex lookup only: vertex base)"
    roles[src["skin_fdid"]] = f"skin LOD {src['lod']}"
    for k, f in src["anim_files"].items():
        if f:
            roles[f] = f"external .anim {k}"
    for path, f in rep["output"]["textures"].items():
        roles.setdefault(f, f"BLP for {path}")
    if a.work.exists():
        raise SystemExit(f"{a.work} exists; pass a new folder")
    srcdir, base, out = a.work / "src", a.work / "base", a.work / "out"
    srcdir.mkdir(parents=True)
    files = []
    for f, role in sorted(roles.items()):
        b = (a.cache / f"{f}.bin").read_bytes()
        (srcdir / f"{f}.bin").write_bytes(b)
        files.append({"file_data_id": int(f), "name": f"{f}.bin", "bytes": len(b), "sha256": h(b), "role": role})
    model_path = rep["output"]["model_path"]
    game_files = []
    reader = mpq.ChainReader(mpq.official_chain(a.data, ["patch-0.MPQ", "patch-1.MPQ"]))
    try:
        for p, role in ((model_path, "native model being replaced (PlayableAnimationLookup template)"),
                        ("DBFilesClient\\AnimationData.dbc", "official AnimationData.dbc (fallback chain)")):
            try:
                data, origin = reader.read(p)
            except KeyError:
                if p == model_path:  # model absent from the game (e.g. GolemHarvest): AnimationData fallbacks only
                    continue
                raise
            dest = base.joinpath(*p.split("\\"))
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            game_files.append({"path": p, "sha256": [h(data)], "role": f"{role}; from {origin.name}"})
    finally:
        reader.close()
    r = subprocess.run([sys.executable, "-I", str(HERE / "skinned_step.py"), "--source-dir", str(srcdir),
                        "--game-dir", str(base), "--output-dir", str(out)], capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        raise SystemExit(f"step tool failed: {r.stderr[-800:]}")
    produced = sorted(p for p in (out / "payload").rglob("*") if p.is_file())
    expect = [{"path": p.relative_to(out).as_posix(), "sha256": h(p.read_bytes())} for p in produced]
    mpq_files = [{"slot": SLOT, "path": p.relative_to(out / "payload").as_posix().replace("/", "\\"),
                  "from": "convert:" + p.relative_to(out).as_posix(), "sha256": h(p.read_bytes())} for p in produced]
    model_out = next(e for e in mpq_files if e["path"].lower() == model_path.lower())
    oc = json.loads((out / "conversion-report.json").read_text(encoding="utf8"))
    cost = oc["output"]["cost"]
    recipe = {
        "schema": "as-recipe/1",
        "id": f"skinned-{a.name}-{profile}",
        "version": "0.1.0",
        "title": f"Animated {profile.title()} {conv['name']} model ({model_path})",
        "description": (f"Exports the {client} {conv['name'].lower()} model, its LOD {src['lod']} skin, "
                        f"animations and skins from your own client and converts them to the game's animated MD20 v256 "
                        f"model at {model_path}, replacing the native model for every creature that uses it."),
        "status": "experimental",
        "default": False,
        "requires": {"game": {"exe_sha256": EXE_SHA, "versions": ["0.103.0"]},
                     "tools": ["casc-export", "skinned-converter"], "disk_mb": 64},
        "sources": {profile: dict(pins, files=files),
                    "game-base": {"kind": "game-chain", "exclude_slots": ["patch-0.MPQ", "patch-1.MPQ"], "files": game_files}},
        "steps": [{"id": "export", "op": "casc-export", "source": profile},
                  {"id": "base", "op": "game-extract", "source": "game-base"},
                  {"id": "convert", "op": "run-tool", "tool": "skinned-converter",
                   "inputs": {"src": "export", "base": "base"}, "expect_outputs": expect}],
        "outputs": {"mpq_files": mpq_files},
        "acceptance": [{"check": "mpq-has", "slot": SLOT, "path": model_out["path"], "sha256": model_out["sha256"]}],
        "provenance": (f"{profile.title()} FileDataIDs model {src['model_fdid']}, skin {src['skin_fdid']}; converter recipe "
                       f"tools/skinned-converter/recipes/{a.name}.json; pinned skinned_step.py. Output cost: "
                       f"{cost['vertices']} verts, {cost['triangles']} tris, {cost['bones']} bones, {cost['sequences']} sequences; "
                       f"vs native {oc.get('cost_ratio_vs_native')}."),
        "limits": ["Particles, ribbons, lights, modern shader combiners and camera animation are dropped (see the report).",
                   "Replaces the model for every display that uses it; native skin names are reused for the modern skins.",
                   conv.get("note", "")],
    }
    a.out.write_text(json.dumps(recipe, indent=1) + "\n", encoding="utf8")
    print(json.dumps({"recipe": str(a.out), "files": len(files), "outputs": len(mpq_files), "model_sha256": model_out["sha256"]}))


if __name__ == "__main__":
    main()
