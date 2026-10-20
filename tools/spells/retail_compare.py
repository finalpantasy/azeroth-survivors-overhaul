"""Join the 161 used spell models against asset-coverage/model-compare.csv (native vs Retail vs Forever headers)."""
import csv
import json
from pathlib import Path

A = Path(r"./analysis")
BS = chr(92)
base = json.load(open(A / "spells/baseline.json"))
rows = {r["path"]: r for r in csv.DictReader(open(A / "asset-coverage/model-compare.csv", encoding="utf8"))}
out = []
for p, i in sorted(base["models"].items()):
    k = p.replace(BS, "/").lower()
    r = rows.get(k)
    if not r:
        out.append((p, "no-row"))
        continue
    out.append((p, i["verts"], i["particles"], i["ribbons"], r["r_present"], r["r_verts"], r["r_part"], r["r_rib"], r["f_present"], r["f_verts"], r["f_part"], r["f_rib"]))
same = chg = absent = 0
cats = {"identical": [], "changed": [], "absent": []}
for o in out:
    if o[1] == "no-row":
        cats["absent"].append(o[0])
        continue
    p, nv, npart, nrib, rp, rv, rpart, rrib, fp, fv, fpart, frib = o
    if rp != "True" and fp != "True":
        cats["absent"].append(p)
    elif (str(nv) == rv or str(nv) == fv) and (str(npart) == rpart or str(npart) == fpart):
        cats["identical"].append(p)
    else:
        cats["changed"].append(o)
print({k: len(v) for k, v in cats.items()})
for o in cats["changed"][:60]:
    print(o)
json.dump(cats, open(A / "spells/retail-compare.json", "w"), indent=1)
