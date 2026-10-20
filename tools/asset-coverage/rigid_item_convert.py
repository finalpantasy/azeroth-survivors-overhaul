#!/usr/bin/env python3
"""Generalized static ("rigid") modern-M2 -> native MD20 v256 converter for weapons, shields, shoulders and similar items.

Generalizes tools/retail-rigid-preview/build_retail_rigid.py (one pinned mace) to any item model that passes the
rigidity gate below.  Everything is read from a local cache directory of <FileDataID>.bin files (made with
casc_probe.py --dump-dir, so no game or CASC access happens here unless --fetch is given) and written to a NEW output
directory; nothing is installed.  The accepted regression is the original mace: this converter must reproduce its
preverified MD20 SHA-256 byte for byte (see test_rigid_item_convert.py).

Rigidity gate (all must hold, otherwise the model is rejected with the reason, never silently approximated):
  * MD21 core, version 264..274, exactly one external skin profile used (SFID[0]); skin level == 0 for every section
  * <= 1 sequence, no lights/cameras/particles/ribbons, no texture animations
  * every bone transform track (translation/rotation/scale) has zero keys, so a rest-pose collapse to one identity
    bone is exact; every attachment's attached-model track is empty
Kept: vertex positions/normals/UVs, skin lookup/triangles, every section and batch, attachments (identity bone),
first texture layer of each batch (type 0 from TXID, or type 2 replaceable via --tex / --auto-texture).
Dropped (reported): bone hierarchy, animation, events ($WT* weapon trails), extra texture layers, shader semantics.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import struct
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load_base():
    path = HERE.parent / "retail-rigid-preview" / "build_retail_rigid.py"
    spec = importlib.util.spec_from_file_location("build_retail_rigid_base", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


B = _load_base()
require = B.require
m2_array = B.m2_array
bounded_slice = B.bounded_slice
V = B.V256_ARRAY_OFFSETS
MOD = B.MODERN_ARRAY_OFFSETS

# Provisional modern->native blend-mode map (lab acceptance must confirm per recipe batch).
BLEND_MAP = {0: 0, 1: 1, 2: 2, 3: 3, 4: 3, 5: 4, 6: 5, 7: 3}


def sha256(b):
    return hashlib.sha256(b).hexdigest()


def read_cache(cache: Path, fdid: int, limit=64 * 1024 * 1024) -> bytes:
    p = cache / f"{fdid}.bin"
    require(p.is_file(), f"FileDataID {fdid} is not in the cache {cache} (use --fetch or casc_probe.py --dump-dir)")
    require(p.stat().st_size <= limit, f"{p} is larger than {limit} bytes")
    return p.read_bytes()


def fetch(cache: Path, profile: str, fdids):
    missing = [f for f in fdids if not (cache / f"{f}.bin").is_file()]
    if not missing:
        return
    ids = cache / "_fetch-ids.txt"
    cache.mkdir(parents=True, exist_ok=True)
    ids.write_text("\n".join(map(str, missing)), encoding="utf8")
    cmd = [sys.executable, "-I", str(HERE / "casc_probe.py"), "--profile", profile, "--ids", str(ids),
           "--out", str(cache / "_fetch-report.json"), "--dump-dir", str(cache), "--allow-client-read",
           "--total-cap", str(256 * 1024 * 1024)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    require(r.returncode == 0, f"fetch failed: {r.stderr[-300:]}")


def split_chunks(payload: bytes):
    chunks, cur = {}, 0
    while cur < len(payload):
        require(cur + 8 <= len(payload), f"truncated chunk header at 0x{cur:x}")
        tag = payload[cur:cur + 4].decode("ascii")
        size = struct.unpack_from("<I", payload, cur + 4)[0]
        require(cur + 8 + size <= len(payload), f"{tag} chunk overruns the file")
        chunks.setdefault(tag, payload[cur + 8:cur + 8 + size])
        cur += 8 + size
    require("MD21" in chunks, "not an MD21 chunked model")
    return chunks


def read_track_keys(core, start, label, stride, nseq):
    """Total number of keys in one animation track (0 means static)."""
    t = B.parse_modern_track(core, start, label, value_stride=stride, expected_sequences=nseq if nseq else None)
    return sum(t["sequence_key_counts"])


def track_is_identity(core, start, label, stride, nseq, name):
    """True when every key of the track equals the rest value (scale 1, translation 0, rotation 0,0,0,1)."""
    t = B.parse_modern_track(core, start, label, value_stride=stride, expected_sequences=nseq if nseq else None)
    for arr in t["sequence_value_arrays"]:
        for i in range(0, len(arr), stride):
            if name == "rotation":
                x, y, z, w = struct.unpack_from("<4h", arr, i)
                if max(abs(x), abs(y), abs(z)) > 64 or w < 32000:
                    return False
            else:
                vals = struct.unpack_from("<3f", arr, i)
                rest = 1.0 if name == "scale" else 0.0
                if any(abs(v - rest) > 1e-3 for v in vals):
                    return False
    return True


def inspect(model: bytes, skin: bytes, drop_texanim=False, dedupe_attachments=False, drop_effects=False, freeze_bones=False):
    """Validate the rigidity gate and return the parsed pieces."""
    chunks = split_chunks(model)
    core = chunks["MD21"]
    require(core[:4] == b"MD20", "MD21 core is not MD20")
    version = struct.unpack_from("<I", core, 4)[0]
    require(264 <= version <= 274, f"unsupported MD20 version {version}")
    counts = {n: m2_array(core, o, n) for n, o in MOD.items()}
    require(struct.unpack_from("<I", core, 0x44)[0] >= 1, "model has no external skin profile")
    nseq = counts["animations"][0]
    require(nseq <= 1, f"{nseq} sequences: not a static item")
    effect_kinds = ("lights", "ribbons", "particles")
    for n in ("cameras",) + (() if drop_effects else effect_kinds) + (() if drop_texanim else ("texture_animations",)):
        require(counts[n][0] == 0, f"{n} present ({counts[n][0]}): not a rigid item")
    dropped_effects = {n: counts[n][0] for n in effect_kinds if drop_effects and counts[n][0]}
    require(counts["vertices"][0] > 0, "no vertices")

    # bones: every transform track must be empty
    bcount, boff = counts["bones"]
    frozen_tracks = 0
    bounded_slice(core, boff, bcount, 88, "bones")
    for i in range(bcount):
        base = boff + i * 88
        for name, off, stride in (("translation", 16, 12), ("rotation", 36, 8), ("scale", 56, 12)):
            keys = read_track_keys(core, base + off, f"bone[{i}].{name}", stride, nseq)
            if keys and freeze_bones and track_is_identity(core, base + off, f"bone[{i}].{name}", stride, nseq, name):
                keys = 0
                frozen_tracks += 1
            require(keys == 0, f"bone {i} {name} is animated ({keys} keys): not a rigid item")

    # attachments (40 bytes): attached-model track must be empty
    acount, aoff = counts["attachments"]
    bounded_slice(core, aoff, acount, 40, "attachments")
    attachments = []
    for i in range(acount):
        base = aoff + i * 40
        aid, bone, _unk = struct.unpack_from("<IHH", core, base)
        pos = struct.unpack_from("<3f", core, base + 8)
        require(all(math.isfinite(x) for x in pos), "attachment position not finite")
        require(read_track_keys(core, base + 20, f"attachment[{i}]", 1, nseq) == 0, f"attachment {aid} has an animated track")
        attachments.append((aid, pos))
    if dedupe_attachments:
        seen, uniq = set(), []
        for a_, p_ in attachments:
            if a_ not in seen:
                seen.add(a_)
                uniq.append((a_, p_))
        attachments = uniq
    require(len({a for a, _ in attachments}) == len(attachments), "duplicate attachment ids")
    require(all(a < 256 for a, _ in attachments), "attachment id too large for the 1-byte lookup budget")

    # vertices
    vcount, voff = counts["vertices"]
    vblob = bounded_slice(core, voff, vcount, 48, "vertices")
    positions = []
    for i in range(vcount):
        v = vblob[i * 48:(i + 1) * 48]
        vals = struct.unpack_from("<3f", v, 0) + struct.unpack_from("<3f", v, 20) + struct.unpack_from("<4f", v, 32)
        require(all(math.isfinite(x) for x in vals), f"vertex {i} has a non-finite attribute")
        positions.append(struct.unpack_from("<3f", v, 0))
    require(vcount <= 65535, "vertex count exceeds u16 skin indexing")

    # skin
    require(skin[:4] == b"SKIN", "skin file is not SKIN")
    arrs = [struct.unpack_from("<II", skin, o) for o in (4, 12, 20, 28, 36)]
    (nl, ol), (nt, ot), (nprop, oprop), (nsec, osec), (nbat, obat) = arrs
    require(nsec >= 1 and nbat >= 1, "skin has no sections/batches")
    lookup = struct.unpack(f"<{nl}H", bounded_slice(skin, ol, nl, 2, "skin lookup"))
    tris = struct.unpack(f"<{nt}H", bounded_slice(skin, ot, nt, 2, "skin triangles"))
    require(all(x < vcount for x in lookup), "skin lookup escapes the vertex array")
    require(nt % 3 == 0 and all(x < nl for x in tris), "skin triangle indices malformed")
    require(nprop == nl, "skin property array length differs from the lookup length")
    sec_size = (obat - osec) // nsec if obat > osec else 48
    require(sec_size in (32, 48), f"unexpected skin section size {sec_size}")
    sections = []
    for i in range(nsec):
        s = struct.unpack_from("<8H", skin, osec + i * sec_size)
        require(s[1] == 0, f"section {i} has Level={s[1]} (large-mesh vertex offset): not supported")
        require(s[2] + s[3] <= nl and s[4] + s[5] <= nt, f"section {i} escapes the skin arrays")
        sections.append(s)
    batches = []
    for i in range(nbat):
        batches.append(struct.unpack_from("<12H", skin, obat + i * 24))

    # materials / textures
    ncount, noff = counts["textures"]
    tex = [struct.unpack_from("<II", core, noff + i * 16) for i in range(ncount)]
    mcount, moff = counts["render_flags"]
    mats = [struct.unpack_from("<HH", core, moff + i * 4) for i in range(mcount)]
    tlk_n, tlk_o = counts["texture_lookup"]
    tlookup = list(struct.unpack(f"<{tlk_n}H", bounded_slice(core, tlk_o, tlk_n, 2, "texture lookup")))
    for b in batches:
        require(b[2] < nsec and b[8] < len(tlookup) and b[5] < max(len(mats), 1), "batch indexes escape section/texture/material arrays")
    return dict(version=version, core=core, chunks=chunks, counts=counts, vcount=vcount, vblob=vblob, positions=positions,
                lookup=lookup, tris=tris, sections=sections, sec_size=sec_size, batches=batches, tex=tex, mats=mats,
                tlookup=tlookup, attachments=attachments, bones=bcount, sequences=nseq, skin_arrays=arrs,
                dropped_effects=dropped_effects, frozen_tracks=frozen_tracks)


RACE_SUFFIXES = ("bef", "bem", "drf", "drm", "dwf", "dwm", "gnf", "gnm", "gof", "gom", "huf", "hum", "nif", "nim", "orf", "orm",
                 "scf", "scm", "taf", "tam", "trf", "trm")


_LISTFILE_INDEX = {}


def make_listfile_pick(listfile: Path, model_fdid: int):
    """Sibling-BLP heuristic: BLPs next to the model whose name starts with the model stem (exact stem.blp preferred)."""
    key = str(listfile)
    if key not in _LISTFILE_INDEX:       # the listfile has ~1.2M lines: index it once per process
        names, by_dir = {}, {}
        for line in listfile.read_text(encoding="utf8").splitlines():
            f, _, n = line.partition(";")
            if n.endswith(".blp"):
                by_dir.setdefault(n.rpartition("/")[0], []).append((n.rpartition("/")[2], int(f)))
            elif n.endswith(".m2"):
                names[f] = n
        _LISTFILE_INDEX[key] = (names, by_dir)
    names, by_dir = _LISTFILE_INDEX[key]
    path = names.get(str(model_fdid))
    require(path, f"model FDID {model_fdid} not found in {listfile}")
    d, _, b = path.rpartition("/")
    stem = b[:-3]
    stems = [stem]
    if len(stem) > 4 and stem[-4] == "_" and stem[-3:] in RACE_SUFFIXES:    # head models: one BLP set serves all race/sex variants
        stems.append(stem[:-4])
    if stem[:9] in ("lshoulder", "rshoulder"):                              # shoulder BLPs drop the L/R prefix
        stems.append(stem[1:])
    sib = sorted({x for st in stems for x in by_dir.get(d, []) if x[0].startswith(st)})
    require(sib, f"no sibling BLP for {path}")
    pick = next((x for st in stems for x in sib if x[0] == st + ".blp"), sib[0])

    def f(_slot, _typ):
        return pick[1]
    f.chosen = pick
    f.candidates = sib
    return f


def derive_textures(info, tex_overrides, listfile_pick=None):
    """Choose one BLP FileDataID per texture slot used by layer 0 of any batch."""
    txid = info["chunks"].get("TXID")
    txids = list(struct.unpack(f"<{len(txid) // 4}I", txid)) if txid else []
    used = {}
    for b in info["batches"]:
        slot = info["tlookup"][b[8]]  # textureComboIndex -> texture slot (layer 0)
        used[slot] = None
    for slot in used:
        typ = info["tex"][slot][0]
        if slot in tex_overrides:
            used[slot] = tex_overrides[slot]
        elif typ == 0 and slot < len(txids) and txids[slot]:
            used[slot] = txids[slot]
        elif listfile_pick is not None:
            used[slot] = listfile_pick(slot, typ)
        require(used[slot], f"texture slot {slot} (type {typ}) has no FileDataID: pass --tex {slot}=<fdid> or --auto-texture")
    return used


def build(info, out_stem, tex_paths, display_name):
    """Assemble the MD20 v256 model; tex_paths maps slot -> internal BLP path (backslashes)."""
    n = info["vcount"]
    positions = info["positions"]
    mins = tuple(min(p[a] for p in positions) for a in range(3))
    maxs = tuple(max(p[a] for p in positions) for a in range(3))
    center = tuple(sum(p[a] for p in positions) / n for a in range(3))
    radius_c = max(math.dist(p, center) for p in positions)
    bounds = {"mins": mins, "maxs": maxs, "radius": radius_c}

    model = bytearray(0x144)
    struct.pack_into("<4sI", model, 0, b"MD20", 256)
    struct.pack_into("<I", model, 0x10, 0)
    name = display_name.encode("ascii") + b"\0"
    B.set_array(model, V["name"], name, len(name), 1)
    B.set_array(model, V["animations"], B.neutral_sequence_v256(bounds), 1)
    B.set_array(model, V["bones"], B.empty_bone_v256(), 1)
    att = info["attachments"]
    if att:
        max_id = max(a for a, _ in att)
        lookup = [0xFFFF] * (max_id + 1)
        recs = bytearray()
        for idx, (aid, pos) in enumerate(att):
            r = bytearray(48)
            struct.pack_into("<II3f", r, 0, aid, 0, *pos)
            recs += r
            lookup[aid] = idx
        B.set_array(model, V["attachments"], bytes(recs), len(att))
        B.set_array(model, V["attachment_lookup"], struct.pack(f"<{len(lookup)}H", *lookup), len(lookup), 2)

    out_v = bytearray(info["vblob"])
    for i in range(n):
        out_v[i * 48 + 12:i * 48 + 16] = b"\xff\0\0\0"
        out_v[i * 48 + 16:i * 48 + 20] = b"\0\0\0\0"
    B.set_array(model, V["vertices"], bytes(out_v), n)

    nl = len(info["lookup"])
    view_off = B.append_payload(model, b"\0" * 44)
    struct.pack_into("<II", model, V["views"], 1, view_off)
    view = bytearray(44)
    for i, (data, cnt) in enumerate(((struct.pack(f"<{nl}H", *info["lookup"]), nl),
                                      (struct.pack(f"<{len(info['tris'])}H", *info["tris"]), len(info["tris"])),
                                      (b"\0" * (4 * nl), nl))):
        struct.pack_into("<II", view, i * 8, cnt, B.append_payload(model, data))
    secs = bytearray()
    for s in info["sections"]:
        sid, _lvl, vs, vc, is_, ic, _bc, _bs = s
        sub = [info["positions"][info["lookup"][k]] for k in range(vs, vs + vc)] or [center]
        c = tuple(sum(p[a] for p in sub) / len(sub) for a in range(3))
        r = max(math.dist(p, c) for p in sub)
        secs += struct.pack("<8H4f", sid, 0, vs, vc, is_, ic, 1, 0, *c, r)
    struct.pack_into("<II", view, 24, len(info["sections"]), B.append_payload(model, bytes(secs)))

    slots = sorted(tex_paths)
    mats = info["mats"]
    mat_used = sorted({b[5] for b in info["batches"]})
    mat_remap = {m: i for i, m in enumerate(mat_used)}
    bat = bytearray()
    for b in info["batches"]:
        _h0, _shader, sec_idx, geo, _color, mat, layer, _tcount, tcombo, _tcoord, _tweight, _ttrans = b
        slot = info["tlookup"][tcombo]
        bat += struct.pack("<12H", 0, 0, sec_idx, geo, 0xFFFF, mat_remap[mat], layer, 1, slots.index(slot), 0, 0xFFFF, 0xFFFF)
    struct.pack_into("<II", view, 32, len(info["batches"]), B.append_payload(model, bytes(bat)))
    struct.pack_into("<I", view, 40, 1)
    model[view_off:view_off + 44] = view

    # textures: one record per used slot, in slot order; texture_lookup maps slot -> record index
    trec = bytearray()
    for slot in slots:
        nm = tex_paths[slot].encode("ascii") + b"\0"
        off = B.append_payload(model, nm, 1)
        trec += struct.pack("<IIII", 0, 0, len(nm), off)
    B.set_array(model, V["textures"], bytes(trec), len(slots))
    rf = bytearray()
    for m in mat_used:
        flags, blend = mats[m]
        rf += struct.pack("<HH", flags & 0xFF, BLEND_MAP.get(blend, 0))
    B.set_array(model, V["render_flags"], bytes(rf), len(mat_used))
    B.set_array(model, V["bone_lookup"], struct.pack("<H", 0), 1, 2)
    B.set_array(model, V["texture_lookup"], struct.pack(f"<{len(slots)}H", *range(len(slots))), len(slots), 2)
    B.set_array(model, V["texture_unit_lookup"], struct.pack("<H", 0), 1, 2)

    radius0 = max(math.dist(p, (0.0, 0.0, 0.0)) for p in positions)
    struct.pack_into("<3f", model, 0xB4, *mins)
    struct.pack_into("<3f", model, 0xC0, *maxs)
    struct.pack_into("<f", model, 0xCC, radius0)
    struct.pack_into("<3f", model, 0xD0, *mins)
    struct.pack_into("<3f", model, 0xDC, *maxs)
    struct.pack_into("<f", model, 0xE8, radius0)
    return bytes(model), {"vertices": n, "triangles": len(info["tris"]) // 3, "sections": len(info["sections"]),
                          "batches": len(info["batches"]), "textures": len(slots), "attachments": len(att),
                          "bounds": {"mins": mins, "maxs": maxs, "radius": radius0}}


def validate_output(md20: bytes, info):
    require(md20[:4] == b"MD20" and struct.unpack_from("<I", md20, 4)[0] == 256, "output is not MD20 v256")
    arrays = {n: m2_array(md20, o, n) for n, o in V.items()}
    require(arrays["vertices"][0] == info["vcount"] and arrays["views"][0] == 1 and arrays["bones"][0] == 1, "output counts")
    vd = bounded_slice(md20, arrays["vertices"][1], arrays["vertices"][0], 48, "output vertices")
    for i in range(info["vcount"]):
        a, o = vd[i * 48:(i + 1) * 48], info["vblob"][i * 48:(i + 1) * 48]
        require(a[:12] == o[:12] and a[20:] == o[20:] and a[12:20] == b"\xff\0\0\0\0\0\0\0", f"vertex {i} changed")
    view = bounded_slice(md20, arrays["views"][1], 1, 44, "output view")
    secs = struct.unpack_from("<II", view, 24)
    bats = struct.unpack_from("<II", view, 32)
    require(secs[0] == len(info["sections"]) and bats[0] == len(info["batches"]), "output section/batch counts")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cache", type=Path, required=True, help="directory of <FileDataID>.bin files")
    ap.add_argument("--model", type=int, required=True, help="modern M2 FileDataID")
    ap.add_argument("--name", required=True, help="output model stem, e.g. KultirasQuestB01_Silver_Static")
    ap.add_argument("--kind", default="Weapon", choices=("Weapon", "Shield", "Shoulder", "Head", "Other"))
    ap.add_argument("--tex", action="append", default=[], metavar="SLOT=FDID", help="BLP FileDataID for a texture slot")
    ap.add_argument("--out-dir", type=Path, required=True, help="NEW output directory (never overwritten)")
    ap.add_argument("--native-model-path", help=r"internal MPQ path to publish the model under (default Item\ObjectComponents\<Kind>\<name>.m2)")
    ap.add_argument("--fetch", choices=("retail", "forever"), help="read missing inputs from the offline CASC via casc_probe.py")
    ap.add_argument("--packer", type=Path, default=HERE.parent / "pack-mpq.py")
    ap.add_argument("--preflight", action="store_true", help="only evaluate the rigidity gate")
    ap.add_argument("--auto-texture", type=Path, metavar="LISTFILE_CSV", help="resolve replaceable texture slots from sibling BLPs in a 'fdid;path' listfile")
    ap.add_argument("--drop-texture-animations", action="store_true", help="accept models with UV-scroll texture animations (they are dropped and reported)")
    ap.add_argument("--dedupe-attachments", action="store_true", help="keep only the first of duplicate attachment ids")
    ap.add_argument("--freeze-bones", action="store_true", help="accept animated bone tracks whose keys are all the rest value (identity)")
    ap.add_argument("--drop-effects", action="store_true", help="accept models with particle emitters/ribbons/lights (dropped and reported)")
    a = ap.parse_args()

    if a.fetch:
        fetch(a.cache, a.fetch, [a.model])
    model = read_cache(a.cache, a.model)
    chunks = split_chunks(model)
    sfid = chunks.get("SFID")
    require(sfid and len(sfid) >= 4, "model has no SFID chunk (skin FileDataIDs)")
    skin_id = struct.unpack_from("<I", sfid, 0)[0]
    txid = chunks.get("TXID")
    txids = list(struct.unpack(f"<{len(txid) // 4}I", txid)) if txid else []
    overrides = {int(k): int(v) for k, v in (t.split("=") for t in a.tex)}
    if a.fetch:
        fetch(a.cache, a.fetch, [skin_id] + [t for t in txids if t] + list(overrides.values()))
    skin = read_cache(a.cache, skin_id)
    info = inspect(model, skin, a.drop_texture_animations, a.dedupe_attachments, a.drop_effects, a.freeze_bones)
    report = {"model_fdid": a.model, "skin_fdid": skin_id, "version": info["version"], "gate": "pass",
              "source": {"vertices": info["vcount"], "triangles": len(info["tris"]) // 3, "sections": len(info["sections"]),
                         "batches": len(info["batches"]), "bones": info["bones"], "sequences": info["sequences"],
                         "texture_types": [t[0] for t in info["tex"]], "txid": txids}}
    if a.preflight:
        print(json.dumps(report, indent=1))
        return 0
    pick = make_listfile_pick(a.auto_texture, a.model) if a.auto_texture else None
    slots = derive_textures(info, {k: v for k, v in overrides.items()}, pick)
    if pick is not None:
        report["auto_texture"] = {"chosen": list(pick.chosen), "candidates": [list(c) for c in pick.candidates][:12]}
    if a.fetch:
        fetch(a.cache, a.fetch, list(slots.values()))
    kind_dir = f"Item\\ObjectComponents\\{a.kind}" if a.kind != "Other" else "Item\\ObjectComponents"
    model_internal = a.native_model_path or f"{kind_dir}\\{a.name}.m2"
    base_dir = model_internal.rsplit("\\", 1)[0]
    uniq = sorted(set(slots.values()), key=lambda f: min(sl for sl, fd in slots.items() if fd == f))
    path_of = {fd: base_dir + chr(92) + a.name + ("" if len(uniq) == 1 else f"_{i}") + ".blp" for i, fd in enumerate(uniq)}
    tex_internal = {slot: path_of[fd] for slot, fd in slots.items()}
    md20, stats = build(info, a.name, tex_internal, a.name)
    validate_output(md20, info)

    out = a.out_dir.resolve()
    require(not out.exists(), f"refusing to overwrite {out}")
    payload = out / "payload"
    for fdid in uniq:
        slot = min(sl for sl, fd in slots.items() if fd == fdid)
        blp = read_cache(a.cache, fdid)
        require(blp[:4] == b"BLP2", f"texture {fdid} is not BLP2")
        p = payload / Path(tex_internal[slot].replace("\\", "/"))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(blp)
    mp = payload / Path(model_internal.replace("\\", "/"))
    mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_bytes(md20)
    archive = out / f"{a.name}.mpq"
    B.arm_conversion_job()
    log = B.run_tool([sys.executable, str(a.packer), str(payload), str(archive)], out / "pack-mpq.txt")
    report.update({"output": {"model_path": model_internal, "model_bytes": len(md20), "model_sha256": sha256(md20),
                              "textures": {str(s): {"fdid": slots[s], "path": tex_internal[s]} for s in slots},
                              "mpq_bytes": archive.stat().st_size, "packer_log": log.strip().splitlines()[-1] if log.strip() else ""},
                   "stats": stats,
                   "dropped": ["bone hierarchy (collapsed to one identity bone)", "animation (neutral 1000 ms sequence)",
                               "events ($WT* weapon trails)", "texture animations (UV scroll) if --drop-texture-animations", "texture layers beyond layer 0", "modern shader semantics",
                               "blend modes mapped provisionally via BLEND_MAP: lab acceptance required"],
                   "installed": False})
    (out / "conversion-report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf8")
    print(json.dumps({"status": "success", "model_sha256": sha256(md20), "stats": stats, "out": str(out)}, indent=1))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"rigid_item_convert: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
