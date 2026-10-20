"""Generate plan.json (S1: VanillaHD drop-in models) from s1/manifest.json + s1/risk.json with explicit exclusions."""
import json
from pathlib import Path

ROOT = Path(r".")
A = ROOT / "analysis/spells"
BS = chr(92)
man = json.load(open(A / "s1/manifest.json"))
risk = json.load(open(A / "s1/risk.json"))
vhd = A / "vanillahd"
idx = {str(p.relative_to(vhd)).replace("\\", "/").lower(): p for p in vhd.rglob("*") if p.is_file() and ".git" not in p.parts}
EXCLUDE = {
    "spells\\fear_state_base.m2": "durations changed and needs Retail-only doodad textures",
    "spells\\moonfire_impact_base.m2": "12.0 s clamp becomes 4.47 s loop",
    "spells\\presenceofmind_cast_base.m2": "animation ids differ (158 -> 144)",
    "spells\\firenova_state.m2": "needs creature\\ridingphoenix\\ember.blp (not in native or VanillaHD)",
    "spells\\holy_hammer_missile.m2": "needs item\\objectcomponents\\shield\\armorreflect4.blp (checked later)",
    "spells\\innerfire_base.m2": "needs creature\\gyrocopter\\smoothreflect01.blp (checked later)",
}
plan = {"models": [], "textures": [], "excluded": EXCLUDE}
for m, info in sorted(man["models"].items()):
    if m in EXCLUDE:
        continue
    r = risk[m]
    bad = [f for f in r["flags"] if "dur" in f]
    if bad:
        plan["excluded"][m] = "; ".join(bad)
        continue
    cand = (A / "s1/tree" / m.replace(BS, "/")).resolve()
    texmap = {}
    from sys import path
    path.insert(0, str(ROOT / "tools/spells"))
    import m2_patch
    for t in m2_patch.textures(cand.read_bytes()):
        nm = t["name"].lower()
        k = nm.replace(BS, "/")
        if nm and k in idx and nm in man["textures"]:
            texmap[nm] = str(idx[k])
    plan["models"].append({"native": m, "source": str(cand), "alias_dir": "Spells" + BS + "AzerothSurvivorsHD" + BS + "S1", "textures": texmap,
                           "clamp_from_native": True, "note": "VanillaHD Patch-A drop-in"})
json.dump(plan, open(A / "plan.json", "w"), indent=1)
print("models", len(plan["models"]), "excluded", len(plan["excluded"]))
