#!/usr/bin/env python3
"""W10: for Patch-B world files that exist in the game, compare old vs new: BLP dimensions/bytes and M2 vertex counts.
Native bytes come from the installed chain via mpq-inspect extract into analysis/world/native-cache."""
import json, struct, zipfile, subprocess, sys, collections, os, ctypes
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

ROOT = Path(r".")
ZIP = Path(r"inputs/wow-data/hd-source/Patch-B-a112a137.zip")
OUT = ROOT / "analysis" / "world"
NC = OUT / "native-cache"
INSPECT = ROOT / "tools" / "mpq-inspect.exe"
DATA = ROOT / "Data"
BS = chr(92)


def blp_info(b):
    if b[:4] != b"BLP2":
        return None
    enc, ab = b[8], b[9]
    w, h = struct.unpack_from("<II", b, 12)
    return {"enc": enc, "alpha_bits": ab, "w": w, "h": h}


def m2_info(b):
    if b[:4] != b"MD20":
        return None
    nv, ov = struct.unpack_from("<II", b, 0x44)
    nt, ot = struct.unpack_from("<II", b, 0x5C)
    nb = struct.unpack_from("<I", b, 0x34)[0]
    ns = struct.unpack_from("<I", b, 0x1C)[0]
    texs = []
    for i in range(nt):
        if ot + 16 * i + 16 > len(b):
            texs.append("<bad>"); continue
        ty, fl, ln, of = struct.unpack_from("<IIII", b, ot + 16 * i)
        if ty == 0 and ln:
            texs.append(b[of:of + ln].rstrip(bytes(1)).decode("latin1"))
        else:
            texs.append("<type%d>" % ty)
    return {"verts": nv, "bones": nb, "seqs": ns, "textures": texs}


def extract(p):
    d = NC / p.replace(BS, "/")
    d.parent.mkdir(parents=True, exist_ok=True)
    if not d.exists():
        subprocess.run([str(INSPECT), str(DATA), "extract", p, str(d)], capture_output=True)
    return d.read_bytes() if d.exists() else None


def main():
    if os.name == "nt":
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)
    rows = json.load(open(OUT / "patchb-world-files.json"))
    z = zipfile.ZipFile(ZIP)
    names = {i.filename.split("/", 1)[1].replace("/", BS).lower(): i for i in z.infolist() if not i.is_dir()}
    sel = [r for r in rows if r["in_game"] and r["ext"] in ("m2", "blp", "wmo") and (r["ext"] != "m2" or r.get("ver") == 256)]
    with ThreadPoolExecutor(3) as ex:
        nat = list(ex.map(lambda r: extract(r["path"]), sel))
    out = []
    for r, nb in zip(sel, nat):
        nb_ = nb or b""
        hb = z.read(names[r["path"]])
        e = {"path": r["path"], "ext": r["ext"], "new_bytes": len(hb), "old_bytes": len(nb_)}
        if r["ext"] == "blp":
            e["new"] = blp_info(hb); e["old"] = blp_info(nb_)
        elif r["ext"] == "m2":
            e["new"] = m2_info(hb); e["old"] = m2_info(nb_)
        out.append(e)
    json.dump(out, open(OUT / "patchb-vs-native.json", "w"), indent=0)
    blp = [e for e in out if e["ext"] == "blp" and e["new"] and e["old"]]
    c = collections.Counter(((e["old"]["w"], e["old"]["h"]), (e["new"]["w"], e["new"]["h"])) for e in blp)
    print("BLP old->new dims", c.most_common(12))
    print("BLP bytes old", sum(e["old_bytes"] for e in blp), "new", sum(e["new_bytes"] for e in blp))
    m2 = [e for e in out if e["ext"] == "m2" and e["new"] and e["old"]]
    same = sum(1 for e in m2 if e["new"]["verts"] == e["old"]["verts"])
    bigger = sum(1 for e in m2 if e["new"]["verts"] > 1.25 * e["old"]["verts"])
    print("M2 n", len(m2), "same verts", same, ">1.25x verts", bigger, "old verts", sum(e["old"]["verts"] for e in m2), "new verts", sum(e["new"]["verts"] for e in m2))
    print("M2 bytes old", sum(e["old_bytes"] for e in m2), "new", sum(e["new_bytes"] for e in m2))


if __name__ == "__main__":
    main()
