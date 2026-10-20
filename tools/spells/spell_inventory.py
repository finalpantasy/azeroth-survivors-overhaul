#!/usr/bin/env python3
"""W9 spells: ability -> SpellVisual stages -> effect models -> textures, from the BASELINE native chain.

Read-only. Uses mpq-inspect.exe `spellvis` (the engine's own chain walker) per distinct SpellVisual id, then parses
the cached native M2 files (m2cache) for texture names, particle/ribbon counts and vertex counts.
Output: analysis/spells/inventory.json
"""
import json
import re
import struct
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(r".")
sys.path.insert(0, str(ROOT / "tools/asset-coverage"))
from dbclib import Dbc  # noqa: E402

W = ROOT / "analysis/asset-coverage/work"
OUT = ROOT / "analysis/spells"
INSPECT = str(ROOT / "tools/mpq-inspect.exe")
BASE = str(OUT / "baseline-data")


def u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def m2_info(b):
    """Classic MD20 v256: texture file names, emitters (texture index), verts."""
    tc, to = u32(b, 0x5C), u32(b, 0x60)
    tex = []
    for i in range(tc):
        t, fl, nl, no = struct.unpack_from("<4I", b, to + i * 16)
        name = b[no:no + nl].split(b"\0")[0].decode("latin1") if nl else ""
        tex.append({"type": t, "name": name})
    pc, po = u32(b, 0x13C), u32(b, 0x140)
    rc, ro = u32(b, 0x134), u32(b, 0x138)
    parts = []
    # M2ParticleEmitter (classic) is 0x1DC bytes; texture index u16 at +0x16
    for i in range(pc):
        o = po + i * 0x1DC
        if o + 0x1DC <= len(b):
            parts.append(struct.unpack_from("<H", b, o + 0x16)[0])
    ribs = []
    for i in range(rc):
        o = ro + i * 0xDC
        if o + 0xDC <= len(b):
            # ribbon: id,flags,bone,pos(3f),textureRefs(count,off) ... read first tex ref
            tcnt, toff = struct.unpack_from("<2I", b, o + 0x14)
            if tcnt and toff + 2 <= len(b):
                ribs.append(struct.unpack_from("<H", b, toff)[0])
    return {"verts": u32(b, 0x44), "textures": tex, "particles": pc, "ribbons": rc, "part_tex": parts, "ribbon_tex": ribs}


def main():
    import ctypes
    ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)
    en = json.load(open(ROOT / "analysis/native-english.json", encoding="utf8"))
    abil = {k[8:-5]: v for k, v in en.items() if k.startswith("ability.") and k.endswith(".name")}
    sp = Dbc(W / "raw/Spell.dbc")
    byname = {}
    for r in sp.rows():
        byname.setdefault(sp.string(r[120]).lower(), []).append((r[0], r[115]))
    vis_spell = {}
    ab_vis = {}
    for key, name in abil.items():
        hits = byname.get(name.lower(), [])
        vs = sorted({v for _, v in hits if v})
        ab_vis[key] = {"name": name, "visuals": vs, "n_spells": len(hits)}
        for sid, v in hits:
            if v and v not in vis_spell:
                vis_spell[v] = sid

    def run(v):
        p = subprocess.run([INSPECT, BASE, "spellvis", str(vis_spell[v])], capture_output=True, text=True)
        return v, p.stdout

    with ThreadPoolExecutor(3) as ex:
        res = dict(ex.map(run, sorted(vis_spell)))
    vis_models = {}
    for v, txt in res.items():
        stage = None
        items = []
        for ln in txt.splitlines():
            m = re.match(r"\s{2}(precast|cast|impact|state|channel|missile|[a-z]+)\s", ln)
            if m and not ln.strip().startswith("attach"):
                stage = m.group(1)
            for mm in re.finditer(r"->\s+(\S+\.(?:mdx|m2|mdl))", ln, re.I):
                items.append((stage, mm.group(1)))
        vis_models[v] = items
    models = {}
    for key, a in ab_vis.items():
        stages = {}
        for v in a["visuals"]:
            for st, p in vis_models.get(v, []):
                stages.setdefault(st, set()).add(p.lower().replace(".mdx", ".m2").replace(".mdl", ".m2"))
        a["stages"] = {k: sorted(v) for k, v in stages.items()}
        for s in stages.values():
            for p in s:
                models.setdefault(p, set()).add(key)
    cache = W / "m2cache"
    mt = {}
    for p, abs_ in sorted(models.items()):
        f = cache / p.replace("\\", "__")
        cand = [x for x in cache.glob("*") if x.name.lower() == p.replace("\\", "__").lower()]
        if not cand:
            mt[p] = {"missing_in_cache": True, "abilities": sorted(abs_)}
            continue
        b = cand[0].read_bytes()
        try:
            info = m2_info(b)
        except Exception as e:  # noqa: BLE001
            info = {"error": str(e)}
        info["bytes"] = len(b)
        info["abilities"] = sorted(abs_)
        mt[p] = info
    json.dump({"abilities": ab_vis, "models": mt}, open(OUT / "inventory.json", "w"), indent=1)
    tex = {}
    for p, i in mt.items():
        for t in i.get("textures", []):
            if t["name"]:
                tex.setdefault(t["name"].lower(), set()).add(p)
    print("abilities", len(ab_vis), "visuals", len(vis_spell), "models", len(mt), "missing", sum(1 for i in mt.values() if i.get("missing_in_cache")),
          "textures", len(tex))


if __name__ == "__main__":
    main()
