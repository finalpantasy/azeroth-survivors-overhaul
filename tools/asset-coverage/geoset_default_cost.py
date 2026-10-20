#!/usr/bin/env python3
"""Per HD character skin: submesh (geoset) table and the vertex/triangle cost of a default-visible geoset selection.

Default selection = group 0 (body) plus the lowest non-zero variant of every other group (id = group*100 + variant).
Reports whether the selection is a contiguous vertex prefix (the trick the Human male trim relies on).
Usage: geoset_default_cost.py <skin-dir> <ids-file 'fdid;name' or plain> <hd-parse.json> <profile-listing>
"""
import collections
import json
import struct
import sys
from pathlib import Path


def submeshes(b):
    assert b[:4] == b"SKIN"
    nsub, osub = struct.unpack_from("<II", b, 28)
    nbat, obat = struct.unpack_from("<II", b, 36)
    size = (obat - osub) // nsub if nsub and obat > osub else 48
    out = []
    for i in range(nsub):
        o = osub + i * size
        sid, level, vstart, vcount, istart, icount = struct.unpack_from("<6H", b, o)
        out.append(dict(id=sid, level=level, vstart=vstart | (level << 16), vcount=vcount, istart=istart, icount=icount, size=size))
    return out


def main():
    skin_dir, parse_json, names_file = map(Path, sys.argv[1:4])
    names = {l.split(";")[0]: l.strip().split(";")[1] for l in names_file.read_text().splitlines() if ";" in l}
    d = json.loads(parse_json.read_text())["results"]
    rows = []
    for fid, r in d.items():
        n = names.get(fid, "")
        m = r.get("m2")
        if not m or not n.startswith("character/") or "transform" in n:
            continue
        f = skin_dir / f"{m['sfid'][0]}.bin"
        if not f.exists():
            continue
        subs = submeshes(f.read_bytes())
        groups = collections.defaultdict(list)
        for s in subs:
            groups[s["id"] // 100].append(s)
        sel = []
        for g, lst in groups.items():
            lst.sort(key=lambda s: s["id"])
            if g == 0:
                sel += [s for s in lst if s["id"] % 100 == 0] or lst[:1]
            else:
                nz = [s for s in lst if s["id"] % 100 != 0]
                sel += nz[:1] if nz else lst[:1]
        verts = sum(s["vcount"] for s in sel)
        tris = sum(s["icount"] for s in sel) // 3
        lo = min(s["vstart"] for s in sel)
        hi = max(s["vstart"] + s["vcount"] for s in sel)
        rows.append(dict(path=n, model_verts=m["verts"], bones=m["bones"], submeshes=len(subs), groups=len(groups), sel_submeshes=len(sel),
                         sel_verts=verts, sel_tris=tris, prefix_span=hi, contiguous_prefix=(lo == 0), base_group0_verts=sum(s["vcount"] for s in groups[0])))
    print(json.dumps(rows, indent=1))


if __name__ == "__main__":
    main()
