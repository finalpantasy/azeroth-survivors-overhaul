#!/usr/bin/env python3
"""W10: compare the pinned VanillaHD Patch-B tree (local GitHub archive zip the owner already has) with the
installed game's file chain and with the doodads/WMOs/textures the playable maps reference.

Read-only. Needs: analysis/world/world-inventory.json (inventory_world.py), analysis/world/chain-list.txt
(mpq-inspect list of Data/), the Patch-B zip. Writes analysis/world/patchb-overlap.json.
"""
import json, re, struct, sys, zipfile, hashlib, collections
from pathlib import Path

ROOT = Path(r".")
ZIP = Path(r"inputs/wow-data/hd-source/Patch-B-a112a137.zip")
OUT = ROOT / "analysis" / "world"
BS = chr(92)


def chain():
    d = {}
    for l in open(OUT / "chain-list.txt", encoding="utf8", errors="replace"):
        m = re.match(r"\s*(\d+)\s+(.*)", l.rstrip("\n"))
        if m:
            d[m.group(2).lower()] = int(m.group(1))
    return d


def main():
    ch = chain()
    inv = json.load(open(OUT / "world-inventory.json"))
    used_m2 = set(); used_wmo = set()
    for m, r in inv.items():
        for p, _ in r["top_doodads"]:
            pass
    z = zipfile.ZipFile(ZIP)
    items = {}
    for i in z.infolist():
        if i.is_dir():
            continue
        p = i.filename.split("/", 1)[1]
        items[p.replace("/", BS).lower()] = i
    ext = collections.Counter()
    rows = []
    for p, i in items.items():
        if p.startswith("tileset" + BS) or p.startswith("(") or p.startswith("dungeons"):
            continue
        e = p.rsplit(".", 1)[-1]
        in_chain = p in ch
        row = {"path": p, "ext": e, "bytes": i.file_size, "in_game": in_chain, "game_bytes": ch.get(p)}
        if e == "m2":
            b = z.read(i)[:8]
            row["magic"] = b[:4].decode("latin1"); row["ver"] = struct.unpack_from("<I", b, 4)[0] if len(b) >= 8 else None
        if e == "wmo":
            b = z.read(i)
            # MVER chunk: 'REVM' size 4 version
            row["wmo_ver"] = struct.unpack_from("<I", b, 8)[0] if b[:4] == b"REVM" else None
        rows.append(row)
    json.dump(rows, open(OUT / "patchb-world-files.json", "w"), indent=0)
    c = collections.Counter((r["ext"], r["in_game"]) for r in rows)
    print(sorted(c.items()))
    print(collections.Counter((r.get("magic"), r.get("ver")) for r in rows if r["ext"] == "m2"))
    print(collections.Counter(r.get("wmo_ver") for r in rows if r["ext"] == "wmo"))


if __name__ == "__main__":
    main()
