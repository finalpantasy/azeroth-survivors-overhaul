#!/usr/bin/env python3
"""Skinned (animated) modern M2 -> native MD20 v256 converter (recipe R4).

Input: a recipe JSON (see recipes/*.json) naming a modern model FileDataID, its skin, the native model path it
replaces and texture mappings; all bytes are read from a local cache of <FileDataID>.bin files (casc_probe.py
--dump-dir; --fetch reads missing ones from the offline CASC). Output: a NEW directory with payload/ (model + BLPs at
their native MPQ paths), an MPQ overlay, conversion-report.json and preview sheets. Nothing is installed.

Kept: vertices and bone weights, full bone hierarchy and keyframes (flattened onto the native single timeline),
every sequence whose data is present (embedded, or external .anim when its file is in the cache) plus aliases,
AnimationLookup/PlayableAnimationLookup (native template + AnimationData fallbacks), attachments, events, cameras
(static), colors/transparency/texture transforms, collision hull, bounds.
Changed: geosets pruned to the recipe's selection with vertex compaction; multi-texture batches split into a base layer
and an additive env-map layer (or dropped); key bones >= 27 cleared; modern blend 7 -> 4.
Dropped (reported): particles, ribbons, lights, modern shader combiners, camera spline animation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import m2ir  # noqa: E402
import decimate  # noqa: E402
from m2ir import require  # noqa: E402

ROOT = HERE.parent.parent  # AzerothSurvivors/
MPQ_INSPECT = ROOT / "tools" / "mpq-inspect.exe"
PACKER = ROOT / "tools" / "pack-mpq.py"
CASC_PROBE = ROOT / "tools" / "asset-coverage" / "casc_probe.py"
BLEND = {0: 0, 1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6, 7: 4}


def sha256(b):
    return hashlib.sha256(b).hexdigest()


def read_cache(cache: Path, fdid: int) -> bytes:
    p = cache / f"{fdid}.bin"
    require(p.is_file(), f"FileDataID {fdid} not in cache {cache} (use --fetch)")
    return p.read_bytes()


def fetch(cache: Path, profile: str, fdids):
    missing = sorted({f for f in fdids if f and not (cache / f"{f}.bin").is_file()})
    if not missing:
        return
    cache.mkdir(parents=True, exist_ok=True)
    ids = cache / "_fetch-ids.txt"
    ids.write_text("\n".join(map(str, missing)), encoding="utf8")
    r = subprocess.run([sys.executable, "-I", str(CASC_PROBE), "--profile", profile, "--ids", str(ids), "--out",
                        str(cache / "_fetch-report.json"), "--dump-dir", str(cache), "--allow-client-read"],
                       capture_output=True, text=True, timeout=600)
    require(r.returncode == 0, f"fetch failed: {r.stderr[-400:]}")


def native_template(data_dir: Path, internal: str, work: Path, game_dir: Path | None = None) -> bytes | None:
    """Extract the native model being replaced (read-only) to reuse its PlayableAnimationLookup. With game_dir (an
    installer game-extract step folder) the file is read from there instead of the MPQ chain."""
    if game_dir is not None:
        p = game_dir.joinpath(*internal.split("\\"))
        return p.read_bytes() if p.is_file() else None
    dest = work / ("template-" + internal.replace("\\", "_"))
    if not dest.is_file():
        r = subprocess.run([str(MPQ_INSPECT), str(data_dir), "extract", internal, str(dest)], capture_output=True, text=True)
        if r.returncode != 0:
            return None
    return dest.read_bytes()


def load_animation_fallbacks(data_dir: Path, work: Path, game_dir: Path | None = None):
    dest = game_dir / "DBFilesClient" / "AnimationData.dbc" if game_dir is not None else work / "AnimationData.dbc"
    if game_dir is not None:
        require(dest.is_file(), f"{dest} missing from the game-extract step")
    elif not dest.is_file():
        subprocess.run([str(MPQ_INSPECT), str(data_dir), "extract", "DBFilesClient\\AnimationData.dbc", str(dest)],
                       capture_output=True, text=True, check=True)
    b = dest.read_bytes()
    _m, n, _f, rs, _ss = struct.unpack_from("<4sIIII", b, 0)
    fb = {}
    for i in range(n):
        row = struct.unpack_from("<7I", b, 20 + i * rs)
        fb[row[0]] = row[5]
    return fb


# --------------------------------------------------------------------------------------------- stages

def select_sequences(m: m2ir.Model, drop_ids=()):
    """Keep sequences with data (embedded or cached .anim) and aliases that resolve to a kept one."""
    def target(i, depth=0):
        s = m.sequences[i]
        if s["flags"] & 0x40 and depth < 16 and s["alias_next"] != i and s["alias_next"] < len(m.sequences):
            return target(s["alias_next"], depth + 1)
        return i
    keep, dropped = [], []
    for i, s in enumerate(m.sequences):
        t = target(i)
        if s["id"] in drop_ids:
            dropped.append(f"seq {i} anim {s['id']}.{s['variation']} (recipe drop)")
        elif (m.sequences[t]["flags"] & 0x40) or not m.sequences[t]["available"]:
            dropped.append(f"seq {i} anim {s['id']}.{s['variation']} (data unavailable)")
        else:
            keep.append(i)
    aliases = []
    for i in keep:
        t = target(i)
        aliases.append(keep.index(t) if t != i else None)
        if t != i:
            require(t in keep, f"alias target {t} dropped")
    return keep, aliases, dropped


def default_geosets(m):
    ids = sorted({s["id"] for s in m.sections})
    groups = {}
    for g in ids:
        groups.setdefault(g // 100, []).append(g)
    sel = set(groups.get(0, []))
    for grp, members in groups.items():
        if grp and grp * 100 + 1 in members:
            sel.add(grp * 100 + 1)
    return sorted(sel)


def prune_geosets(m: m2ir.Model, keep_ids):
    keep_sec = [i for i, s in enumerate(m.sections) if s["id"] in keep_ids]
    require(keep_sec, f"geoset selection {keep_ids} keeps no section")
    sec_map = {old: new for new, old in enumerate(keep_sec)}
    new_lookup, new_tris, new_props, new_secs = [], [], bytearray(), []
    for old in keep_sec:
        s = dict(m.sections[old])
        v0 = len(new_lookup)
        new_lookup += m.lookup[s["vstart"]:s["vstart"] + s["vcount"]]
        new_props += m.props[4 * s["vstart"]:4 * (s["vstart"] + s["vcount"])]
        i0 = len(new_tris)
        new_tris += [t - s["vstart"] + v0 for t in m.tris[s["istart"]:s["istart"] + s["icount"]]]
        for t in new_tris[i0:]:
            require(v0 <= t < len(new_lookup), f"section {old} triangle escapes its vertex range")
        s.update(vstart=v0, istart=i0, level=0)
        new_secs.append(s)
    # compact global vertices
    used = sorted(set(new_lookup))
    gmap = {g: i for i, g in enumerate(used)}
    verts = b"".join(m.vertices[g * 48:(g + 1) * 48] for g in used)
    require(len(used) <= 65535, f"{len(used)} vertices after pruning exceed u16")
    require(len(new_tris) <= 65535, f"{len(new_tris)} triangle indices exceed the u16 section start")
    m.vertices, m.lookup, m.tris, m.props, m.sections = verts, [gmap[g] for g in new_lookup], new_tris, bytes(new_props), new_secs
    m.batches = [tuple([b[0], b[1], sec_map[b[2]], sec_map[b[2]]] + list(b[4:])) for b in m.batches if b[2] in sec_map]
    return {"kept_geosets": sorted({s["id"] for s in new_secs}), "sections": len(new_secs), "vertices": len(used),
            "triangles": len(new_tris) // 3}


def trim_bones(m: m2ir.Model):
    """Drop bones nothing needs after geoset pruning: keep every bone that skins a kept vertex (weight > 0), is named
    by an attachment or section centre, or holds a native key-bone id (< 27), plus all their ancestors. Events
    (sounds, footsteps) do not pin bones: they move to the nearest kept ancestor. Bone
    order is preserved, so parents still precede children. Zero-weight vertex slots and bone-lookup entries that named
    a dropped bone are pointed at its nearest kept ancestor (they carry no weight, so the pose is unchanged)."""
    n = len(m.bones)
    nv = len(m.vertices) // 48
    v = bytearray(m.vertices)
    need = {s["center_bone"] for s in m.sections} | {a["bone"] for a in m.attachments}
    need |= {i for i, bn in enumerate(m.bones) if 0 <= bn["key"] < m2ir.NATIVE_KEY_BONES}
    for i in range(nv):
        for k in range(4):
            if v[i * 48 + 12 + k]:
                need.add(v[i * 48 + 16 + k])
    keep = set()
    for b in need:
        while 0 <= b < n and b not in keep:
            keep.add(b)
            b = m.bones[b]["parent"]
    if len(keep) == n:
        return {"bones_before": n, "bones_after": n}
    order = sorted(keep)
    nmap = {o: i for i, o in enumerate(order)}

    old = m.bones

    def kept(b):
        while 0 <= b < n and b not in nmap:
            b = old[b]["parent"]
        return nmap.get(b, 0)

    bones = []
    for o in order:
        bn = dict(m.bones[o])
        bn["parent"] = nmap[bn["parent"]] if bn["parent"] >= 0 else -1
        bones.append(bn)
    for i in range(nv):
        for k in range(4):
            j = i * 48 + 16 + k
            v[j] = nmap[v[j]] if v[i * 48 + 12 + k] else kept(v[j])
    m.bones, m.vertices = bones, bytes(v)
    m.bone_lookup = [kept(b) for b in m.bone_lookup]
    m.key_bone_lookup = [nmap.get(b, -1) if b >= 0 else -1 for b in m.key_bone_lookup]
    for s in m.sections:
        s["center_bone"] = kept(s["center_bone"])
    for a in m.attachments:
        a["bone"] = nmap[a["bone"]]
    for e in m.events:  # sound/footstep markers: their position stays, the bone moves to the nearest kept ancestor
        e["bone"] = kept(e["bone"])
    return {"bones_before": n, "bones_after": len(bones)}


def rewrite_batches(m: m2ir.Model, env_mode: str, layers: dict | None = None):
    """Native batches carry one texture each; split modern multi-texture batches (layer 0 + additive env layer).
    layers (recipe "batch_layers"): {section index: {"layer": k, "uv": 0|1}} draws that section's batch with texture
    layer k sampled with UV set 0 or 1 instead of layer 0 / UV 0 (modern combiners that shape a tint with a second
    texture on the second UV set, e.g. voidwalker2's smoke cards)."""
    layers = {int(k): v for k, v in (layers or {}).items()}
    uv_unit = {}
    # Modern files leave textureCoordCombo lookup empty (UV set comes from the shader id); the native engine reads
    # texture_unit_lookup[texCoordCombo] and treats a value > 2 (or a missing entry) as a sphere env-map. Shipped
    # native layering (Murloc.m2): texture_unit_lookup [0, -1]; env layer = material (0x10, blend 4), layer + 1.
    out, notes = [], []
    env_mat = None
    m.texture_unit_lookup = [0]
    for b in m.batches:
        fl, shader, sec, geo, color, mat, layer, tcount, tcombo, tcoord, tweight, ttrans = b
        if sec in layers:
            k, uvs = int(layers[sec].get("layer", 0)), int(layers[sec].get("uv", 0))
            require(k < tcount, f"batch_layers: section {sec} has {tcount} texture layer(s), not {k + 1}")
            if uvs not in uv_unit:
                uv_unit[uvs] = 0 if uvs == 0 else len(m.texture_unit_lookup)
                if uvs:
                    m.texture_unit_lookup.append(uvs)
            out.append((fl & 0xFF3F, 0, sec, geo, color, mat, layer, 1, tcombo + k, uv_unit[uvs], tweight, ttrans))
            notes.append(f"batch sec {sec}: texture layer {k} on UV set {uvs} (recipe batch_layers); other layers dropped")
            continue
        out.append((fl & 0xFF3F, 0, sec, geo, color, mat, layer, 1, tcombo, 0, tweight, ttrans))
        if tcount >= 2:
            if env_mode == "split" and tcombo + 1 < len(m.texture_lookup):
                if env_mat is None:
                    m.materials.append((0x10, 4))
                    env_mat = len(m.materials) - 1
                    m.texture_unit_lookup.append(-1)
                out.append((fl & 0xFF3F, 0, sec, geo, color, env_mat, layer + 1, 1, tcombo + 1, 1, tweight, ttrans))
                notes.append(f"batch sec {sec}: texture 2 split into an additive env-map layer (native Murloc layout)")
            else:
                notes.append(f"batch sec {sec}: {tcount - 1} extra texture layer(s) dropped")
    m.batches = out
    m.materials = [(f & 0xFF, BLEND.get(bl, 0)) for f, bl in m.materials]
    return notes


NATIVE_MAX_TEX_TYPE = 13  # vanilla replaceable types end at 13 (monster skin 3); modern 14+ are customization slots


def drop_unused_textures(m: m2ir.Model):
    """Remove textures no kept batch samples (after geoset pruning), remapping texture_lookup and the replaceable
    lookup. Used when the model carries modern texture types the native engine does not know (characters)."""
    used = sorted({m.texture_lookup[b[8] + k] for b in m.batches for k in range(b[7])})
    remap = {old: new for new, old in enumerate(used)}
    removed = [{"slot": i, "type": t["type"], "fdid": t["fdid"]} for i, t in enumerate(m.textures) if i not in remap]
    for i, t in enumerate(m.textures):
        t["recipe_slot"] = i  # recipe "textures" keys name the source slots
    m.textures = [m.textures[i] for i in used]
    m.texture_lookup = [remap.get(v, 0) for v in m.texture_lookup]
    m.replaceable_lookup = [remap.get(v, -1) if v >= 0 else -1 for v in m.replaceable_lookup]
    return removed


def build_lookups(m: m2ir.Model, keep, aliases, template: m2ir.Model | None, fallbacks):
    ids = [m.sequences[i]["id"] for i in keep]
    first = {}
    for oi, i in enumerate(keep):  # head of each id's variation chain: variation 0, else first in file order
        s = m.sequences[i]
        if s["id"] not in first or (s["variation"] == 0 and m.sequences[keep[first[s["id"]]]]["variation"] != 0):
            first[s["id"]] = oi
    n_anim = max([len(template.animation_lookup) if template else 0] + [x + 1 for x in ids])
    m.animation_lookup = [first.get(a, -1) for a in range(n_anim)]
    have = set(ids)
    n_play = max(len(template.playable_lookup) if template else 0, max(fallbacks) + 1 if fallbacks else 0)
    play = []
    for a in range(n_play):
        if a in have:
            play.append((a, 0))
            continue
        tid, tfl = (template.playable_lookup[a] if template and a < len(template.playable_lookup) else (None, 0))
        if tid is not None and tid in have:
            play.append((tid, tfl))
            continue
        cur, seen = a, set()
        while cur not in have and cur not in seen:
            seen.add(cur)
            cur = fallbacks.get(cur, 0)
        play.append((cur if cur in have else 0, tfl if tid == cur else 0))
    m.playable_lookup = play


def scale_model(m: m2ir.Model, k: float):
    if k == 1.0:
        return
    vb = bytearray(m.vertices)
    for i in range(len(vb) // 48):
        x, y, z = struct.unpack_from("<3f", vb, i * 48)
        struct.pack_into("<3f", vb, i * 48, x * k, y * k, z * k)
    m.vertices = bytes(vb)
    sc = lambda v: tuple(x * k for x in v)
    for bn in m.bones:
        bn["pivot"] = sc(bn["pivot"])
        tr = bn["translation"]
        tr.keys = [[(t, struct.pack("<3f", *sc(struct.unpack("<3f", v)))) for t, v in ks] for ks in tr.keys]
    for a in m.attachments:
        a["pos"] = sc(a["pos"])
    for e in m.events:
        e["pos"] = sc(e["pos"])
    for s in m.sequences:
        s["bmin"], s["bmax"], s["radius"] = sc(s["bmin"]), sc(s["bmax"]), s["radius"] * k
    for c in m.cameras:
        c["pos_base"], c["tgt_base"] = sc(c["pos_base"]), sc(c["tgt_base"])
    for s in m.sections:
        s["center"] = sc(s["center"])
    b0, b1, br, c0, c1, cr = m.bounds
    m.bounds = (sc(b0), sc(b1), br * k, sc(c0), sc(c1), cr * k)
    cv = bytearray(m.coll_verts)
    for i in range(len(cv) // 12):
        struct.pack_into("<3f", cv, i * 12, *sc(struct.unpack_from("<3f", cv, i * 12)))
    m.coll_verts = bytes(cv)


def validate(m: m2ir.Model):
    nv, nb = len(m.vertices) // 48, len(m.bones)
    require(nb <= 256, f"{nb} bones exceed the u8 vertex bone index")
    for i in range(nv):
        w = m.vertices[i * 48 + 12:i * 48 + 16]
        bi = m.vertices[i * 48 + 16:i * 48 + 20]
        require(all(bi[k] < nb or w[k] == 0 for k in range(4)), f"vertex {i} references bone >= {nb}")
        require(abs(sum(w) - 255) <= 4 or nb == 0, f"vertex {i} weights sum {sum(w)}")
    for bn in m.bones:
        require(bn["parent"] < nb, "bone parent out of range")
    require(all(t < len(m.lookup) for t in m.tris), "triangle index escapes the skin lookup")


def cost(m: m2ir.Model, tex_bytes=0, keep=None):
    return {"vertices": len(m.vertices) // 48, "triangles": len(m.tris) // 3, "bones": len(m.bones),
            "sequences": len(keep) if keep is not None else len(m.sequences), "batches": len(m.batches),
            "texture_bytes": tex_bytes}


def lod_vertex_base(sfid, lod: int, cache: Path, n_vertices: int) -> int:
    sizes = []
    for f in sfid:
        b = read_cache(cache, f)
        n, ofs = struct.unpack_from("<II", b, 4)
        sizes.append(max(struct.unpack_from(f"<{n}H", b, ofs)) + 1)
    found = set()
    for mask in range(1 << (len(sizes) - 1)):  # bit i-1 set: skin i shares skin i-1's block
        bases, cur, total, ok = [0], 0, sizes[0], True
        for i in range(1, len(sizes)):
            if mask >> (i - 1) & 1:
                ok &= sizes[i] <= sizes[i - 1] and bases[i - 1] == cur
                bases.append(cur)
            else:
                cur = total
                bases.append(cur)
                total += sizes[i]
        if ok and total == n_vertices:
            found.add(bases[lod])
    require(len(found) == 1, f"LOD {lod}: cannot place the skin's vertex block (skin sizes {sizes}, {n_vertices} vertices, bases {sorted(found)})")
    return found.pop()


# --------------------------------------------------------------------------------------------- main

def convert(recipe: dict, cache: Path, out: Path, data_dir: Path | None, work: Path | None, profile=None, previews=True,
            game_dir: Path | None = None, pack: bool = True):
    src = recipe["source"]
    model_id = int(src["model"])
    if profile:
        fetch(cache, profile, [model_id])
    model_bytes = read_cache(cache, model_id)
    chunks = m2ir.split_chunks(model_bytes)
    sfid = struct.unpack(f"<{len(chunks['SFID']) // 4}I", chunks["SFID"])
    lod = int(src.get("lod", 0))
    require(0 <= lod < len(sfid), f"lod {lod} out of range: model has {len(sfid)} skin files {list(sfid)}")
    skin_id = int(src.get("skin") or sfid[lod])
    afid = {}
    if "AFID" in chunks:
        a = chunks["AFID"]
        afid = {struct.unpack_from("<HH", a, i * 8): struct.unpack_from("<I", a, i * 8 + 4)[0] for i in range(len(a) // 8)}
    if profile:
        fetch(cache, profile, [skin_id] + [f for f in afid.values() if f])
    anims = {k: (cache / f"{v}.bin").read_bytes() for k, v in afid.items() if v and (cache / f"{v}.bin").is_file()}
    reader = m2ir.ModernReader(model_bytes, read_cache(cache, skin_id), anims)
    m = reader.read(recipe["name"])
    # LDV1 models store each LOD's vertices back to back; a LOD skin's vertex lookup is relative to the start of its own
    # block. Some skins reuse the previous skin's block (imp2, voidwalker2), so the base comes from the one assignment of
    # skins to blocks whose block sizes add up to the model's vertex count.
    base = 0
    if lod > 0 and "LDV1" in chunks:
        if profile:
            fetch(cache, profile, [f for f in sfid if f != skin_id])
        base = lod_vertex_base(sfid, lod, cache, len(m.vertices) // 48)
        require(base + max(m.lookup) + 1 <= len(m.vertices) // 48, f"LOD {lod} vertex base {base} overruns the vertex array")
        m.lookup = [g + base for g in m.lookup]
    report = {"recipe": recipe["name"], "source": {"model_fdid": model_id, "skin_fdid": skin_id, "lod": lod, "lod_vertex_base": base, "skin_files": list(sfid), "version": reader.version,
              "anim_files": {f"{k[0]}.{k[1]}": v for k, v in afid.items()}, "anim_files_used": len(anims)}}
    report["source"]["cost"] = cost(m)
    report["source"]["geosets"] = sorted({s["id"] for s in m.sections})

    keep, aliases, seq_dropped = select_sequences(m, set(recipe.get("drop_anims", [])))
    geos = recipe.get("geosets") or default_geosets(m)
    report["prune"] = prune_geosets(m, set(geos))
    if recipe.get("decimate"):
        dc = recipe["decimate"]
        report["decimate"] = decimate.decimate(m, int(dc["target_vertices"]), float(dc.get("weight_penalty", 0.002)),
                                               float(dc.get("boundary_weight", 10.0)), int(dc.get("min_component", 0)),
                                               set(dc["sections"]) if "sections" in dc else None)
    batch_notes = rewrite_batches(m, recipe.get("env_layers", "split"), recipe.get("batch_layers"))
    if recipe.get("trim_bones"):
        report["trim_bones"] = trim_bones(m)
    internal = recipe["native_model_path"].replace("/", "\\")
    template = None
    tb = native_template(data_dir, internal, work, game_dir)
    if tb:
        template = m2ir.read_v256(tb, "template")
        report["template"] = {"path": internal, "cost": cost(template, keep=None), "sha256": sha256(tb)}
    build_lookups(m, keep, aliases, template, load_animation_fallbacks(data_dir, work, game_dir))
    scale_model(m, float(recipe.get("scale", 1.0)))

    # textures
    if recipe.get("drop_unused_textures") or any(t["type"] > NATIVE_MAX_TEX_TYPE for t in m.textures):
        report["textures_removed"] = drop_unused_textures(m)
    base_dir = internal.rsplit("\\", 1)[0]
    payload_tex = {}
    tex_cfg = recipe.get("textures", {})
    for slot, t in enumerate(m.textures):
        cfg = tex_cfg.get(str(t.get("recipe_slot", slot)), {})
        if cfg.get("replaceable"):
            for native_path, fd in cfg.get("skins", {}).items():
                payload_tex[native_path.replace("/", "\\")] = int(fd)
            t["path"] = ""
        elif cfg.get("fdid") or t["fdid"]:
            fd = int(cfg.get("fdid") or t["fdid"])
            path = (cfg.get("path") or f"{base_dir}\\{recipe['name']}_tex{slot}.blp").replace("/", "\\")
            t.update(type=0, path=path)
            payload_tex[path] = fd
        else:
            require(t["type"] != 0, f"texture slot {slot}: type 0 without FileDataID")
            t["path"] = ""
        t["flags"] &= 0x3
    # replaceable lookup: keep only types still used
    used_types = {t["type"] for t in m.textures}
    m.replaceable_lookup = [(v if 0 <= v < len(m.textures) and m.textures[v]["type"] == typ else -1)
                            for typ, v in enumerate(m.replaceable_lookup)] if m.replaceable_lookup else []
    if profile:
        fetch(cache, profile, list(payload_tex.values()))
    validate(m)
    md20 = m2ir.write_v256(m, keep, aliases, recipe["name"])
    back = m2ir.read_v256(md20, "readback")
    require(len(back.vertices) == len(m.vertices) and len(back.bones) == len(m.bones) and len(back.sequences) == len(keep),
            "readback counts differ")

    require(not out.exists() or (game_dir is not None and not any(out.iterdir())), f"refusing to overwrite {out}")
    payload = out / "payload"
    tex_bytes = 0
    blps = {}
    for path, fd in payload_tex.items():
        blp = read_cache(cache, fd)
        require(blp[:4] == b"BLP2", f"texture {fd} is not BLP2")
        p = payload / Path(path.replace("\\", "/"))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(blp)
        tex_bytes += len(blp)
        blps[path] = fd
    mp = payload / Path(internal.replace("\\", "/"))
    mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_bytes(md20)
    archive = out / f"{recipe['name']}.mpq"
    if pack:
        r = subprocess.run([sys.executable, "-I", str(PACKER), str(payload), str(archive)], capture_output=True, text=True,
                           timeout=300)
        (out / "pack-mpq.txt").write_text(r.stdout + r.stderr, encoding="utf8")
        require(r.returncode == 0, f"pack-mpq failed: {r.stderr[-300:]}")

    report["output"] = {"model_path": internal, "model_bytes": len(md20), "model_sha256": sha256(md20),
                        "textures": blps, "mpq": archive.name if pack else None,
                        "mpq_bytes": archive.stat().st_size if pack else None,
                        "cost": cost(m, tex_bytes, keep)}
    report["sequences"] = [{"out": oi, "anim": m.sequences[i]["id"], "variation": m.sequences[i]["variation"],
                            "duration": m.sequences[i]["duration"], "alias_of": aliases[oi]} for oi, i in enumerate(keep)]
    report["dropped"] = m.dropped + seq_dropped + batch_notes + [
        "modern shader combiners (shader id -> 0, one texture per batch)", "camera spline animation (static camera kept)",
        f"key bones >= {m2ir.NATIVE_KEY_BONES} cleared from the key-bone lookup"]
    if template:
        tc = report["template"]["cost"]
        oc = report["output"]["cost"]
        report["cost_ratio_vs_native"] = {k: round(oc[k] / tc[k], 2) for k in ("vertices", "triangles", "bones") if tc.get(k)}
    report["installed"] = False
    (out / "conversion-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf8")
    return report, m, back, keep


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("recipe", type=Path)
    ap.add_argument("--cache", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True, help="NEW output directory")
    ap.add_argument("--data", type=Path, default=ROOT / "Data", help="native Data dir (read-only: template + AnimationData)")
    ap.add_argument("--work", type=Path, default=ROOT / "analysis" / "skinned-converter" / "work")
    ap.add_argument("--fetch", choices=("forever", "retail"))
    a = ap.parse_args()
    recipe = json.loads(a.recipe.read_text(encoding="utf8"))
    report, *_ = convert(recipe, a.cache, a.out_dir.resolve(), a.data, a.work, a.fetch)
    print(json.dumps({"status": "success", "out": str(a.out_dir), "cost": report["output"]["cost"],
                      "ratio": report.get("cost_ratio_vs_native"), "sha256": report["output"]["model_sha256"]}, indent=1))


if __name__ == "__main__":
    try:
        main()
    except m2ir.ConvertError as exc:
        print(f"skinned_convert: {exc}", file=sys.stderr)
        raise SystemExit(1)
