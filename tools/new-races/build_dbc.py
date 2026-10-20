#!/usr/bin/env python3
"""Goblin (ChrRaces id 9) playable-race DBC rows for a patch-9.MPQ payload.

Input: the effective shipped DBCs (all from patch.MPQ; extract with mpq-inspect into --src).
Output: --out/DBFilesClient/{ChrRaces,CreatureDisplayInfo,CharBaseInfo,CharStartOutfit,CharSections}.dbc
  * CreatureDisplayInfo: two new rows for the shipped character models GoblinMale (model 831) and GoblinFemale
    (model 832); the shipped Goblin row 1140 uses the NPC creature model (164), not the character body.
  * ChrRaces row 9: male/female display -> the new rows, faction template and exploration sound copied from Orc,
    flag 0x1 ("not creatable" in the reference client) cleared.
  * CharBaseInfo: (9, class) rows for the classes the EXE patch allows.
  * CharStartOutfit: race-9 rows cloned from Troll (warrior/hunter/rogue/priest/shaman/mage) and Orc (warlock).
"""
from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path

CLASSES = [1, 3, 4, 5, 7, 8, 9]


def read(p: Path):
    d = p.read_bytes()
    magic, n, fields, rs, ss = struct.unpack_from("<4sIIII", d)
    assert magic == b"WDBC"
    rows = [bytearray(d[20 + i * rs:20 + (i + 1) * rs]) for i in range(n)]
    return fields, rs, rows, d[20 + n * rs:]


def write(p: Path, fields, rs, rows, strings):
    p.parent.mkdir(parents=True, exist_ok=True)
    out = struct.pack("<4sIIII", b"WDBC", len(rows), fields, rs, len(strings)) + b"".join(bytes(r) for r in rows) + strings
    p.write_bytes(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    src, out = Path(a.src), Path(a.out) / "DBFilesClient"
    rep = {}

    # CreatureDisplayInfo: ID, ModelID, SoundID, ExtendedDisplayInfoID, Scale(f), Alpha, Tex0..2(str), SizeClass, Blood, NPCSound
    f, rs, rows, ss = read(src / "CreatureDisplayInfo.dbc")
    ids = {struct.unpack_from("<I", r)[0] for r in rows}
    tmpl = next(r for r in rows if struct.unpack_from("<I", r)[0] == 49)  # Human male player display
    new_ids = []
    nid = max(ids) + 1
    for model in (831, 832):
        r = bytearray(tmpl)
        struct.pack_into("<II", r, 0, nid, model)
        rows.append(r)
        new_ids.append(nid)
        nid += 1
    write(out / "CreatureDisplayInfo.dbc", f, rs, rows, ss)
    rep["CreatureDisplayInfo"] = {"male": new_ids[0], "female": new_ids[1], "template": 49}

    # ChrRaces
    f, rs, rows, ss = read(src / "ChrRaces.dbc")
    orc = next(r for r in rows if struct.unpack_from("<I", r)[0] == 2)
    gob = next(r for r in rows if struct.unpack_from("<I", r)[0] == 9)
    before = list(struct.unpack_from("<6I", gob))
    flags = struct.unpack_from("<I", gob, 4)[0] & ~1
    struct.pack_into("<IIII", gob, 4, flags, struct.unpack_from("<I", orc, 8)[0], struct.unpack_from("<I", orc, 12)[0], new_ids[0])
    struct.pack_into("<I", gob, 20, new_ids[1])
    write(out / "ChrRaces.dbc", f, rs, rows, ss)
    rep["ChrRaces"] = {"before": before, "after": list(struct.unpack_from("<6I", gob))}

    # CharBaseInfo (2-byte rows: race, class)
    f, rs, rows, ss = read(src / "CharBaseInfo.dbc")
    for c in CLASSES:
        rows.append(bytearray([9, c]))
    write(out / "CharBaseInfo.dbc", f, rs, rows, ss)

    # CharStartOutfit (152-byte rows: id u32, race/class/sex/outfit u8, 12 items, 12 displays, 12 invtypes)
    f, rs, rows, ss = read(src / "CharStartOutfit.dbc")
    nid = max(struct.unpack_from("<I", r)[0] for r in rows) + 1
    added = []
    for c in CLASSES:
        donor_race = 2 if c == 9 else 8
        for sex in (0, 1):
            donor = next(r for r in rows if r[4] == donor_race and r[5] == c and r[6] == sex)
            r = bytearray(donor)
            struct.pack_into("<I", r, 0, nid)
            r[4] = 9
            rows.append(r)
            added.append({"id": nid, "class": c, "sex": sex, "donor_race": donor_race})
            nid += 1
    write(out / "CharStartOutfit.dbc", f, rs, rows, ss)
    rep["CharStartOutfit"] = added

    # CharSections (10 u32: ID, Race, Sex, SectionType, Variation, Color, Tex0-2, Flags). The shipped Goblin skins
    # carry flag 0x1 (NPC only), which the appearance ranges skip; add flag-0 copies so the Armory skin dial works.
    f, rs, rows, ss = read(src / "CharSections.dbc")
    nid = max(struct.unpack_from("<I", r)[0] for r in rows) + 1
    added = []
    for r in list(rows):
        rid, race, sex, ty, var, color, t0, t1, t2, flags = struct.unpack_from("<10I", r)
        if race == 9 and flags & 1:
            n = bytearray(r)
            struct.pack_into("<I", n, 0, nid)
            struct.pack_into("<I", n, 36, flags & ~1)
            rows.append(n)
            added.append({"id": nid, "from": rid, "sex": sex, "type": ty, "variation": var, "color": color})
            nid += 1
    write(out / "CharSections.dbc", f, rs, rows, ss)
    rep["CharSections"] = added
    print(json.dumps(rep, indent=1))
    (Path(a.out) / "dbc-report.json").write_text(json.dumps(rep, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
