#!/usr/bin/env python3
"""Filter the pinned community listfile (FileDataID;path) to the path prefixes relevant to this game.

Names come from a community listfile; they are lookup aids only. Membership of an ID in a given client build is
established separately (casc_probe.py).  Writes <out> as 'fdid;path' lines.
"""
import argparse
import zipfile

PREFIXES = (
    "character/human/", "character/orc/", "character/dwarf/", "character/nightelf/", "character/scourge/",
    "character/tauren/", "character/gnome/", "character/troll/", "character/bloodelf/", "character/draenei/",
    "character/goblin/", "creature/", "item/objectcomponents/", "item/texturecomponents/", "spells/", "particles/",
    "world/", "dungeons/", "environments/", "tileset/", "character/common/", "item/", "textures/", "xtextures/",
    "interface/icons/",
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    n = k = 0
    with zipfile.ZipFile(a.zip) as z, z.open("listfile.csv") as src, open(a.out, "w", encoding="utf8") as dst:
        for raw in src:
            n += 1
            line = raw.decode("utf8", "replace").rstrip("\r\n")
            fid, sep, name = line.partition(";")
            if sep and name.lower().startswith(PREFIXES):
                dst.write(f"{fid};{name.lower()}\n")
                k += 1
    print("read", n, "kept", k)


if __name__ == "__main__":
    main()
