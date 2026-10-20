#!/usr/bin/env python3
"""R2 picks: retail static shoulders (L/R pairs) and helms (16 race/sex variants) mapped onto the native drop-pool displays.

Pool displays come from the armor pool embedded in AzerothSurvivors.exe (analysis/asset-coverage/work/exe-armor-pool.json, a list of
ItemDisplayInfo ids in the ~8 KB block at file offset 112833896); each display's material (Plate/Mail/Leather/Robe) is read from its native
model name and mapped round-robin onto two hand-picked retail models of that material.  Models were chosen by eye from the contact
sheets in analysis/asset-coverage/work/helms-*.png and the shoulder census in rigid-pool.csv.

  gear_picks_r2.py <listfile> <exe-armor-pool.json> <out.json>
"""
import json
import sys
from pathlib import Path

HELM_BASES = {  # material -> retail helm base names (all 16 race/sex variants exist; hum variant passed the relaxed rigid gate)
    "Plate": ["helm_plate_dungeonplate_c_07", "helm_plate_pvpdeathknight_f_01"],
    "Mail": ["helm_mail_dungeonmail_c_02", "helm_mail_dungeonshaman_b_01"],
    "Leather": ["helm_leather_dungeonleather_c_03", "helm_leather_legionhonor_d_01"],
    "Robe": ["helm_robe_dungeonrobe_c_01", "helm_robe_pvpmage_e_01"],
}
SHOULDER_PAIRS = {
    "Plate": ["shoulder_plate_dungeonwarrior_a_01", "shoulder_plate_pvpalliance_a_01"],
    "Mail": ["shoulder_mail_dungeonshaman_a_01", "shoulder_mail_raidshaman_a_01"],
    "Leather": ["shoulder_leather_raiddruid_p_01", "shoulder_leather_dungeonrogue_b_01"],
    "Robe": ["shoulder_cloth_cataclysm_b_01", "shoulder_robe_dungeonrobe_c_05"],
}
RACES = [("Hu", "hu"), ("Or", "or"), ("Dw", "dw"), ("Ni", "ni"), ("Sc", "sc"), ("Ta", "ta"), ("Gn", "gn"), ("Tr", "tr")]


def material(model):
    m = model.lower()
    for key in ("plate", "mail", "leather"):
        if f"_{key}_" in m:
            return key.capitalize()
    return "Robe"          # Cloth and Robe both map to the robe/cloth pair


def pretty(base):
    return "_".join(p.capitalize() if not p.isdigit() else p for p in base.split("_"))


def main():
    listfile, pool, out = Path(sys.argv[1]), json.loads(Path(sys.argv[2]).read_text(encoding="utf8")), Path(sys.argv[3])
    fd = {}
    for line in listfile.read_text(encoding="utf8").splitlines():
        f, _, n = line.partition(";")
        if n.endswith(".m2") and ("/head/" in n or "/shoulder/" in n):
            fd[n.rsplit("/", 1)[1][:-3]] = int(f)
    items, edits = [], []
    # helms: 16 variants per base, one display edit per (display, base)
    helm_disp = {}
    for did, model in pool["helm"].items():
        helm_disp.setdefault(material(model), []).append(int(did))
    for mat, bases in HELM_BASES.items():
        for bi, base in enumerate(bases):
            name = pretty(base) + "_Static"
            ids = []
            for code, rc in RACES:
                for sex in ("M", "F"):
                    iid = f"{base}-{code.lower()}{sex.lower()}"
                    items.append({"id": iid, "kind": "Head", "fdid": fd[f"{base}_{rc}{sex.lower()}"], "name": f"{name}_{code}{sex}",
                                  "displays": [], "tex_stem": name, "note": f"{mat} helm {base} {code}{sex}"})
                    ids.append(iid)
            for did in helm_disp.get(mat, [])[bi::2]:
                edits.append({"display": did, "new_basename": name + ".mdx", "kind": "Head", "id": base, "column": 1, "requires": ids})
    # shoulders: L in column 1, R in column 2 of the same display
    sh_disp = {}
    for did, (left, right) in pool["shoulder"].items():
        if right:
            sh_disp.setdefault(material(left), []).append(int(did))
    for mat, bases in SHOULDER_PAIRS.items():
        for bi, base in enumerate(bases):
            name = pretty(base) + "_Static"
            lid, rid = f"l{base}", f"r{base}"
            items.append({"id": lid, "kind": "Shoulder", "fdid": fd["l" + base], "name": "L" + name, "displays": [], "tex_stem": name, "note": f"{mat} L"})
            items.append({"id": rid, "kind": "Shoulder", "fdid": fd["r" + base], "name": "R" + name, "displays": [], "tex_stem": name, "note": f"{mat} R"})
            for did in sh_disp.get(mat, [])[bi::2]:
                edits.append({"display": did, "new_basename": "L" + name + ".mdx", "kind": "Shoulder", "id": lid, "column": 1, "requires": [lid]})
                edits.append({"display": did, "new_basename": "R" + name + ".mdx", "kind": "Shoulder", "id": rid, "column": 2, "requires": [rid]})
    out.write_text(json.dumps({"batch": "R2", "items": items, "edits": edits}, indent=1), encoding="utf8")
    print("items", len(items), "edits", len(edits), "helm displays", {k: len(v) for k, v in helm_disp.items()}, "shoulder displays", {k: len(v) for k, v in sh_disp.items()})


if __name__ == "__main__":
    main()
