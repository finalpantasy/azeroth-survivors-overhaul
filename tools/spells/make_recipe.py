#!/usr/bin/env python3
"""Generate installer/recipes/spells-retail-fx.json (as-recipe/1, backlog batch R7) and register the converter tool.

Inputs (all from a verified local run of spells_step.py):
  analysis/spells/src-retail/   exported FileDataIDs (<fdid>.bin)
  analysis/spells/game-base/    native templates + AnimationData.dbc extracted from the official chain
  analysis/spells/step-out/     tool output (payload/ + conversion-report.json)
Run: make_recipe.py  (rewrites the recipe and the registry entry; pins are read from the files on disk).
"""
import hashlib
import json
from pathlib import Path

ROOT = Path(r".")
A = ROOT / "analysis/spells"
INST = ROOT / "tools/installer"
BS = chr(92)


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def main():
    models = json.load(open(ROOT / "tools/spells/fx_models.json"))
    report = json.load(open(A / "step-out/conversion-report.json"))
    src = A / "src-retail"
    files, seen = [], set()

    def add(fd, role):
        if fd in seen:
            return
        seen.add(fd)
        p = src / f"{fd}.bin"
        files.append({"file_data_id": int(fd), "name": f"{fd}.bin", "bytes": p.stat().st_size, "sha256": sha(p), "role": role})

    import struct, sys
    sys.path.insert(0, str(ROOT / "tools/skinned-converter"))
    import m2ir
    for name, fd in sorted(models.items()):
        add(fd, "model " + name)
        ch = m2ir.split_chunks((src / f"{fd}.bin").read_bytes())
        add(struct.unpack("<%dI" % (len(ch["SFID"]) // 4), ch["SFID"])[0], "skin LOD 0 of " + name)
    for fd in sorted(int(k) for k in report["textures"]):
        add(fd, "BLP " + report["textures"][str(fd)]["size"])
    game = [{"path": n, "sha256": [sha(A / "game-base" / n.replace(BS, "/"))], "role": "native effect model (animation lookup template)"}
            for n in sorted(models)]
    game.append({"path": "DBFilesClient" + BS + "AnimationData.dbc", "sha256": [sha(A / "game-base/DBFilesClient/AnimationData.dbc")],
                 "role": "official AnimationData.dbc (fallback chain)"})
    payload = A / "step-out/payload"
    outs = []
    for p in sorted(payload.rglob("*")):
        if p.is_file():
            rel = str(p.relative_to(payload)).replace(BS, "/")
            outs.append({"path": rel, "sha256": sha(p)})
    mace = json.load(open(INST / "recipes/retail-mace-kultiras-silver.json"))
    retail = {k: v for k, v in mace["sources"]["retail"].items() if k not in ("files", "note")}
    retail["files"] = files
    t = report["totals"]
    recipe = {
        "schema": "as-recipe/1",
        "id": "spells-retail-fx",
        "version": "0.1.0",
        "title": "Retail spell effects: 161 models with translated particles, ribbons and HD textures",
        "description": ("Exports the 161 spell effect models the game uses (every player ability and common enemy cast) and their "
                        "258 textures from your own Retail client, converts the models to the game's MD20 v256 including the "
                        "particle and ribbon emitters (modern to native emitter translation), and publishes the textures under a "
                        "private path (Spells\\AzerothSurvivorsHD\\R\\) so the official patch files never have to be overridden. "
                        "Replaces the native model for every ability that uses it. Cosmetic only."),
        "status": "experimental",
        "default": False,
        "requires": {"game": mace["requires"]["game"], "tools": ["casc-export", "spells-fx-converter"], "disk_mb": 128},
        "provenance": (f"Retail {retail['version']} FileDataIDs listed below; converter spells_step.py (pins fx_convert.py, "
                       f"fx_particles.py, fx_models.json, m2ir.py, skinned_convert.py). Output: {t['models']} models, "
                       f"{t['particles']} particle emitters, {t['ribbons']} ribbons, {t['textures']} textures "
                       f"({t['texture_bytes']} bytes, Retail BLP2 DXT with mips, shipped unchanged)."),
        "limits": [
            "Emitters use the first texture only (multi-texture particles), no scale/lifespan/rate variance, no geometry or recursion particles; colour, alpha and scale over life are resampled to 3 keys.",
            "Mesh effect shaders are dropped (the skinned converter flattens them to the native blend modes).",
            "Several Retail effects use fewer emitters than the classic ones (modern re-design); see conversion-report.json.",
            "Replaces the model for every ability that shares it; no per-ability opt-out.",
        ],
        "sources": {
            "retail": retail,
            "game-base": {"kind": "game-chain", "exclude_slots": ["patch-0.MPQ", "patch-1.MPQ"], "files": game},
        },
        "steps": [
            {"id": "export", "op": "casc-export", "source": "retail"},
            {"id": "base", "op": "game-extract", "source": "game-base"},
            {"id": "convert", "op": "run-tool", "tool": "spells-fx-converter", "inputs": {"src": "export", "base": "base"},
             "expect_outputs": [{"path": "payload/" + o["path"], "sha256": o["sha256"]} for o in outs]},
        ],
        "outputs": {"mpq_files": [{"slot": "patch-0.MPQ", "path": o["path"].replace("/", BS), "from": "convert:payload/" + o["path"],
                                   "sha256": o["sha256"]} for o in outs]},
        "acceptance": [{"check": "mpq-has", "slot": "patch-0.MPQ", "path": o["path"].replace("/", BS), "sha256": o["sha256"]}
                       for o in outs if o["path"].endswith("fireball_missile_high.m2") or o["path"].endswith("lightningbolt_impact_chest.m2")],
    }
    json.dump(recipe, open(INST / "recipes/spells-retail-fx.json", "w"), indent=1)
    reg_p = INST / "tools/registry.json"
    reg = json.load(open(reg_p))
    reg["tools"]["spells-fx-converter"] = {
        "script": "spells_step.py", "sha256": sha(ROOT / "tools/spells/spells_step.py"), "search": ["tools", "../spells"],
        "argv": ["--source-dir", "{in:src}", "--game-dir", "{in:base}", "--output-dir", "{out}"], "timeout": 900,
        "note": "Retail spell-effect M2 -> native MD20 v256 with particle/ribbon translation (W9, R7); pins its own modules"}
    json.dump(reg, open(reg_p, "w"), indent=2)
    print("recipe files", len(files), "game files", len(game), "outputs", len(outs))


if __name__ == "__main__":
    main()
