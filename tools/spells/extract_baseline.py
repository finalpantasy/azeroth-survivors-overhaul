#!/usr/bin/env python3
"""Extract the baseline (no installer overlay) models and textures used by the inventoried spell effects.

Reads analysis/spells/inventory.json (model list), pulls each M2 from analysis/spells/baseline-data with
mpq-inspect, re-parses textures/emitters from the baseline bytes, then pulls every referenced BLP and records its
header (size, encoding, alpha, w x h, mips). Writes analysis/spells/baseline/{m2,blp}/ and baseline.json.
"""
import json
import struct
import subprocess
import sys
import ctypes
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(r".")
sys.path.insert(0, str(ROOT / "tools/spells"))
from spell_inventory import m2_info  # noqa: E402

OUT = ROOT / "analysis/spells"
INSPECT = str(ROOT / "tools/mpq-inspect.exe")
BASE = str(OUT / "baseline-data")
BS = chr(92)


def extract(path, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size:
        return True
    p = subprocess.run([INSPECT, BASE, "extract", path, str(dest)], capture_output=True, text=True)
    return dest.exists() and dest.stat().st_size > 0


def blp_header(b):
    if b[:4] != b"BLP2":
        return {"magic": b[:4].decode("latin1")}
    typ, enc, adepth, aenc, hasmips, w, h = struct.unpack_from("<I4B2I", b, 4)
    offs = struct.unpack_from("<16I", b, 20)
    return {"enc": enc, "alpha_depth": adepth, "alpha_enc": aenc, "w": w, "h": h, "mips": sum(1 for o in offs if o), "bytes": len(b)}


def main():
    ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)
    inv = json.load(open(OUT / "inventory.json"))
    models = sorted(inv["models"])
    d = OUT / "baseline"
    with ThreadPoolExecutor(3) as ex:
        okm = list(ex.map(lambda p: (p, extract(p, d / "m2" / p.replace(BS, "__"))), models))
    mt = {}
    texs = set()
    for p, ok in okm:
        if not ok:
            mt[p] = {"missing": True}
            continue
        b = (d / "m2" / p.replace(BS, "__")).read_bytes()
        info = m2_info(b)
        info["bytes"] = len(b)
        info["abilities"] = inv["models"][p]["abilities"]
        mt[p] = info
        for t in info["textures"]:
            if t["name"]:
                texs.add(t["name"])
    with ThreadPoolExecutor(3) as ex:
        okt = list(ex.map(lambda n: (n, extract(n, d / "blp" / n.replace(BS, "__").lower())), sorted(texs)))
    bt = {}
    for n, ok in okt:
        if not ok:
            bt[n.lower()] = {"missing": True}
            continue
        bt[n.lower()] = blp_header((d / "blp" / n.replace(BS, "__").lower()).read_bytes())
    json.dump({"models": mt, "textures": bt, "abilities": inv["abilities"]}, open(OUT / "baseline.json", "w"), indent=1)
    print("models", len(mt), "missing", sum(1 for v in mt.values() if v.get("missing")), "textures", len(bt),
          "missing", sum(1 for v in bt.values() if v.get("missing")), "bytes", sum(v.get("bytes", 0) for v in bt.values()))


if __name__ == "__main__":
    main()
