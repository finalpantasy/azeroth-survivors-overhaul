#!/usr/bin/env python3
"""Dump batches of a character M2: geoset id, texture type/name, tris. Usage: dump_batches.py <model.m2>"""
import struct
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from m2lib import M2, HEADER_OFFSETS

m = M2(Path(sys.argv[1]).read_bytes())
tex = m.textures()
n, o = m.arr(0x94)
lookup = struct.unpack_from(f"<{n}H", m.data, o)
subs = m.submeshes()
v = m.view()
print("textures:", [(i, t[0], t[2]) for i, t in enumerate(tex)])
for i in range(v["n_texunits"]):
    flags, shading, sub, sub2, color, rf, tu, mode, tex_i, tu2, tr, ta = struct.unpack_from("<HHHHhHHHHHHH", m.data, v["ofs_texunits"] + i * 24)

    s = subs[sub]
    li = lookup[tex_i] if tex_i < len(lookup) else None
    t = tex[li] if li is not None and li < len(tex) else None
    print(f"batch {i:3d} sub {sub:3d} geoset {s['id']:4d} tris {s['tcount']//3:5d} tex#{li} idx{tex_i} type {t[0] if t else '?'} {t[2] if t else ''} rf {rf} shader {shading}")
