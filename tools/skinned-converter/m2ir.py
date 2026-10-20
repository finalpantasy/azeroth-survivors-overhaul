#!/usr/bin/env python3
"""Skinned M2 intermediate representation: modern MD21 (v264-274) reader, native MD20 v256 writer and reader.

The native engine (wenilla/benilla, WoW 1.12.1 client reimplementation) reads classic MD20 v256 only. Layout facts
pinned for the writer (evidence in analysis/skinned-converter/NATIVE-FORMAT.md):
  * 108-byte bones, 28-byte tracks (interp u16, gseq i16, ranges, timestamps, values) on ONE timeline; each sequence
    owns a band [start, end] and a bone track's ranges[i] = (first, last) key index, inclusive. Aliased sequences share
    their target's band. A track keyed in no sequence has 0 ranges; a constant track has 0 ranges and one key at t=0.
  * Rotations are float quaternions (x, y, z, w), 16 bytes (modern files use 8-byte compressed int16 quaternions).
  * Event tracks use half-open ranges (first, end).
  * 68-byte sequences (start/end ms), 48-byte vertices (same as modern), embedded 44-byte views, 32-byte sections.
  * PlayableAnimationLookup (u32: low16 resolved anim id, high16 flags) and AnimationLookup are both required.
Everything here is pure Python; numpy is used only by the preview renderer.
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field

# --------------------------------------------------------------------------------------------- helpers


class ConvertError(ValueError):
    pass


def require(cond, msg):
    if not cond:
        raise ConvertError(msg)


def arr(b, o):
    return struct.unpack_from("<II", b, o)


def split_chunks(payload: bytes):
    chunks, cur = {}, 0
    while cur + 8 <= len(payload):
        tag = payload[cur:cur + 4].decode("ascii", "replace")
        size = struct.unpack_from("<I", payload, cur + 4)[0]
        require(cur + 8 + size <= len(payload), f"{tag} chunk overruns the file")
        chunks.setdefault(tag, payload[cur + 8:cur + 8 + size])
        cur += 8 + size
    return chunks


def comp_quat(h):
    """Modern M2CompQuat int16 -> float (wowdev: v<0 ? v+32768 : v-32767, /32767)."""
    return tuple(((v + 32768) if v < 0 else (v - 32767)) / 32767.0 for v in h)


def quat_norm(q):
    n = math.sqrt(sum(x * x for x in q)) or 1.0
    return tuple(x / n for x in q)


# value codecs: name -> (modern stride, native stride, decode(modern bytes)->native bytes)
def _rot(b):
    return struct.pack("<4f", *quat_norm(comp_quat(struct.unpack("<4h", b))))


CODECS = {
    "vec3": (12, 12, lambda b: b),
    "quat16": (8, 16, _rot),      # bone rotation: compressed int16 -> float
    "quatf": (16, 16, lambda b: b),  # texture transform rotation (float in both)
    "fix16": (2, 2, lambda b: b),
    "u8": (1, 1, lambda b: b),
    "f32": (4, 4, lambda b: b),
}
DEFAULTS = {
    "translation": struct.pack("<3f", 0, 0, 0),
    "rotation": struct.pack("<4f", 0, 0, 0, 1),
    "scale": struct.pack("<3f", 1, 1, 1),
    "color": struct.pack("<3f", 1, 1, 1),
    "alpha": struct.pack("<h", 0x7FFF),
    "weight": struct.pack("<h", 0x7FFF),
    "tt_translation": struct.pack("<3f", 0, 0, 0),
    "tt_rotation": struct.pack("<4f", 0, 0, 0, 1),
    "tt_scale": struct.pack("<3f", 1, 1, 1),
    "attach": struct.pack("<B", 1),
}


@dataclass
class Track:
    interp: int
    gseq: int
    keys: list  # per source sequence: list of (time_ms, native_value_bytes); for gseq tracks a single list
    default: bytes | None = None
    stride: int = 0

    def animated(self):
        return any(self.keys_of(i) for i in range(len(self.keys)))

    def keys_of(self, i):
        return self.keys[i] if i < len(self.keys) and self.keys[i] else []


@dataclass
class Model:
    name: str
    flags: int
    global_sequences: list
    sequences: list          # dicts
    bones: list              # dicts with Track objects
    vertices: bytes          # 48-byte records
    lookup: list             # skin vertex -> global vertex
    tris: list               # skin triangle indices (into lookup)
    props: bytes             # 4 bytes per skin vertex
    sections: list           # dicts
    batches: list            # 12-tuple of u16 (flags as u16 at [0])
    textures: list           # dicts: type, flags, path
    materials: list          # (flags, blend)
    bone_lookup: list
    key_bone_lookup: list
    texture_lookup: list
    texture_unit_lookup: list
    transparency_lookup: list
    tex_transform_lookup: list
    replaceable_lookup: list
    colors: list = field(default_factory=list)          # (color Track, alpha Track)
    transparency: list = field(default_factory=list)    # Track
    tex_transforms: list = field(default_factory=list)  # (t, r, s) Tracks
    attachments: list = field(default_factory=list)     # dicts id, bone, pos, track
    attachment_lookup: list = field(default_factory=list)
    events: list = field(default_factory=list)          # dicts ident, data, bone, pos, track(times only)
    cameras: list = field(default_factory=list)         # raw dicts (converted lazily)
    camera_lookup: list = field(default_factory=list)
    bounds: tuple = ((0, 0, 0), (0, 0, 0), 0.0, (0, 0, 0), (0, 0, 0), 0.0)
    coll_tris: bytes = b""
    coll_verts: bytes = b""
    coll_normals: bytes = b""
    playable_lookup: list = field(default_factory=list)  # (resolved id, flags)
    animation_lookup: list = field(default_factory=list)
    dropped: list = field(default_factory=list)


# ------------------------------------------------------------------------------------------ modern reader

MOD = dict(name=0x08, flags=0x10, gseq=0x14, seqs=0x1C, seq_lookup=0x24, bones=0x2C, key_bone=0x34, verts=0x3C,
           nskins=0x44, colors=0x48, textures=0x50, weights=0x58, tex_trans=0x60, repl=0x68, mats=0x70,
           bone_lookup=0x78, tex_lookup=0x80, tex_unit=0x88, trans_lookup=0x90, tt_lookup=0x98, bounds=0xA0,
           coll_tris=0xD8, coll_verts=0xE0, coll_norm=0xE8, att=0xF0, att_lookup=0xF8, events=0x100, lights=0x108,
           cams=0x110, cam_lookup=0x118, ribbons=0x120, particles=0x128)


def _u16s(b, n, o):
    return list(struct.unpack_from(f"<{n}H", b, o)) if n else []


def _h16s(b, n, o):
    return list(struct.unpack_from(f"<{n}h", b, o)) if n else []


class ModernReader:
    """Reads a modern MD21 file; external .anim data is supplied as {(anim_id, variation): bytes}."""

    def __init__(self, model_bytes: bytes, skin_bytes: bytes, anim_files: dict | None = None):
        self.chunks = split_chunks(model_bytes)
        require("MD21" in self.chunks, "not an MD21 chunked model")
        self.core = core = self.chunks["MD21"]
        require(core[:4] == b"MD20", "MD21 core is not MD20")
        self.version = struct.unpack_from("<I", core, 4)[0]
        require(264 <= self.version <= 274, f"unsupported MD20 version {self.version}")
        require("SKID" not in self.chunks, "model uses an external .skel (SKID): not supported (use a model with embedded bones)")
        self.skin = skin_bytes
        require(skin_bytes[:4] == b"SKIN", "skin file is not SKIN")
        self.anim_raw = anim_files or {}
        self.afid = {}
        if "AFID" in self.chunks:
            a = self.chunks["AFID"]
            for i in range(len(a) // 8):
                aid, sub, fd = struct.unpack_from("<HHI", a, i * 8)
                self.afid[(aid, sub)] = fd
        n, o = arr(core, MOD["seqs"])
        self.seq_raw = [struct.unpack_from("<HHIfIhHIIHH3f3ffhH", core, o + i * 64) for i in range(n)]
        # buffer per sequence (None = data unavailable)
        self.seq_buf = []
        for s in self.seq_raw:
            sid, var, flags = s[0], s[1], s[4]
            if flags & 0x40:
                self.seq_buf.append(core)          # alias: data lives with its target
            elif flags & 0x20:
                self.seq_buf.append(core)
            else:
                raw = self.anim_raw.get((sid, var))
                self.seq_buf.append(self._anim_data(raw) if raw is not None else None)

    @staticmethod
    def _anim_data(raw):
        if raw[:4] in (b"AFM2", b"AFSA", b"AFSB"):
            ch = split_chunks(raw)
            require("AFM2" in ch, "chunked .anim without AFM2")
            return ch["AFM2"]
        return raw

    def track(self, base, codec, default=None, events=False):
        core = self.core
        interp, gseq = struct.unpack_from("<Hh", core, base)
        nts, ots = arr(core, base + 4)
        if events:
            nvs, ovs = 0, 0
        else:
            nvs, ovs = arr(core, base + 12)
        mstride, nstride, dec = CODECS[codec] if codec else (0, 0, None)
        keys = []
        for i in range(nts):
            c, o = arr(core, ots + i * 8)
            if gseq >= 0:
                buf = core
            else:
                buf = self.seq_buf[i] if i < len(self.seq_buf) else core
            if c == 0 or buf is None:
                keys.append([])
                continue
            times = struct.unpack_from(f"<{c}I", buf, o)
            if events:
                keys.append([(t, b"") for t in times])
                continue
            vc, vo = arr(core, ovs + i * 8)
            require(vc == c, f"track at 0x{base:x}: {c} timestamps vs {vc} values")
            vals = [dec(buf[vo + k * mstride: vo + (k + 1) * mstride]) for k in range(c)]
            keys.append(list(zip(times, vals)))
        return Track(interp, gseq, keys, default, nstride)

    def read(self, name: str) -> Model:
        core, A = self.core, (lambda k: arr(self.core, MOD[k]))
        flags = struct.unpack_from("<I", core, MOD["flags"])[0]
        dropped = []
        for k in ("lights", "ribbons", "particles"):
            if A(k)[0]:
                dropped.append(f"{A(k)[0]} {k}")
        n, o = A("gseq")
        gseqs = list(struct.unpack_from(f"<{n}I", core, o)) if n else []
        seqs = []
        for i, s in enumerate(self.seq_raw):
            (sid, var, dur, spd, sfl, freq, _pad, rmin, rmax, bin_, bout, x0, y0, z0, x1, y1, z1, rad, vnext, anext) = s
            seqs.append(dict(id=sid, variation=var, duration=dur, movespeed=spd, flags=sfl, frequency=freq,
                             replay=(rmin, rmax), blend=bin_, bmin=(x0, y0, z0), bmax=(x1, y1, z1), radius=rad,
                             variation_next=vnext, alias_next=anext, available=self.seq_buf[i] is not None))
        nb, ob = A("bones")
        bones = []
        for i in range(nb):
            base = ob + i * 88
            kb, bfl, par, sub, crc = struct.unpack_from("<iIhHI", core, base)
            pivot = struct.unpack_from("<3f", core, base + 76)
            bones.append(dict(key=kb, flags=bfl, parent=par, submesh=sub, crc=crc, pivot=pivot,
                              translation=self.track(base + 16, "vec3", DEFAULTS["translation"]),
                              rotation=self.track(base + 36, "quat16", DEFAULTS["rotation"]),
                              scale=self.track(base + 56, "vec3", DEFAULTS["scale"])))
        nv, ov = A("verts")
        verts = core[ov:ov + nv * 48]
        require(len(verts) == nv * 48, "vertex array truncated")
        # skin
        sk = self.skin
        (nl, ol), (nt, ot), (np_, op), (ns, os_), (nba, oba) = [arr(sk, o) for o in (4, 12, 20, 28, 36)]
        lookup = _u16s(sk, nl, ol)
        tris = _u16s(sk, nt, ot)
        props = sk[op:op + 4 * np_]
        sec_size = 48 if self.version >= 264 else 32
        sections = []
        for i in range(ns):
            s = struct.unpack_from("<10H3f", sk, os_ + i * sec_size)
            sections.append(dict(id=s[0], level=s[1], vstart=s[2], vcount=s[3], istart=s[4] | (s[1] << 16),
                                 icount=s[5], bone_count=s[6], bone_combo=s[7], bone_infl=s[8], center_bone=s[9],
                                 center=s[10:13]))
        batches = [struct.unpack_from("<12H", sk, oba + i * 24) for i in range(nba)]
        # materials/textures
        txid = self.chunks.get("TXID", b"")
        txids = list(struct.unpack(f"<{len(txid) // 4}I", txid)) if txid else []
        n, o = A("textures")
        textures = []
        for i in range(n):
            ttype, tfl, fn_n, fn_o = struct.unpack_from("<IIII", core, o + i * 16)
            path = core[fn_o:fn_o + fn_n].split(b"\0")[0].decode("ascii", "replace") if fn_n else ""
            textures.append(dict(type=ttype, flags=tfl, path=path, fdid=txids[i] if i < len(txids) else 0))
        n, o = A("mats")
        mats = [struct.unpack_from("<HH", core, o + i * 4) for i in range(n)]
        lk = {k: _h16s(core, *A(k)) for k in ("bone_lookup", "key_bone", "tex_lookup", "tex_unit", "trans_lookup",
                                               "tt_lookup", "repl", "att_lookup", "seq_lookup", "cam_lookup")}
        # colors / weights / texture transforms
        n, o = A("colors")
        colors = [(self.track(o + i * 40, "vec3", DEFAULTS["color"]), self.track(o + i * 40 + 20, "fix16", DEFAULTS["alpha"]))
                  for i in range(n)]
        n, o = A("weights")
        weights = [self.track(o + i * 20, "fix16", DEFAULTS["weight"]) for i in range(n)]
        n, o = A("tex_trans")
        tts = [(self.track(o + i * 60, "vec3", DEFAULTS["tt_translation"]),
                self.track(o + i * 60 + 20, "quatf", DEFAULTS["tt_rotation"]),
                self.track(o + i * 60 + 40, "vec3", DEFAULTS["tt_scale"])) for i in range(n)]
        n, o = A("att")
        atts = []
        for i in range(n):
            aid, bone, _u = struct.unpack_from("<IHH", core, o + i * 40)
            pos = struct.unpack_from("<3f", core, o + i * 40 + 8)
            atts.append(dict(id=aid, bone=bone, pos=pos, track=self.track(o + i * 40 + 20, "u8", DEFAULTS["attach"])))
        n, o = A("events")
        events = []
        for i in range(n):
            ident, data, bone = struct.unpack_from("<4sII", core, o + i * 36)
            pos = struct.unpack_from("<3f", core, o + i * 36 + 12)
            events.append(dict(ident=ident, data=data, bone=bone, pos=pos, track=self.track(o + i * 36 + 24, None, events=True)))
        n, o = A("cams")
        cams = []
        for i in range(n):
            base = o + i * 116
            ctype, far, near = struct.unpack_from("<Iff", core, base)
            pos_base = struct.unpack_from("<3f", core, base + 32)
            tgt_base = struct.unpack_from("<3f", core, base + 64)
            # FoV spline track (value, in, out) @ +96: take the first key's value
            fov_tr = self.track(base + 96, "f32")
            fov_keys = [k for ks in fov_tr.keys for k in ks]
            fov = struct.unpack("<f", fov_keys[0][1])[0] if fov_keys else 0.7
            cams.append(dict(type=ctype, far=far, near=near, pos_base=pos_base, tgt_base=tgt_base, fov_v=fov))
        bb = struct.unpack_from("<3f3ff3f3ff", core, MOD["bounds"])
        bounds = (bb[0:3], bb[3:6], bb[6], bb[7:10], bb[10:13], bb[13])
        def raw_arr(k, stride):
            n, o = A(k)
            return core[o:o + n * stride]
        return Model(name=name, flags=flags, global_sequences=gseqs, sequences=seqs, bones=bones, vertices=verts,
                     lookup=lookup, tris=tris, props=props, sections=sections, batches=batches, textures=textures,
                     materials=mats, bone_lookup=lk["bone_lookup"], key_bone_lookup=lk["key_bone"],
                     texture_lookup=lk["tex_lookup"], texture_unit_lookup=lk["tex_unit"],
                     transparency_lookup=lk["trans_lookup"], tex_transform_lookup=lk["tt_lookup"],
                     replaceable_lookup=lk["repl"], colors=colors, transparency=weights, tex_transforms=tts,
                     attachments=atts, attachment_lookup=lk["att_lookup"], events=events, cameras=cams,
                     camera_lookup=lk["cam_lookup"], bounds=bounds, coll_tris=raw_arr("coll_tris", 2),
                     coll_verts=raw_arr("coll_verts", 12), coll_normals=raw_arr("coll_norm", 12), dropped=dropped)


# ------------------------------------------------------------------------------------------- v256 writer

V = dict(name=0x08, gseq=0x14, seqs=0x1C, anim_lookup=0x24, playable=0x2C, bones=0x34, key_bone=0x3C, verts=0x44,
         views=0x4C, colors=0x54, textures=0x5C, transparency=0x64, flipbooks=0x6C, tex_trans=0x74, repl=0x7C,
         mats=0x84, bone_lookup=0x8C, tex_lookup=0x94, tex_unit=0x9C, trans_lookup=0xA4, tt_lookup=0xAC,
         bounds=0xB4, coll_tris=0xEC, coll_verts=0xF4, coll_norm=0xFC, att=0x104, att_lookup=0x10C, events=0x114,
         lights=0x11C, cams=0x124, cam_lookup=0x12C, ribbons=0x134, particles=0x13C)
HEADER_SIZE = 0x144
NATIVE_KEY_BONES = 27  # vanilla key-bone enum stops at 26


class Blob:
    def __init__(self):
        self.b = bytearray(HEADER_SIZE)

    def put(self, data: bytes, align=16):
        if not data:
            return 0
        while len(self.b) % align:
            self.b.append(0)
        off = len(self.b)
        self.b += data
        return off

    def set(self, hdr_off, data: bytes, count: int):
        struct.pack_into("<II", self.b, hdr_off, count, self.put(data) if count else 0)


def plan_bands(seqs):
    """Assign each kept sequence a band [start, end] on the single native timeline; aliases share their target."""
    bands = [None] * len(seqs)
    t = 0
    for i, s in enumerate(seqs):
        if s["alias_of"] is not None:
            continue
        start = (t // 1000 + 1) * 1000
        bands[i] = (start, start + s["duration"])
        t = start + s["duration"]
    for i, s in enumerate(seqs):
        if s["alias_of"] is not None:
            bands[i] = bands[s["alias_of"]]
    return bands


class TrackWriter:
    def __init__(self, blob: Blob, src_index, bands, aliases):
        self.blob, self.src, self.bands, self.aliases = blob, src_index, bands, aliases

    def track_bytes(self, tr: Track | None, stride: int, events=False):
        """28-byte (20 for events) native track header, with arrays appended to the blob."""
        if tr is None:
            return bytes(20 if events else 28)
        if tr.gseq >= 0:
            ks = [k for sub in tr.keys for k in sub]
            return self._hdr(tr.interp, tr.gseq, [], [k[0] for k in ks], b"".join(k[1] for k in ks), len(ks), events)
        per = [tr.keys_of(si) for si in self.src]
        if not any(per):
            if events:  # shipped events keep one empty (0, 0) range per sequence
                return self._hdr(0, -1, [(0, 0)] * len(per), [], b"", 0, events)
            return self._hdr(tr.interp, -1, [], [], b"", 0, events)
        own = [p for i, p in enumerate(per) if self.aliases[i] is None]
        if not events and all(own) and len({v for p in own for _, v in p}) == 1:
            v = own[0][0][1]
            return self._hdr(tr.interp, -1, [], [0], v, 1, events)
        ranges = [None] * len(per)
        times, vals = [], []
        for i, p in enumerate(per):
            if self.aliases[i] is not None:
                continue
            start, end = self.bands[i]
            first = len(times)
            if p:
                for t, v in p:
                    times.append(start + min(t, end - start))
                    vals.append(v)
            elif not events and tr.default is not None:
                times += [start, end]
                vals += [tr.default, tr.default]
            ranges[i] = (first, len(times) if events else max(first, len(times) - 1))
        for i in range(len(per)):
            if self.aliases[i] is not None:
                ranges[i] = ranges[self.aliases[i]]
        # timestamps must ascend across bands (bands ascend in file order)
        require(all(a <= b for a, b in zip(times, times[1:])), "non-monotonic flattened timeline")
        return self._hdr(tr.interp, -1, ranges, times, b"".join(vals), len(times), events)

    def _hdr(self, interp, gseq, ranges, times, vals, nvals, events):
        rb = b"".join(struct.pack("<II", *r) for r in ranges)
        tb = struct.pack(f"<{len(times)}I", *times) if times else b""
        h = struct.pack("<Hh", interp, gseq)
        h += struct.pack("<II", len(ranges), self.blob.put(rb))
        h += struct.pack("<II", len(times), self.blob.put(tb))
        if not events:
            h += struct.pack("<II", nvals, self.blob.put(vals))
        return h


def write_v256(m: Model, keep_seq: list, aliases: list, name: str) -> bytes:
    """keep_seq: source sequence indices kept, in output order; aliases[i] = output index of alias target or None."""
    blob = Blob()
    struct.pack_into("<4sI", blob.b, 0, b"MD20", 256)
    nm = name.encode("ascii") + b"\0"
    blob.set(V["name"], nm, len(nm))
    struct.pack_into("<I", blob.b, 0x10, m.flags & 0x3)
    blob.set(V["gseq"], struct.pack(f"<{len(m.global_sequences)}I", *m.global_sequences), len(m.global_sequences))
    out_seqs = []
    for oi, si in enumerate(keep_seq):
        s = dict(m.sequences[si])
        s["alias_of"] = aliases[oi]
        out_seqs.append(s)
    bands = plan_bands(out_seqs)
    tw = TrackWriter(blob, keep_seq, bands, aliases)
    # sequences (68 B)
    sb = bytearray()
    for oi, s in enumerate(out_seqs):
        start, end = bands[oi]
        vnext = s["variation_next"]
        vnext = keep_seq.index(vnext) if vnext in keep_seq else -1
        anext = s["alias_next"]
        anext = keep_seq.index(anext) if anext in keep_seq else oi
        sb += struct.pack("<HHIIfIhHIII3f3ffhH", s["id"], s["variation"], start, end, s["movespeed"], s["flags"] & 0x1F,
                          s["frequency"], 0, s["replay"][0], s["replay"][1], s["blend"], *s["bmin"], *s["bmax"],
                          s["radius"], vnext, anext)
    blob.set(V["seqs"], bytes(sb), len(out_seqs))
    blob.set(V["anim_lookup"], struct.pack(f"<{len(m.animation_lookup)}h", *m.animation_lookup), len(m.animation_lookup))
    pl = b"".join(struct.pack("<HH", a, f) for a, f in m.playable_lookup)
    blob.set(V["playable"], pl, len(m.playable_lookup))
    # bones (108 B)
    bb = bytearray()
    for bn in m.bones:
        kb = bn["key"] if 0 <= bn["key"] < NATIVE_KEY_BONES else -1
        rec = struct.pack("<iIhH", kb, bn["flags"] & 0x3FF, bn["parent"], bn["submesh"])
        rec += tw.track_bytes(bn["translation"], 12)
        rec += tw.track_bytes(bn["rotation"], 16)
        rec += tw.track_bytes(bn["scale"], 12)
        rec += struct.pack("<3f", *bn["pivot"])
        require(len(rec) == 108, "bone record size")
        bb += rec
    blob.set(V["bones"], bytes(bb), len(m.bones))
    kbl = [-1] * NATIVE_KEY_BONES
    for i, bn in enumerate(m.bones):
        if 0 <= bn["key"] < NATIVE_KEY_BONES and kbl[bn["key"]] == -1:
            kbl[bn["key"]] = i
    blob.set(V["key_bone"], struct.pack(f"<{len(kbl)}h", *kbl), len(kbl))
    nv = len(m.vertices) // 48
    blob.set(V["verts"], m.vertices, nv)
    # colors (56 B), transparency (28 B), texture transforms (84 B)
    cb = b"".join(tw.track_bytes(c, 12) + tw.track_bytes(a, 2) for c, a in m.colors)
    blob.set(V["colors"], cb, len(m.colors))
    blob.set(V["transparency"], b"".join(tw.track_bytes(t, 2) for t in m.transparency), len(m.transparency))
    blob.set(V["tex_trans"], b"".join(tw.track_bytes(a, 12) + tw.track_bytes(b, 16) + tw.track_bytes(c, 12)
                                      for a, b, c in m.tex_transforms), len(m.tex_transforms))
    # textures (16 B): type, flags, filename
    tb = bytearray()
    for t in m.textures:
        fn = (t["path"].encode("ascii") + b"\0") if t["path"] else b""
        tb += struct.pack("<IIII", t["type"], t["flags"], len(fn), blob.put(fn, 1) if fn else 0)
    blob.set(V["textures"], bytes(tb), len(m.textures))
    for k, vals in (("repl", m.replaceable_lookup), ("bone_lookup", m.bone_lookup), ("tex_lookup", m.texture_lookup),
                    ("tex_unit", m.texture_unit_lookup), ("trans_lookup", m.transparency_lookup),
                    ("tt_lookup", m.tex_transform_lookup), ("att_lookup", m.attachment_lookup),
                    ("cam_lookup", m.camera_lookup)):
        blob.set(V[k], struct.pack(f"<{len(vals)}h", *vals), len(vals))
    blob.set(V["mats"], b"".join(struct.pack("<HH", *x) for x in m.materials), len(m.materials))
    b0, b1, br, c0, c1, cr = m.bounds
    struct.pack_into("<3f3ff3f3ff", blob.b, V["bounds"], *b0, *b1, br, *c0, *c1, cr)
    blob.set(V["coll_tris"], m.coll_tris, len(m.coll_tris) // 2)
    blob.set(V["coll_verts"], m.coll_verts, len(m.coll_verts) // 12)
    blob.set(V["coll_norm"], m.coll_normals, len(m.coll_normals) // 12)
    # attachments (48 B)
    ab = b"".join(struct.pack("<II3f", a["id"], a["bone"], *a["pos"]) + tw.track_bytes(a["track"], 1) for a in m.attachments)
    blob.set(V["att"], ab, len(m.attachments))
    # events (44 B)
    eb = b"".join(struct.pack("<4sII3f", e["ident"], e["data"], e["bone"], *e["pos"]) + tw.track_bytes(e["track"], 0, events=True)
                  for e in m.events)
    blob.set(V["events"], eb, len(m.events))
    # cameras (124 B): static position/target (no spline keys), diagonal fov from the modern vertical fov
    cam = bytearray()
    for c in m.cameras:
        fov_d = 2 * math.atan(math.tan(c["fov_v"] / 2) * (5.0 / 3.0))
        rec = struct.pack("<Ifff", c["type"], fov_d, c["far"], c["near"]) + bytes(28) + struct.pack("<3f", *c["pos_base"])
        rec += bytes(28) + struct.pack("<3f", *c["tgt_base"]) + bytes(28)
        require(len(rec) == 0x7C, "camera record size")
        cam += rec
    blob.set(V["cams"], bytes(cam), len(m.cameras))
    # view (skin profile), written 4 times like shipped creatures (same arrays)
    nl = len(m.lookup)
    lo = blob.put(struct.pack(f"<{nl}H", *m.lookup))
    to = blob.put(struct.pack(f"<{len(m.tris)}H", *m.tris))
    po = blob.put(m.props)
    sec = bytearray()
    for s in m.sections:
        require(s["istart"] <= 0xFFFF and s["vstart"] <= 0xFFFF, "section start exceeds u16 (prune geosets / LOD)")
        sec += struct.pack("<10H3f", s["id"], 0, s["vstart"], s["vcount"], s["istart"], s["icount"], s["bone_count"],
                           s["bone_combo"], s["bone_infl"], s["center_bone"], *s["center"])
    so = blob.put(bytes(sec))
    bo = blob.put(b"".join(struct.pack("<12H", *b) for b in m.batches))
    max_bones = max((s["bone_count"] for s in m.sections), default=0)
    view = struct.pack("<10II", nl, lo, len(m.tris), to, nl, po, len(m.sections), so, len(m.batches), bo, max_bones)
    vo = blob.put(view * 4)
    struct.pack_into("<II", blob.b, V["views"], 4, vo)
    return bytes(blob.b)


# ------------------------------------------------------------------------------------------- v256 reader

def read_v256(b: bytes, name="native") -> Model:
    """Reads a native MD20 v256 model into the same IR (one sequence list = one band each)."""
    require(b[:4] == b"MD20" and 256 <= struct.unpack_from("<I", b, 4)[0] <= 263, "not MD20 v256..263")
    A = lambda k: arr(b, V[k])
    n, o = A("seqs")
    seqs, bands = [], []
    for i in range(n):
        s = struct.unpack_from("<HHIIfIhHIII3f3ffhH", b, o + i * 68)
        seqs.append(dict(id=s[0], variation=s[1], duration=s[3] - s[2], movespeed=s[4], flags=s[5], frequency=s[6],
                         replay=(s[8], s[9]), blend=s[10], bmin=s[11:14], bmax=s[14:17], radius=s[17],
                         variation_next=s[18], alias_next=s[19], available=True))
        bands.append((s[2], s[3]))

    def track(base, stride, default, events=False):
        interp, gseq = struct.unpack_from("<Hh", b, base)
        nr, orr = arr(b, base + 4)
        nt, ot = arr(b, base + 12)
        nv, ov = (0, 0) if events else arr(b, base + 20)
        times = struct.unpack_from(f"<{nt}I", b, ot) if nt else ()
        vals = [b[ov + k * stride: ov + (k + 1) * stride] for k in range(nv)] if not events else [b""] * nt
        if gseq >= 0 or nr == 0:
            return Track(interp, gseq if gseq >= 0 else -1, [list(zip(times, vals))] if gseq >= 0 else
                         [[(0, vals[0])] if vals else [] for _ in seqs], default, stride)
        keys = []
        for i in range(len(seqs)):
            f, l = struct.unpack_from("<II", b, orr + i * 8) if i < nr else (0, 0)
            st, en = bands[i]
            last = l if not events else l - 1
            keys.append([(times[k] - st, vals[k]) for k in range(f, min(last, nt - 1) + 1) if st <= times[k] <= en])
        return Track(interp, -1, keys, default, stride)

    nb, ob = A("bones")
    bones = []
    for i in range(nb):
        base = ob + i * 108
        kb, fl, par, sub = struct.unpack_from("<iIhH", b, base)
        bones.append(dict(key=kb, flags=fl, parent=par, submesh=sub, crc=0, pivot=struct.unpack_from("<3f", b, base + 96),
                          translation=track(base + 12, 12, DEFAULTS["translation"]),
                          rotation=track(base + 40, 16, DEFAULTS["rotation"]),
                          scale=track(base + 68, 12, DEFAULTS["scale"])))
    nv, ov = A("verts")
    nvw, ovw = A("views")
    vp = ovw
    (nl, ol), (nt, ot), (np_, op), (ns, os_), (nba, oba) = [arr(b, vp + k * 8) for k in range(5)]
    sections = []
    for i in range(ns):
        s = struct.unpack_from("<10H3f", b, os_ + i * 32)
        sections.append(dict(id=s[0], level=0, vstart=s[2], vcount=s[3], istart=s[4], icount=s[5], bone_count=s[6],
                             bone_combo=s[7], bone_infl=s[8], center_bone=s[9], center=s[10:13]))
    n, o = A("textures")
    textures = []
    for i in range(n):
        tt, tf, fn_n, fn_o = struct.unpack_from("<IIII", b, o + i * 16)
        textures.append(dict(type=tt, flags=tf, path=b[fn_o:fn_o + fn_n].split(b"\0")[0].decode("ascii", "replace"), fdid=0))
    n, o = A("mats")
    mats = [struct.unpack_from("<HH", b, o + i * 4) for i in range(n)]
    L = {k: _h16s(b, *A(k)) for k in ("bone_lookup", "key_bone", "tex_lookup", "tex_unit", "trans_lookup", "tt_lookup",
                                      "repl", "att_lookup", "anim_lookup", "cam_lookup")}
    n, o = A("playable")
    playable = [struct.unpack_from("<HH", b, o + i * 4) for i in range(n)]
    n, o = A("att")
    atts = [dict(id=struct.unpack_from("<I", b, o + i * 48)[0], bone=struct.unpack_from("<I", b, o + i * 48 + 4)[0],
                 pos=struct.unpack_from("<3f", b, o + i * 48 + 8), track=track(o + i * 48 + 20, 1, DEFAULTS["attach"]))
            for i in range(n)]
    n, o = A("events")
    events = [dict(ident=b[o + i * 44:o + i * 44 + 4], data=struct.unpack_from("<I", b, o + i * 44 + 4)[0],
                   bone=struct.unpack_from("<I", b, o + i * 44 + 8)[0], pos=struct.unpack_from("<3f", b, o + i * 44 + 12),
                   track=track(o + i * 44 + 24, 0, None, events=True)) for i in range(n)]
    n, o = A("colors")
    colors = [(track(o + i * 56, 12, DEFAULTS["color"]), track(o + i * 56 + 28, 2, DEFAULTS["alpha"])) for i in range(n)]
    n, o = A("transparency")
    weights = [track(o + i * 28, 2, DEFAULTS["weight"]) for i in range(n)]
    bbv = struct.unpack_from("<3f3ff3f3ff", b, V["bounds"])
    nc, oc = A("coll_tris")
    ncv, ocv = A("coll_verts")
    return Model(name=name, flags=struct.unpack_from("<I", b, 0x10)[0], global_sequences=list(struct.unpack_from(f"<{A('gseq')[0]}I", b, A('gseq')[1])),
                 sequences=seqs, bones=bones, vertices=b[ov:ov + nv * 48], lookup=_u16s(b, nl, ol), tris=_u16s(b, nt, ot),
                 props=b[op:op + 4 * np_], sections=sections,
                 batches=[struct.unpack_from("<12H", b, oba + i * 24) for i in range(nba)], textures=textures,
                 materials=mats, bone_lookup=L["bone_lookup"], key_bone_lookup=L["key_bone"],
                 texture_lookup=L["tex_lookup"], texture_unit_lookup=L["tex_unit"], transparency_lookup=L["trans_lookup"],
                 tex_transform_lookup=L["tt_lookup"], replaceable_lookup=L["repl"], colors=colors, transparency=weights,
                 attachments=atts, attachment_lookup=L["att_lookup"], events=events, camera_lookup=L["cam_lookup"],
                 bounds=(bbv[0:3], bbv[3:6], bbv[6], bbv[7:10], bbv[10:13], bbv[13]), coll_tris=b[oc:oc + nc * 2],
                 coll_verts=b[ocv:ocv + ncv * 12], playable_lookup=playable, animation_lookup=L["anim_lookup"])
