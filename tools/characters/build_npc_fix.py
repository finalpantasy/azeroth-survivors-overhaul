#!/usr/bin/env python3
"""NPC-contamination fix: keep native CharSections rows for NPCs, HD rows for the player.

The HD build rewrites the type 0/1/2/4 texture cells of CharSections rows into HD aliases; NPCs of the same
race/sex that composite from CreatureDisplayInfoExtra look up the SAME rows and so wear HD skin/face/facial hair
on their native models. Fix (data only): add a hidden (flags|=1) native duplicate of every row at Color+100 and
shift SkinID / HairColorID by +100 in every player-race CreatureDisplayInfoExtra row whose shifted keys exist.
Player customization still sees only the unshifted rows.

  build_npc_fix.py [--out DIR]     writes DIR/DBFilesClient/{CharSections,CreatureDisplayInfoExtra}.dbc
"""
import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from dbclib import DBC

R = Path(__file__).resolve().parents[2]
ND = R / "analysis/characters/native-dbc"
SHIFT = 100


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(R / "mod-source/characters-hd-npcfix/payload"))
    a = ap.parse_args()
    nat = DBC((ND / "CharSections.dbc").read_bytes())
    hd = DBC((R / "mod-source/characters-hd/payload/DBFilesClient/CharSections.dbc").read_bytes())
    ext = DBC((ND / "CreatureDisplayInfoExtra.dbc").read_bytes())
    nid = max(r[0] for r in hd.rows) + 1
    have = {(r[1], r[2], r[3], r[4], r[5]) for r in hd.rows}
    added = 0
    for r in nat.rows:
        key = (r[1], r[2], r[3], r[4], r[5] + SHIFT)
        if max(r[5], 0) >= SHIFT:
            raise SystemExit(f"color already >= {SHIFT}: {r}")
        d = list(r)
        d[0] = nid
        nid += 1
        d[5] = r[5] + SHIFT
        for c in (6, 7, 8):
            d[c] = hd.add_string(nat.string(r[c]))
        d[9] = r[9] | 1
        hd.rows.append(d)
        have.add(key)
        added += 1
    races = {(r[1], r[2]) for r in nat.rows}
    shifted_skin = shifted_hc = 0
    for e in ext.rows:
        race, sex, skin, face, hs, hc, fh = e[1], e[2], e[3], e[4], e[5], e[6], e[7]
        if (race, sex) not in races:
            continue
        if (race, sex, 0, 0, skin + SHIFT) in have:
            e[3] = skin + SHIFT
            shifted_skin += 1
        if (race, sex, 2, fh, hc + SHIFT) in have or (race, sex, 3, hs, hc + SHIFT) in have:
            e[6] = hc + SHIFT
            shifted_hc += 1
    out = Path(a.out) / "DBFilesClient"
    out.mkdir(parents=True, exist_ok=True)
    (out / "CharSections.dbc").write_bytes(hd.pack())
    (out / "CreatureDisplayInfoExtra.dbc").write_bytes(ext.pack())
    print(f"added {added} hidden native rows; CDIE rows shifted: skin {shifted_skin}, hair color {shifted_hc} of {len(ext.rows)}")


if __name__ == "__main__":
    main()
