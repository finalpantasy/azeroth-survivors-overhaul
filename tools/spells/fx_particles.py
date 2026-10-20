"""Particle/ribbon emitter records: native MD20 v256 (0x1F8 B, 28 B tracks, 3-key ramps) and modern MD21 v272/274
(0x1EC B, 20 B per-sequence tracks, FBlocks).  Native layout from the engine's own loader notes
(`_old-version-inactive/crates/benilla-formats/src/particles.rs`); modern layout pinned against Forever/Retail files.

Reading only here; the writer is fx_convert.py.
"""
import struct

N_STRIDE = 0x1F8
M_STRIDE = 0x1EC


def u16(b, o):
    return struct.unpack_from("<H", b, o)[0]


def u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def f32(b, o):
    return struct.unpack_from("<f", b, o)[0]


def _arr(b, o):
    return struct.unpack_from("<II", b, o)


def _cstr(b, o):
    n, p = _arr(b, o)
    if n < 2 or p + n > len(b):
        return ""
    return b[p:p + n].split(b"\0")[0].decode("ascii", "replace")


def native_raw_track(b, t, vfmt, vsz):
    interp, gseq = struct.unpack_from("<Hh", b, t)
    rn, ro = _arr(b, t + 4)
    tn, to = _arr(b, t + 12)
    vn, vo = _arr(b, t + 20)
    ranges = [struct.unpack_from("<II", b, ro + i * 8) for i in range(rn)] if rn and ro + rn * 8 <= len(b) else []
    n = min(tn, vn)
    keys = [(u32(b, to + i * 4), struct.unpack_from(vfmt, b, vo + i * vsz)[0]) for i in range(n)] if n else []
    return {"interp": interp, "gseq": gseq, "ranges": ranges, "keys": keys}


def read_native(b):
    """Native emitters as dicts (raw field values)."""
    if b[:4] != b"MD20":
        return []
    n, o = _arr(b, 0x13C)
    tn, to = _arr(b, 0x5C)
    texs = []
    for i in range(tn):
        t, fl, ln, no = struct.unpack_from("<IIII", b, to + i * 16)
        texs.append(b[no:no + ln].split(b"\0")[0].decode("ascii", "replace") if ln else "")
    out = []
    for i in range(n):
        e = o + i * N_STRIDE
        d = {"id": u32(b, e), "flags": u32(b, e + 4), "pos": struct.unpack_from("<3f", b, e + 8), "bone": u16(b, e + 0x14),
             "tex": u16(b, e + 0x16), "blend": u16(b, e + 0x28), "emit": u16(b, e + 0x2A), "head_tail": b[e + 0x2C],
             "b2d": b[e + 0x2D], "w2e": u16(b, e + 0x2E), "rows": u16(b, e + 0x30), "cols": u16(b, e + 0x32)}
        d["texture"] = texs[d["tex"]] if d["tex"] < len(texs) else ""
        names = "speed speedVar vert horiz gravity lifespan rate areaL areaW zSource".split()
        for k, nm in enumerate(names):
            tr = native_raw_track(b, e + 0x34 + k * 0x1C, "<f", 4)
            d[nm] = tr
        d["enabled"] = native_raw_track(b, e + 0x1DC, "<B", 1)
        d["mid"] = f32(b, e + 0x14C)
        d["colors"] = [u32(b, e + 0x150 + 4 * k) for k in range(3)]
        d["scales"] = [f32(b, e + 0x15C + 4 * k) for k in range(3)]
        d["cells"] = struct.unpack_from("<10H", b, e + 0x168)
        d["tail_time"] = f32(b, e + 0x17C)
        d["twinkle"] = struct.unpack_from("<4f", b, e + 0x180)
        d["burst_mult"] = f32(b, e + 0x190)
        d["drag"] = f32(b, e + 0x194)
        d["spin"] = f32(b, e + 0x198)
        d["tumble"] = struct.unpack_from("<6f", b, e + 0x19C)
        d["wind"] = struct.unpack_from("<4f", b, e + 0x1B4)
        d["follow"] = struct.unpack_from("<4f", b, e + 0x1C4)
        d["spline_n"], d["spline_o"] = _arr(b, e + 0x1D4)
        d["raw"] = b[e:e + N_STRIDE]
        out.append(d)
    return out


def modern_track(core, t, vfmt, vsz, seq_bufs=None):
    """Modern 20 B M2Track -> {'interp','gseq','keys':[[(t,v)...] per sequence]} (values float/tuple)."""
    interp, gseq = struct.unpack_from("<Hh", core, t)
    nts, ots = _arr(core, t + 4)
    nvs, ovs = _arr(core, t + 12)
    keys = []
    for s in range(nts):
        c, oo = _arr(core, ots + s * 8)
        vc, vo = _arr(core, ovs + s * 8)
        buf = core
        if gseq < 0 and seq_bufs is not None:
            buf = seq_bufs[s] if s < len(seq_bufs) else None
        if not c or buf is None:
            keys.append([])
            continue
        ts = struct.unpack_from("<%dI" % c, buf, oo)
        vs = [struct.unpack_from(vfmt, buf, vo + k * vsz) for k in range(vc)]
        vs = [v[0] if len(v) == 1 else v for v in vs]
        keys.append(list(zip(ts, vs)))
    return {"interp": interp, "gseq": gseq, "keys": keys}


def fblock(core, o, vfmt, vsz):
    c, ot = _arr(core, o)
    vc, ov = _arr(core, o + 8)
    ts = list(struct.unpack_from("<%dH" % c, core, ot)) if c else []
    vs = [struct.unpack_from(vfmt, core, ov + i * vsz) for i in range(vc)]
    vs = [v[0] if len(v) == 1 else v for v in vs]
    return list(zip(ts, vs))


def read_modern(core, seq_bufs=None, count_off=0x128, tracks=True):
    """Modern MD20 core -> emitter dicts (same keys as read_native where there is a counterpart)."""
    n, o = _arr(core, count_off)
    tn, to = _arr(core, 0x50)
    texs = []
    for i in range(tn):
        t, fl, ln, no = struct.unpack_from("<IIII", core, to + i * 16)
        texs.append(core[no:no + ln].split(b"\0")[0].decode("ascii", "replace") if ln else "")
    out = []
    for i in range(n):
        e = o + i * M_STRIDE
        d = {"id": u32(core, e), "flags": u32(core, e + 4), "pos": struct.unpack_from("<3f", core, e + 8), "bone": u16(core, e + 0x14),
             "tex_raw": u16(core, e + 0x16), "blend": core[e + 0x28], "emit": core[e + 0x29], "color_index": u16(core, e + 0x2A),
             "mt0": tuple(core[e + 0x2C:e + 0x2E]), "mt1": tuple(core[e + 0x2E:e + 0x30]), "rows": u16(core, e + 0x30), "cols": u16(core, e + 0x32),
             "geom": _cstr(core, e + 0x18), "recur": _cstr(core, e + 0x20)}
        flags = d["flags"]
        d["tex"] = (d["tex_raw"] & 0x1F) if (flags & 0x10000000) else d["tex_raw"]
        d["tex3"] = ((d["tex_raw"] & 0x1F), (d["tex_raw"] >> 5) & 0x1F, (d["tex_raw"] >> 10) & 0x1F) if (flags & 0x10000000) else (d["tex_raw"],)
        d["texture"] = texs[d["tex"]] if d["tex"] < len(texs) else ""
        # tracks: speed speedVar vert horiz gravity lifespan | lifespanVary | rate | rateVary | areaL areaW zSource
        pos = e + 0x34
        mt = (lambda *a: modern_track(*a)) if tracks else (lambda *a: None)
        for nm in ("speed", "speedVar", "vert", "horiz", "gravity", "lifespan"):
            d[nm] = mt(core, pos, "<f", 4, seq_bufs)
            pos += 20
        d["lifespanVary"] = f32(core, pos)
        pos += 4
        d["rate"] = mt(core, pos, "<f", 4, seq_bufs)
        pos += 20
        d["rateVary"] = f32(core, pos)
        pos += 4
        for nm in ("areaL", "areaW", "zSource"):
            d[nm] = mt(core, pos, "<f", 4, seq_bufs)
            pos += 20
        assert pos == e + 0x104, hex(pos - e)
        d["color"] = fblock(core, e + 0x104, "<3f", 12)
        d["alpha"] = fblock(core, e + 0x114, "<h", 2)
        d["scale"] = fblock(core, e + 0x124, "<2f", 8)
        d["scaleVary"] = struct.unpack_from("<2f", core, e + 0x134)
        d["head"] = fblock(core, e + 0x13C, "<H", 2)
        d["tail"] = fblock(core, e + 0x14C, "<H", 2)
        d["tail_time"] = f32(core, e + 0x15C)
        d["twinkle"] = struct.unpack_from("<4f", core, e + 0x160)       # speed, percent, min, max
        d["burst_mult"] = f32(core, e + 0x170)
        d["drag"] = f32(core, e + 0x174)
        d["spin4"] = struct.unpack_from("<4f", core, e + 0x178)        # baseSpin, baseSpinVary, spin, spinVary
        d["tumble"] = struct.unpack_from("<6f", core, e + 0x188)
        d["wind"] = struct.unpack_from("<4f", core, e + 0x1A0)         # vec3 + windTime
        d["follow"] = struct.unpack_from("<4f", core, e + 0x1B0)
        d["spline_n"], d["spline_o"] = _arr(core, e + 0x1C0)
        d["enabled"] = mt(core, e + 0x1C8, "<B", 1, seq_bufs)
        d["tail_bytes"] = core[e + 0x1DC:e + 0x1EC]
        out.append(d)
    return out
