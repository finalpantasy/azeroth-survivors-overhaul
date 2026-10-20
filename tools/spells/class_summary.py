"""Group the inventoried abilities by class (order of ability.* keys in native-english.json) and summarize effects."""
import json
from pathlib import Path

ROOT = Path(r".")
A = ROOT / "analysis/spells"
BS = chr(92)
en = json.load(open(ROOT / "analysis/native-english.json", encoding="utf8"))
keys = [k[8:-5] for k in en if k.startswith("ability.") and k.endswith(".name")]
bounds = [("warrior", "cleave"), ("paladin", "consecration"), ("hunter", "auto_shot"), ("rogue", "sinister_strike"), ("priest", "smite"),
          ("shaman", "lightning_bolt"), ("mage", "fireball"), ("warlock", "shadow_bolt"), ("druid", "wrath"), ("racial", "perception")]
cls = {}
cur = None
bmap = dict((k, c) for c, k in bounds)
for k in keys:
    cur = bmap.get(k, cur)
    cls.setdefault(cur, []).append(k)
base = json.load(open(A / "baseline.json"))
out = {}
for c, ab in cls.items():
    models, tex = set(), set()
    for a in ab:
        for st, ms in base["abilities"].get(a, {}).get("stages", {}).items():
            models.update(ms)
    em = rb = 0
    for m in models:
        i = base["models"].get(m)
        if not i:
            continue
        em += i["particles"]
        rb += i["ribbons"]
        tex.update(t["name"].lower() for t in i["textures"] if t["name"])
    tb = sum(base["textures"].get(t.lower(), {}).get("bytes", 0) for t in tex)
    out[c] = {"abilities": ab, "models": sorted(models), "textures": sorted(tex), "emitters": em, "ribbons": rb, "texture_bytes": tb}
    print(f"{c:8s} abilities {len(ab):2d} models {len(models):3d} textures {len(tex):3d} emitters {em:4d} ribbons {rb:3d} texbytes {tb}")
json.dump(out, open(A / "classes.json", "w"), indent=1)
