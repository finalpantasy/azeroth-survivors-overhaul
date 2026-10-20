#!/usr/bin/env python3
"""Playable race/gender appearance inventory from the Retail/Forever DB2 customization tables (offline dumps).

Usage: appearance_inventory.py <work-dir> <profile> <out.json>
Per playable race x sex: ChrModel id, customization options (name, choice count), geoset ids, material texture FDIDs.
Column meaning (index into the schema-less table rows, 0 = row id) was verified against known option names.
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import db2lib  # noqa: E402

IDS = {"chrraces": "1305311", "chrracexchrmodel": "3490304", "chrmodel": "3384313", "option": "3384247", "choice": "3450554",
       "element": "3512765", "geoset": "3456171", "material": "3459652", "skinned": "3460183", "texfd": "982459"}
PLAYABLE = {1: "human", 2: "orc", 3: "dwarf", 4: "nightelf", 5: "scourge", 6: "tauren", 7: "gnome", 8: "troll"}


def load(work, prof, key):
    return db2lib.Db2((Path(work) / f"db2-{prof}" / f"{IDS[key]}.bin").read_bytes()).table()[0]


def main():
    work, prof, out = sys.argv[1:4]
    rxm = load(work, prof, "chrracexchrmodel")
    opt = load(work, prof, "option")
    ch = load(work, prof, "choice")
    el = load(work, prof, "element")
    geo = {r[0]: r for r in load(work, prof, "geoset")}
    mat = {r[0]: r for r in load(work, prof, "material")}
    skinned = {r[0]: r for r in load(work, prof, "skinned")}
    texfd = defaultdict(list)  # MaterialResourcesID -> [(FileDataID, UsageType)]
    for r in load(work, prof, "texfd"):
        texfd[r[3]].append((r[1], r[2]))
    opts_by_model = defaultdict(list)
    for r in opt:
        opts_by_model[r[5]].append(r)
    choices_by_opt = defaultdict(list)
    for r in ch:
        choices_by_opt[r[3]].append(r)
    els_by_choice = defaultdict(list)
    for r in el:
        els_by_choice[r[1]].append(r)
    res = {}
    for r in rxm:
        race, model, sex = r[1], r[2], r[3]
        if race not in PLAYABLE:
            continue
        key = f"{PLAYABLE[race]}-{'female' if sex else 'male'}"
        info = {"race_id": race, "chr_model_id": model, "options": []}
        tex, geosets, skinned_models = set(), set(), set()
        for o in sorted(opts_by_model.get(model, []), key=lambda x: x[6]):
            chs = choices_by_opt.get(o[0], [])
            o_geo, o_tex = set(), set()
            for c in chs:
                for e in els_by_choice.get(c[0], []):
                    if e[3] in geo:
                        o_geo.add((geo[e[3]][1], geo[e[3]][2]))
                    if e[5] in mat:
                        o_tex.update(f for f, _u in texfd.get(mat[e[5]][2], []))
                    if e[4] in skinned:
                        skinned_models.add(skinned[e[4]][1])
            info["options"].append({"option_id": o[0], "name": o[1], "choices": len(chs), "geosets": len(o_geo), "textures": len(o_tex)})
            geosets |= o_geo
            tex |= o_tex
        info["total_choices"] = sum(x["choices"] for x in info["options"])
        info["unique_texture_fdids"] = len(tex)
        info["unique_geosets"] = len(geosets)
        info["skinned_model_fdids"] = sorted(skinned_models)
        info["texture_fdids"] = sorted(tex)
        res[key] = info
    Path(out).write_text(json.dumps(res, indent=1), encoding="utf8")
    for k, v in sorted(res.items()):
        top = ", ".join(f"{x['name']}:{x['choices']}" for x in v["options"][:9])
        print(k, "model", v["chr_model_id"], "opts", len(v["options"]), "choices", v["total_choices"], "tex", v["unique_texture_fdids"], "geosets", v["unique_geosets"], "|", top)


if __name__ == "__main__":
    main()
