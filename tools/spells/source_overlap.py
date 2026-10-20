"""Which used spell textures have an HD candidate in each pinned source tree (names only; no downloads)."""
import json
from pathlib import Path

A = Path(r"./analysis")
BS = chr(92)


def norm(p):
    return p.lower().replace("/", BS)


inv = json.load(open(A / "spells/inventory.json"))
names = set()
for i in inv["models"].values():
    for t in i["textures"]:
        if t["name"]:
            names.add(norm(t["name"]))
vh = {norm(x["path"]): x for x in json.load(open(A / "vanillahd-spell-candidates.json"))}
yt = {norm(x["path"]): x for x in json.load(open(A / "modern-spell-tree.json"))["tree"] if x["type"] == "blob"}
print("used textures", len(names), "vanillahd entries", len(vh), "differ", sum(1 for x in vh.values() if x["size"] != x["native_size"]))
inter = [n for n in names if n in vh]
diff = [n for n in inter if vh[n]["size"] != vh[n]["native_size"]]
print("usedused∩vanillahdvanillahd", len(inter), "size-differs", len(diff))
for n in sorted(diff):
    print("  ", n, vh[n]["native_size"], "->", vh[n]["size"])
print("yuritsuki blobs", len(yt), "used&", sum(1 for n in names if n in yt))
yd = [n for n in names if n in yt and yt[n].get("size") != yt[n].get("native_size", yt[n].get("size"))]
print("none in either:", len([n for n in names if n not in vh and n not in yt]))
print(sorted(n for n in names if n not in vh and n not in yt))
