#!/usr/bin/env python3
"""Modern (MD21 v264-274, Retail/Forever) spell-effect M2 -> native MD20 v256 with particle and ribbon emitters.

Builds on the skinned converter's IR (tools/skinned-converter/m2ir.py, read-only reuse): mesh, bones, sequences,
materials, texture transforms and attachments convert there; emitters/ribbons were dropped there and are added here.

Particle translation (modern record 0x1EC -> native record 0x1F8; native layout pinned in
`_old-version-inactive/crates/benilla-formats/src/particles.rs`, modern layout pinned against Forever/Retail files):
  * 20 B per-sequence M2Tracks are flattened onto the native single timeline (m2ir.TrackWriter) as 28 B tracks;
  * FBlock colour/alpha/scale (arbitrary key count) are resampled to the native 3-key ramp (mid = the middle key's time when
    there are exactly three keys, else 0.5);
  * flags: low 16 bits kept, 0x1 (unlit) and 0x8 (ride) forced as shipped native effects always carry them; head/tail comes
    from modern flag bits 0x20000/0x40000 ((flags>>17)&3 = headOrTail + 1, verified on 293 Forever/native pairs);
  * emitter scale capped to max(1.5, 2 x native model's largest emitter scale) (report 'scale_cap');
  * dropped (reported): multi-texture (only texture 0), scaleVary, lifespanVary/rateVary, geometry/recursion model names,
    colour index, high flag bits, y scale (x used), spin variation.
"""
import json
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "skinned-converter"))
sys.path.insert(0, str(HERE))
import m2ir  # noqa: E402
import skinned_convert as sc  # noqa: E402
import fx_particles as fp  # noqa: E402

KEEP_FLAGS = 0xFFFF & ~0x0800  # 0x800 unused in shipped files
EXTRA_FLAGS = 0x1 | 0x8
N_STRIDE = 0x1F8
R_STRIDE = 0xDC


def interp3(keys, t, default):
    """Linear sample of [(t01, value tuple/float)] at t in 0..1."""
    if not keys:
        return default
    if t <= keys[0][0]:
        return keys[0][1]
    for (t0, v0), (t1, v1) in zip(keys, keys[1:]):
        if t <= t1:
            u = 0.0 if t1 == t0 else (t - t0) / (t1 - t0)
            if isinstance(v0, tuple):
                return tuple(a + (b - a) * u for a, b in zip(v0, v1))
            return v0 + (v1 - v0) * u
    return keys[-1][1]


def norm(keys):
    return [(min(max(t / 32767.0, 0.0), 1.0), v) for t, v in keys]


def pick_mid(*key_lists):
    for kl in key_lists:
        if len(kl) == 3:
            return kl[1][0] / 32767.0
    return 0.5


def with_default(tr, forced=None):
    """Sequences with no keys must still get a range into this track's own keys: hold the first value anywhere."""
    if tr is None or tr.gseq >= 0:
        return tr
    if forced is not None:
        tr.default = forced
        return tr
    for ks in tr.keys:
        if ks:
            tr.default = ks[0][1]
            break
    return tr


def native_color(rgb, a):
    r, g, b = (max(0, min(255, int(round(c)))) for c in rgb)
    return (max(0, min(255, int(round(a * 255)))) << 24) | (r << 16) | (g << 8) | b


def cells(keys, mid):
    """Modern head/tail cell FBlock -> native two-segment ramp pairs (A begin,end ; B begin,end)."""
    if not keys:
        return (0, 0, 0, 0)
    ks = norm(keys)
    c0 = int(round(interp3(ks, 0.0, 0)))
    c1 = int(round(interp3(ks, mid, 0)))
    c2 = int(round(interp3(ks, 1.0, 0)))
    b0 = c1 if c2 == c1 else (c1 + 1 if c2 > c1 else c1 - 1)
    return (c0, c1, b0, c2)


def convert_particles(reader, tw, report):
    core = reader.core
    n, o = fp.u32(core, 0x128), fp.u32(core, 0x12C)
    texs = fp.read_modern(core, None, tracks=False)  # decoded fields (tracks via reader below)
    out = bytearray()
    lost = report.setdefault("particle_loss", [])
    for i, d in enumerate(texs):
        e = o + i * fp.M_STRIDE
        r = bytearray(N_STRIDE)
        flags = d["flags"]
        struct.pack_into("<II", r, 0, d["id"], (flags & KEEP_FLAGS) | EXTRA_FLAGS)
        struct.pack_into("<3f", r, 8, *d["pos"])
        struct.pack_into("<HH", r, 0x14, d["bone"], d["tex"])
        blend = d["blend"] if d["blend"] <= 6 else 4
        ht = max(0, ((flags >> 17) & 3) - 1)
        struct.pack_into("<HHBBH", r, 0x28, blend, d["emit"], ht, 0, 0)
        struct.pack_into("<HH", r, 0x30, max(1, d["rows"]), max(1, d["cols"]))
        names = [("speed", "f32"), ("speedVar", "f32"), ("vert", "f32"), ("horiz", "f32"), ("gravity", "f32"), ("lifespan", "f32")]
        base_of = {"speed": 0x34, "speedVar": 0x48, "vert": 0x5C, "horiz": 0x70, "gravity": 0x84, "lifespan": 0x98,
                   "rate": 0xB0, "areaL": 0xC8, "areaW": 0xDC, "zSource": 0xF0}
        dst_of = {"speed": 0x34, "speedVar": 0x50, "vert": 0x6C, "horiz": 0x88, "gravity": 0xA4, "lifespan": 0xC0,
                  "rate": 0xDC, "areaL": 0xF8, "areaW": 0x114, "zSource": 0x130}
        for nm in ("speed", "speedVar", "vert", "horiz", "gravity", "lifespan", "rate", "areaL", "areaW", "zSource"):
            if nm == "zSource":
                continue      # modern files store 255 here (a different meaning); native default 0
            tr = with_default(reader.track(e + base_of[nm], "f32"))
            r[dst_of[nm]:dst_of[nm] + 28] = tw.track_bytes(tr, 4)
        en = with_default(reader.track(e + 0x1C8, "u8"), b"")
        r[0x1DC:0x1DC + 28] = tw.track_bytes(en, 1)
        # over-life ramps
        col, alp, scl = norm(d["color"]), norm([(t, v / 32767.0) for t, v in d["alpha"]]), norm([(t, v) for t, v in d["scale"]])
        mid = pick_mid(d["color"], d["alpha"], d["scale"])
        mid = min(max(mid, 0.02), 0.98)
        cap = report.get("scale_cap")
        cs, as_, ss = [], [], []
        for t in (0.0, mid, 1.0):
            cs.append(interp3(col, t, (255.0, 255.0, 255.0)))
            as_.append(interp3(alp, t, 1.0))
            ss.append(interp3(scl, t, (1.0, 1.0)))
        struct.pack_into("<f", r, 0x14C, mid)
        for k in range(3):
            struct.pack_into("<I", r, 0x150 + 4 * k, native_color(cs[k], max(0.0, as_[k])))
            sk = ss[k][0]
            if cap and sk > cap:
                lost.append(f"emitter {i}: scale {sk:.2f} capped to {cap:.2f}") if k == 0 or ss[k - 1][0] <= cap else None
                sk = cap
            struct.pack_into("<f", r, 0x15C + 4 * k, sk)
        ha = cells(d["head"], mid)
        ta = cells(d["tail"], mid)
        struct.pack_into("<10H", r, 0x168, ha[0], ha[1], 1, ha[2], ha[3], 1, ta[0], ta[1], ta[2], ta[3])
        struct.pack_into("<f", r, 0x17C, d["tail_time"])
        struct.pack_into("<4f", r, 0x180, *d["twinkle"])
        struct.pack_into("<ff", r, 0x190, d["burst_mult"], d["drag"])
        struct.pack_into("<f", r, 0x198, d["spin4"][2])
        struct.pack_into("<6f", r, 0x19C, *d["tumble"])
        struct.pack_into("<4f", r, 0x1B4, *d["wind"])
        struct.pack_into("<4f", r, 0x1C4, *d["follow"])
        # spline points
        sn, so = d["spline_n"], d["spline_o"]
        if sn and so + sn * 12 <= len(core):
            pts = core[so:so + sn * 12]
            struct.pack_into("<II", r, 0x1D4, sn, tw.blob.put(pts))
        out += r
        if flags & ~0xFFFF & ~0x70000000 & ~0x00E60000:
            pass
        if d["geom"] or d["recur"]:
            lost.append(f"emitter {i}: geometry/recursion model {d['geom']!r}/{d['recur']!r} dropped")
        if (flags & 0x10000000) and len(set(d["tex3"])) > 1:
            lost.append(f"emitter {i}: multi-texture {d['tex3']} -> texture {d['tex']} only")
        if any(abs(s[0] - s[1]) > 1e-3 for s in [v for _, v in d["scale"]]):
            lost.append(f"emitter {i}: non-square scale (x used)")
    return n, bytes(out)


def convert_ribbons(reader, tw, report):
    core = reader.core
    n, o = fp.u32(core, 0x120), fp.u32(core, 0x124)
    out = bytearray()
    for i in range(n):
        e = o + i * 0xB0
        r = bytearray(R_STRIDE)
        rid = struct.unpack_from("<i", core, e)[0]
        bone = fp.u32(core, e + 4)
        struct.pack_into("<iH", r, 0, rid, 0)
        struct.pack_into("<H", r, 0x04, bone)
        struct.pack_into("<3f", r, 0x08, *struct.unpack_from("<3f", core, e + 8))
        for src_o, dst_o in ((0x14, 0x14), (0x1C, 0x1C)):
            c, p = fp.u32(core, e + src_o), fp.u32(core, e + src_o + 4)
            vals = core[p:p + c * 2] if c else b""
            struct.pack_into("<II", r, dst_o, c, tw.blob.put(vals) if c else 0)
        for src_o, dst_o, codec, stride in ((0x24, 0x24, "vec3", 12), (0x38, 0x40, "fix16", 2), (0x4C, 0x5C, "f32", 4), (0x60, 0x78, "f32", 4)):
            tr = with_default(reader.track(e + src_o, codec))
            r[dst_o:dst_o + 28] = tw.track_bytes(tr, stride)
        struct.pack_into("<3f", r, 0x94, *struct.unpack_from("<3f", core, e + 0x74))
        struct.pack_into("<HH", r, 0xA0, *struct.unpack_from("<HH", core, e + 0x80))
        r[0xA4:0xA4 + 28] = tw.track_bytes(with_default(_u16_track(reader, e + 0x84)), 2)
        r[0xC0:0xC0 + 28] = tw.track_bytes(with_default(reader.track(e + 0x98, "u8"), b""), 1)
        out += r
    return n, bytes(out)


def _u16_track(reader, base):
    # the shared codec table has no u16; the values are constant (first key) in shipped files
    core = reader.core
    interp, gseq = struct.unpack_from("<Hh", core, base)
    nts, ots = fp.u32(core, base + 4), fp.u32(core, base + 8)
    keys = []
    for s in range(nts):
        c, oo = struct.unpack_from("<II", core, ots + s * 8)
        vc, vo = struct.unpack_from("<II", core, fp.u32(core, base + 16) + s * 8)
        if not c:
            keys.append([])
            continue
        ts = struct.unpack_from("<%dI" % c, core, oo)
        vs = [struct.pack("<H", struct.unpack_from("<H", core, vo + k * 2)[0]) for k in range(vc)]
        keys.append(list(zip(ts, vs)))
    return m2ir.Track(interp, gseq, keys, None, 2)


def plan_out_seqs(m, keep, aliases):
    out = []
    for oi, si in enumerate(keep):
        s = dict(m.sequences[si])
        s["alias_of"] = aliases[oi]
        out.append(s)
    return out


def convert(model_bytes, skin_bytes, anims, name, template, fallbacks, texture_path, report):
    """Returns native MD20 bytes. texture_path(slot, texture dict) -> native path string for a type-0 texture."""
    reader = m2ir.ModernReader(model_bytes, skin_bytes, anims)
    # Retail re-designed a few effects at several times the classic size (white-out blobs in the lab); the game's spell
    # camera/world scale is the classic one, so emitter scales are capped to max(1.5, 2 x the native model's largest).
    nat = fp.read_native(template) if template else []
    report["scale_cap"] = max(1.5, 2.0 * max((max(e["scales"]) for e in nat), default=0.0))
    m = reader.read(name)
    keep, aliases, seq_dropped = sc.select_sequences(m)
    sc.rewrite_batches(m, "drop")
    tmpl = m2ir.read_v256(template, "template") if template else None
    sc.build_lookups(m, keep, aliases, tmpl, fallbacks)
    for slot, t in enumerate(m.textures):
        if t["type"] == 0 and (t["fdid"] or t["path"]):
            t["path"] = texture_path(slot, t)
        t["flags"] &= 0x3
    m.replaceable_lookup = []
    md20 = m2ir.write_v256(m, keep, aliases, name)
    blob = m2ir.Blob()
    blob.b = bytearray(md20)
    bands = m2ir.plan_bands(plan_out_seqs(m, keep, aliases))
    tw = m2ir.TrackWriter(blob, keep, bands, aliases)
    npart, pbytes = convert_particles(reader, tw, report) if fp.u32(reader.core, 0x128) else (0, b"")
    nrib, rbytes = convert_ribbons(reader, tw, report) if fp.u32(reader.core, 0x120) else (0, b"")
    if npart:
        struct.pack_into("<II", blob.b, m2ir.V["particles"], npart, blob.put(pbytes))
    if nrib:
        struct.pack_into("<II", blob.b, m2ir.V["ribbons"], nrib, blob.put(rbytes))
    report.update(sequences_kept=len(keep), sequences_dropped=seq_dropped, particles=npart, ribbons=nrib,
                  vertices=len(m.vertices) // 48, bones=len(m.bones), batches=len(m.batches), dropped=m.dropped)
    return bytes(blob.b)
