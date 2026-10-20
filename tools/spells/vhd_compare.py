"""Compare VanillaHD Patch-A (pinned, sparse checkout) against the baseline spell models/textures in baseline.json."""
import hashlib
import json
import struct
import sys
from pathlib import Path

ROOT = Path(r".")
A = ROOT / "analysis/spells"
V = A / "vanillahd"
BS = chr(92)
sys.path.insert(0, str(ROOT / "tools/spells"))
from extract_baseline import blp_header  # noqa: E402
from spell_inventory import m2_info  # noqa: E402

idx = {}
for p in V.rglob("*"):
    if p.is_file() and ".git" not in p.parts:
        idx[str(p.relative_to(V)).replace("\\", "/").lower()] = p
base = json.load(open(A / "baseline.json"))
res = {"models": {}, "textures": {}}
for m, info in base["models"].items():
    k = m.replace(BS, "/")
    p = idx.get(k)
    if not p:
        res["models"][m] = {"vhd": False}
        continue
    b = p.read_bytes()
    nb = (A / "baseline/m2" / m.replace(BS, "__")).read_bytes()
    if b[:4] != b"MD20" or struct.unpack_from("<I", b, 4)[0] != 256:
        res["models"][m] = {"vhd": True, "format": b[:4].decode("latin1"), "ver": struct.unpack_from("<I", b, 4)[0] if len(b) > 8 else 0}
        continue
    try:
        vi = m2_info(b)
    except Exception as e:  # noqa: BLE001
        res["models"][m] = {"vhd": True, "err": str(e)}
        continue
    res["models"][m] = {"vhd": True, "identical": b == nb, "bytes": len(b), "native_bytes": len(nb), "verts": vi["verts"], "native_verts": info["verts"],
                        "particles": vi["particles"], "native_particles": info["particles"], "ribbons": vi["ribbons"], "native_ribbons": info["ribbons"],
                        "textures": [t["name"] for t in vi["textures"]], "native_textures": [t["name"] for t in info["textures"]]}
for t, info in base["textures"].items():
    p = idx.get(t.replace(BS, "/"))
    if not p:
        res["textures"][t] = {"vhd": False}
        continue
    b = p.read_bytes()
    h = blp_header(b)
    res["textures"][t] = {"vhd": True, "identical": b == (A / "baseline/blp" / t.replace(BS, "__")).read_bytes(), "w": h.get("w"), "h": h.get("h"),
                          "bytes": len(b), "native_w": info["w"], "native_h": info["h"], "native_bytes": info["bytes"], "enc": h.get("enc"), "alpha_depth": h.get("alpha_depth"), "mips": h.get("mips")}
json.dump(res, open(A / "vhd-compare.json", "w"), indent=1)
M = res["models"]
T = res["textures"]
print("models: vhd present", sum(v["vhd"] for v in M.values()), "identical", sum(1 for v in M.values() if v.get("identical")), "differs", sum(1 for v in M.values() if v.get("identical") is False))
print("textures: vhd present", sum(v["vhd"] for v in T.values()), "identical", sum(1 for v in T.values() if v.get("identical")), "differs", sum(1 for v in T.values() if v.get("identical") is False))
up = [t for t, v in T.items() if v.get("identical") is False]
print("texture changes:")
for t in sorted(up):
    v = T[t]
    print(f"  {t}: {v['native_w']}x{v['native_h']} {v['native_bytes']}B -> {v['w']}x{v['h']} {v['bytes']}B enc{v['enc']}")
