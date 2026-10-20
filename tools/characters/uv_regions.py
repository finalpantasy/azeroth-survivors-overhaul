#!/usr/bin/env python3
"""Per-submesh UV extents of a character M2: which geosets sample which atlas region. Usage: uv_regions.py <m2>"""
import struct, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from m2lib import M2
m = M2(Path(sys.argv[1]).read_bytes())
nv, ov = m.arr(0x44)
v = m.view()
idx = struct.unpack_from(f"<{v['n_indices']}H", m.data, v["ofs_indices"])
tri = struct.unpack_from(f"<{v['n_tri_indices']}H", m.data, v["ofs_tri"])
for i, s in enumerate(m.submeshes()):
    us, vs = [], []
    for t in tri[s["tstart"]:s["tstart"] + s["tcount"]]:
        u, w = struct.unpack_from("<2f", m.data, ov + idx[t] * 48 + 32)
        us.append(u); vs.append(w)
    if not us: continue
    print(f"sub {i:3d} geoset {s['id']:4d} tris {s['tcount']//3:5d} u {min(us):.2f}-{max(us):.2f} v {min(vs):.2f}-{max(vs):.2f}")
