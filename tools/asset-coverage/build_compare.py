#!/usr/bin/env python3
"""Join native census + retail/forever header parses + their .skin counts into model-compare.csv (one row per native M2)."""
import csv
import json
import sys
from pathlib import Path

W = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("analysis/asset-coverage")
BS = chr(92)


def load(p):
    return json.loads((W / "work" / p).read_text(encoding="utf8"))["results"]


def side(fid, parse, skins):
    r = parse.get(fid, {})
    m = r.get("m2")
    if not m:
        return dict(present=("bytes" in r), bytes=r.get("bytes", ""))
    s = skins.get(str(m["sfid"][0]), {}).get("skin", {}) if m.get("sfid") else {}
    return dict(present=True, bytes=r["bytes"], ver=m["version"], verts=m["verts"], bones=m["bones"], keybones=m["keybones"],
                seq=m["seq"], tex=m["textures"], tris=s.get("triangles", ""), subm=s.get("submeshes", ""),
                skel=("SKID" in m.get("chunks", {})), part=m["particles"], rib=m["ribbons"], types=m["texture_types"])


def main():
    rp, fp = load("parse-retail.json"), load("parse-forever.json")
    rs, fs = load("skins-retail.json"), load("skins-forever.json")
    cen = {r["path"].lower().replace(BS, "/"): r for r in csv.DictReader(open(W / "work" / "m2-census.csv", encoding="utf8"))}
    cols = ["path", "fdid", "n_bytes", "n_verts", "n_bones", "n_tris", "n_tex", "n_seq"]
    for t in ("r", "f"):
        cols += [f"{t}_{k}" for k in ("present", "ver", "bytes", "verts", "bones", "seq", "tex", "tris", "subm", "skel", "part", "rib")]
    out = csv.writer(open(W / "model-compare.csv", "w", newline="", encoding="utf8"))
    out.writerow(cols)
    for r in csv.DictReader(open(W / "native-to-fdid.csv", encoding="utf8"), delimiter=";"):
        if r["ext"] != "m2":
            continue
        p, fid = r["native_path"], r["fdid"]
        c = cen.get(p, {})
        row = [p, fid, r["bytes"], c.get("verts", ""), c.get("bones", ""), c.get("triangles", ""), c.get("textures", ""), c.get("seq", "")]
        for parse, skins in ((rp, rs), (fp, fs)):
            s = side(fid, parse, skins)
            row += [s.get(k, "") for k in ("present", "ver", "bytes", "verts", "bones", "seq", "tex", "tris", "subm", "skel", "part", "rib")]
        out.writerow(row)


if __name__ == "__main__":
    main()
