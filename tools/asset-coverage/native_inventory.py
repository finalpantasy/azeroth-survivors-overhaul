#!/usr/bin/env python3
"""Native asset inventory for Azeroth Survivors from the game's own MPQ chain/DBCs (read-only inputs).

Inputs (all produced by prior read-only mpq-inspect calls into --work): mpq-list.txt, dbc/*.csv, raw/*.dbc, m2-census.csv.
Output: native-inventory.json (+ prints a compact summary).
"""
import argparse
import collections
import csv
import json
import re
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dbclib import Dbc  # noqa: E402

BS = chr(92)
RACES = {1: "Human", 2: "Orc", 3: "Dwarf", 4: "NightElf", 5: "Scourge", 6: "Tauren", 7: "Gnome", 8: "Troll"}
CLASSES = {1: "Warrior", 2: "Paladin", 3: "Hunter", 4: "Rogue", 5: "Priest", 7: "Shaman", 8: "Mage", 9: "Warlock", 11: "Druid"}
SECTION = {0: "skin", 1: "face", 2: "facialhair", 3: "hair", 4: "underwear"}


def norm(p):
    return p.replace("/", BS).lower()


def load_list(path):
    files = {}
    for line in open(path, encoding="utf8", errors="replace"):
        m = re.match(r"\s*(\d+)\s+(.*)$", line.rstrip("\n"))
        if m:
            files[norm(m.group(2))] = (m.group(2), int(m.group(1)))
    return files


def load_census(path):
    out = {}
    if not Path(path).exists():
        return out
    for r in csv.DictReader(open(path, encoding="utf8")):
        out[norm(r["path"])] = r
    return out


def stat_models(paths, census):
    n = v = b = t = sz = p = 0
    mx_v = mx_b = mx_t = 0
    missing = 0
    for q in paths:
        r = census.get(q)
        if not r:
            missing += 1
            continue
        n += 1
        sz += int(r["bytes"])
        vv, bb, tt = int(r["verts"] or 0), int(r["bones"] or 0), int(r["triangles"] or 0)
        v += vv
        b += bb
        t += tt
        p += int(r["particles"] or 0) + int(r["ribbons"] or 0)
        mx_v, mx_b, mx_t = max(mx_v, vv), max(mx_b, bb), max(mx_t, tt)
    return dict(models=n, no_census=missing, bytes=sz, verts=v, triangles=t, max_verts=mx_v, max_bones=mx_b,
                max_triangles=mx_t, emitters=p)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    ap.add_argument("--english", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    W = Path(a.work)
    files = load_list(W / "mpq-list.txt")
    census = load_census(W / "m2-census.csv")
    inv = {"shipped_files": len(files)}

    # --- characters ---
    cs = list(csv.DictReader(open(W / "dbc/CharSections.csv", encoding="utf8")))
    chars = {}
    for r in cs:
        race, sex, sec = int(r["RaceID"]), int(r["SexID"]), int(r["SectionType"])
        if race not in RACES:
            continue
        key = f"{RACES[race]}/{'Male' if sex == 0 else 'Female'}"
        e = chars.setdefault(key, {})
        s = e.setdefault(SECTION[sec], {"rows": 0, "variations": set(), "colors": set(), "textures": set()})
        s["rows"] += 1
        s["variations"].add(int(r["VariationIndex"]))
        s["colors"].add(int(r["ColorIndex"]))
        for k in ("TextureName0", "TextureName1", "TextureName2"):
            if r[k]:
                s["textures"].add(norm(r[k]))
    char_out = {}
    for key, e in sorted(chars.items()):
        race, g = key.split("/")
        mp = f"character{BS}{race.lower()}{BS}{g.lower()}{BS}{race.lower()}{g.lower()}.m2"
        cen = census.get(mp, {})
        ent = {"model": mp, "model_present": mp in files,
               "model_stats": {k: cen.get(k) for k in ("bytes", "verts", "bones", "seq", "triangles", "attach")}}
        tex_present = 0
        tex_total = 0
        for sec, s in e.items():
            ent[sec] = {"rows": s["rows"], "variations": len(s["variations"]), "colors": len(s["colors"]), "textures": len(s["textures"])}
            for t in s["textures"]:
                tex_total += 1
                if t in files:
                    tex_present += 1
        ent["textures_total"] = tex_total
        ent["textures_shipped"] = tex_present
        char_out[key] = ent
    inv["characters"] = char_out
    # hair geosets / facial hair
    ch = list(csv.DictReader(open(W / "dbc/CharHairGeosets.csv", encoding="utf8")))
    cf = list(csv.DictReader(open(W / "dbc/CharacterFacialHairStyles.csv", encoding="utf8")))
    inv["hair_geoset_rows"] = len(ch)
    inv["facial_hair_style_rows"] = len(cf)
    # character folder files
    cfiles = collections.Counter()
    for p in files:
        if p.startswith("character" + BS):
            cfiles[p.rsplit(".", 1)[-1]] += 1
    inv["character_folder_ext"] = dict(cfiles)
    inv["goblin_note"] = "Goblin (race 9) models/folder ship, but the game's 8 races exclude Goblin"

    # --- starter outfits (CharStartOutfit) ---
    d = Dbc(W / "raw/CharStartOutfit.dbc")
    outfits = []
    items_by_class = collections.defaultdict(set)
    disp_used = collections.Counter()
    for i in range(d.count):
        o = d.base + i * d.rec_size
        rid = struct.unpack_from("<I", d.data, o)[0]
        race, cls, sex, outfit = struct.unpack_from("<4B", d.data, o + 4)
        items = struct.unpack_from("<12i", d.data, o + 8)
        disps = struct.unpack_from("<12i", d.data, o + 56)
        invt = struct.unpack_from("<12i", d.data, o + 104)
        outfits.append(dict(race=race, cls=cls, sex=sex, items=[x for x in items if x > 0], displays=[x for x in disps if x > 0],
                            invtypes=[x for x, it in zip(invt, items) if it > 0]))
        for it, di in zip(items, disps):
            if it > 0:
                items_by_class[cls].add((it, di))
                disp_used[di] += 1
    inv["start_outfits"] = {"rows": len(outfits), "classes": sorted({o["cls"] for o in outfits}),
                           "races": sorted({o["race"] for o in outfits}), "unique_items": sum(len(v) for v in items_by_class.values()),
                           "unique_displays": len(disp_used)}
    json.dump(outfits, open(Path(a.out).with_name("start-outfits.json"), "w"))

    # --- item displays ---
    idi = list(csv.DictReader(open(W / "dbc/ItemDisplayInfo.csv", encoding="utf8")))
    idx = collections.defaultdict(list)
    for p in files:
        if p.startswith("item" + BS + "objectcomponents" + BS):
            idx[p.rsplit(BS, 1)[-1]].append(p)
    models = collections.Counter()
    unresolved = 0
    resolved = {}
    for r in idi:
        for f in ("ModelNameLeft", "ModelNameRight"):
            nm = r[f].lower().replace(".mdx", ".m2").replace(".mdl", ".m2")
            if nm:
                models[nm] += 1
                if nm not in resolved:
                    resolved[nm] = idx.get(nm, [])
                    if not resolved[nm]:
                        unresolved += 1
    cat = collections.defaultdict(list)
    for nm, ps in resolved.items():
        for p in ps[:1]:
            cat[p.split(BS)[2]].append(p)
    item_models = {k: stat_models(v, census) for k, v in sorted(cat.items())}
    tex_comp = collections.Counter()
    for p in files:
        if p.startswith("item" + BS + "texturecomponents" + BS):
            tex_comp[p.split(BS)[2]] += 1
    comp_cols = ["ArmUpperTexture", "ArmLowerTexture", "HandTexture", "TorsoUpperTexture", "TorsoLowerTexture", "LegUpperTexture", "LegLowerTexture", "FootTexture"]
    used_tex = set()
    for r in idi:
        for c in comp_cols:
            if r[c]:
                used_tex.add((c, r[c].lower()))
    inv["items"] = {"display_rows": len(idi), "unique_model_names": len(models), "model_names_unresolved_in_shipped": unresolved,
                    "model_stats_by_folder": item_models, "texture_component_files": dict(tex_comp),
                    "unique_body_texture_names": len(used_tex), "id_range": [idi[0]["ID"], idi[-1]["ID"]],
                    "start_outfit_displays": len(disp_used)}
    # shield/weapon sets by naming
    wp = collections.Counter()
    for nm in models:
        m = re.match(r"([a-z]+)_([a-z0-9]+)_", nm)
        wp[m.group(1) if m else nm.split("_")[0]] += 1
    byf = collections.defaultdict(list)
    for q in census:
        parts = q.split(BS)
        byf[BS.join(parts[:3]) if parts[0] == "item" else parts[0]].append(q)
    inv["m2_by_folder"] = {k: stat_models(v, census) for k, v in sorted(byf.items()) if len(v) > 0}
    inv["items"]["model_name_prefix_counts"] = dict(wp.most_common(40))

    # --- creatures ---
    cm = list(csv.DictReader(open(W / "dbc/CreatureModelData.csv", encoding="utf8")))
    cd = list(csv.DictReader(open(W / "dbc/CreatureDisplayInfo.csv", encoding="utf8")))
    cmodel = {}
    for r in cm:
        p = norm(r["ModelName"]).replace(".mdx", ".m2").replace(".mdl", ".m2")
        cmodel[int(r["ID"])] = p
    present = {i: p for i, p in cmodel.items() if p in files}
    disp_on_present = sum(1 for r in cd if int(r["ModelID"]) in present)
    inv["creatures"] = {"model_rows": len(cm), "model_rows_shipped": len(present), "display_rows": len(cd),
                        "display_rows_on_shipped_model": disp_on_present,
                        "shipped_m2_stats": stat_models(set(present.values()), census),
                        "folder_count": len({p.split(BS)[1] for p in present.values()})}
    c_tex = collections.Counter()
    for p in files:
        if p.startswith("creature" + BS):
            c_tex[p.rsplit(".", 1)[-1]] += 1
    inv["creatures"]["creature_folder_files_by_ext"] = dict(c_tex)
    json.dump({str(k): v for k, v in present.items()}, open(Path(a.out).with_name("creature-models-shipped.json"), "w"))

    # --- spells ---
    veff = Dbc(W / "raw/SpellVisualEffectName.dbc")
    effects = {}
    for r in veff.rows():
        effects[r[0]] = norm(veff.string(r[2])).replace(".mdl", ".m2").replace(".mdx", ".m2")
    spell_files = [p for p in files if p.startswith("spells" + BS) and p.endswith(".m2")]
    part_files = [p for p in files if p.startswith("particles" + BS) and p.endswith(".m2")]
    eff_present = {i: p for i, p in effects.items() if p in files}
    inv["spells"] = {"effect_name_rows": len(effects), "effect_models_shipped": len(eff_present),
                     "spell_m2_in_spells_folder": stat_models(spell_files, census),
                     "particles_folder": stat_models(part_files, census),
                     "spell_texture_blp": sum(1 for p in files if p.startswith("spells" + BS) and p.endswith(".blp")),
                     "enchant_blp": sum(1 for p in files if p.startswith("spells" + BS + "enchantments"))}
    # abilities in game -> vanilla spell names
    en = json.load(open(a.english, encoding="utf8"))
    abil = {k[len("ability."):-len(".name")]: v for k, v in en.items() if k.startswith("ability.") and k.endswith(".name")}
    sp = Dbc(W / "raw/Spell.dbc")
    byname = collections.defaultdict(list)
    for r in sp.rows():
        byname[sp.string(r[120]).lower()].append((r[0], r[115]))
    sv = Dbc(W / "raw/SpellVisual.dbc")
    svkit = Dbc(W / "raw/SpellVisualKit.dbc")
    kit_eff = {}
    for r in svkit.rows():
        kit_eff[r[0]] = [x for x in r[3:15] if x not in (0, 0xFFFFFFFF)]
    visual = {}
    for r in sv.rows():
        visual[r[0]] = r
    found = {}
    missing = []
    used_effects = set()
    for key, name in abil.items():
        hits = byname.get(name.lower(), [])
        vis = sorted({v for _, v in hits if v})
        if not hits:
            missing.append((key, name))
            continue
        e = set()
        for v in vis:
            row = visual.get(v)
            if not row:
                continue
            for kid in (row[1], row[2], row[3], row[4], row[5], row[6], row[14], row[15]):
                for ef in kit_eff.get(kid, []):
                    e.add(ef)
            if row[7] and row[8]:
                e.add(row[8])
        used_effects |= e
        found[key] = {"name": name, "spell_ids": [h[0] for h in hits][:6], "visuals": vis[:6], "effect_name_ids": sorted(e)}
    models_for_abilities = {effects[i] for i in used_effects if i in effects}
    inv["spells"]["abilities_in_game"] = len(abil)
    inv["spells"]["abilities_matched_by_name"] = len(found)
    inv["spells"]["abilities_unmatched"] = [m[1] for m in missing]
    inv["spells"]["ability_effect_models"] = len(models_for_abilities)
    inv["spells"]["ability_effect_models_stats"] = stat_models([m for m in models_for_abilities if m in files], census)
    json.dump({"abilities": found, "effect_models": sorted(models_for_abilities)}, open(Path(a.out).with_name("ability-visuals.json"), "w"), indent=1)

    # --- world ---
    world = collections.Counter()
    for p, (orig, sz) in files.items():
        parts = p.split(BS)
        if p.endswith(".adt"):
            world["adt"] += 1
        elif p.endswith(".wmo"):
            world["wmo"] += 1
        elif p.endswith(".m2") and parts[0] in ("world", "dungeons", "environments"):
            world["doodad_m2"] += 1
        elif p.endswith(".blp") and parts[0] in ("tileset", "world", "dungeons", "xtextures", "textures", "environments"):
            world["world_blp"] += 1
    inv["world"] = dict(world)
    inv["world"]["doodad_m2_stats"] = stat_models([p for p in files if p.endswith(".m2") and p.split(BS)[0] in ("world", "dungeons", "environments")], census)
    inv["world"]["maps"] = {"Azeroth": 100, "Kalimdor": 77, "PVPZone01 (WSG)": 41, "PVPZone03 (AB)": 20, "PVPZone04 (AV)": 37,
                            "MonasteryInstances": 26, "Stratholme Raid": 18}
    ext = collections.Counter(p.rsplit(".", 1)[-1] for p in files if p.startswith("interface" + BS))
    inv["interface"] = dict(ext)

    def tr(o):
        if isinstance(o, set):
            return sorted(o)
        raise TypeError
    json.dump(inv, open(a.out, "w"), indent=1, default=tr)
    print(json.dumps(inv, indent=1, default=tr)[:6000])


if __name__ == "__main__":
    main()
