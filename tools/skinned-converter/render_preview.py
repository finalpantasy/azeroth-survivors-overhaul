#!/usr/bin/env python3
"""Offline skinned-M2 preview: evaluates the skeleton of an IR model (m2ir.Model) at (sequence, time), skins the
vertices on the CPU and rasterizes a textured, lit orthographic front + side view with a z-buffer (numpy).

It is a check tool, not the game: it proves the converted bytes animate sanely (no exploding vertices, plausible pose,
orientation and scale) before a native lab run. The native lab run is still the acceptance test.
"""
from __future__ import annotations

import math
import struct

import numpy as np
from PIL import Image, ImageDraw


def _lerp(a, b, f):
    return tuple(x + (y - x) * f for x, y in zip(a, b))


def sample(track, seq_index, t, fmt, default, gseq_durations):
    if track is None:
        return default
    if track.gseq >= 0:
        keys = track.keys[0] if track.keys else []
        dur = gseq_durations[track.gseq] if track.gseq < len(gseq_durations) else 0
        if dur:
            t = t % dur
    else:
        keys = track.keys_of(seq_index)
    if not keys:
        return default
    vals = [struct.unpack(fmt, v) for _, v in keys]
    times = [k for k, _ in keys]
    if t <= times[0] or len(keys) == 1:
        return vals[0]
    for i in range(1, len(times)):
        if t <= times[i]:
            span = times[i] - times[i - 1]
            f = (t - times[i - 1]) / span if span else 0
            if track.interp == 0:
                return vals[i - 1]
            if len(vals[0]) == 4:
                a, b = vals[i - 1], vals[i]
                if sum(x * y for x, y in zip(a, b)) < 0:
                    b = tuple(-x for x in b)
                q = _lerp(a, b, f)
                n = math.sqrt(sum(x * x for x in q)) or 1
                return tuple(x / n for x in q)
            return _lerp(vals[i - 1], vals[i], f)
    return vals[-1]


def quat_mat(q):
    x, y, z, w = q
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def bone_matrices(model, seq_index, t):
    gs = model.global_sequences
    n = len(model.bones)
    out = [None] * n

    def world(i):
        if out[i] is not None:
            return out[i]
        bn = model.bones[i]
        p = np.array(bn["pivot"])
        tr = np.array(sample(bn["translation"], seq_index, t, "<3f", (0, 0, 0), gs))
        q = sample(bn["rotation"], seq_index, t, "<4f", (0, 0, 0, 1), gs)
        s = np.array(sample(bn["scale"], seq_index, t, "<3f", (1, 1, 1), gs))
        m = np.eye(4)
        m[:3, :3] = quat_mat(q) * s[None, :]
        m[:3, 3] = p + tr - m[:3, :3] @ p
        par = bn["parent"]
        out[i] = (world(par) @ m) if 0 <= par < n and par != i else m
        return out[i]

    for i in range(n):
        world(i)
    return np.stack(out) if n else np.eye(4)[None]


def skinned_positions(model, seq_index, t):
    nv = len(model.vertices) // 48
    raw = np.frombuffer(model.vertices, dtype=np.uint8).reshape(nv, 48)
    pos = raw[:, 0:12].copy().view("<f4").reshape(nv, 3).astype(np.float64)
    nrm = raw[:, 20:32].copy().view("<f4").reshape(nv, 3).astype(np.float64)
    uv = raw[:, 32:40].copy().view("<f4").reshape(nv, 2)
    w = raw[:, 12:16].astype(np.float64) / 255.0
    bi = raw[:, 16:20].astype(np.int64)
    if not model.bones:
        return pos, nrm, uv
    mats = bone_matrices(model, seq_index, t)
    bi = np.clip(bi, 0, len(mats) - 1)
    hom = np.concatenate([pos, np.ones((nv, 1))], 1)
    acc = np.zeros((nv, 3))
    nacc = np.zeros((nv, 3))
    for k in range(4):
        m = mats[bi[:, k]]
        acc += w[:, k:k + 1] * np.einsum("nij,nj->ni", m[:, :3, :], hom)
        nacc += w[:, k:k + 1] * np.einsum("nij,nj->ni", m[:, :3, :3], nrm)
    return acc, nacc, uv


def batch_alphas(model, seq_index, t):
    """Per-batch opacity from the batch's color alpha and transparency tracks (the engine hides alpha-0 batches)."""
    gs = model.global_sequences
    out = []
    for b in model.batches:
        a = 1.0
        if b[4] < len(model.colors):
            a *= sample(model.colors[b[4]][1], seq_index, t, "<h", (32767,), gs)[0] / 32767
        tl = model.transparency_lookup
        if b[10] < len(tl) and 0 <= tl[b[10]] < len(model.transparency):
            a *= sample(model.transparency[tl[b[10]]], seq_index, t, "<h", (32767,), gs)[0] / 32767
        out.append(max(0.0, min(1.0, a)))
    return out


def raster(model, pos, uv, textures, view, size, box, bg=(40, 44, 52), alphas=None):
    """view: 'front' looks down -X (model faces +X), 'side' looks down +Y. textures: batch index -> RGBA ndarray or None."""
    W = H = size
    img = np.zeros((H, W, 3), np.float64)
    img[:] = bg
    zb = np.full((H, W), -1e9)
    if view == "front":
        u, v, d = -pos[:, 1], pos[:, 2], pos[:, 0]
    else:
        u, v, d = pos[:, 0], pos[:, 2], -pos[:, 1]
    (u0, u1), (v0, v1) = box
    sc = (W - 20) / max(u1 - u0, v1 - v0)
    px = (u - (u0 + u1) / 2) * sc + W / 2
    py = H - ((v - v0) * sc + 10)
    light = np.array([0.6, 0.5, 0.62])
    light /= np.linalg.norm(light)
    raw = np.frombuffer(model.vertices, dtype=np.uint8).reshape(-1, 48)
    uv1 = raw[:, 40:48].copy().view("<f4").reshape(-1, 2)
    tul = list(getattr(model, "texture_unit_lookup", []) or [0])
    blends = [model.materials[b[5]][1] if b[5] < len(model.materials) else 0 for b in model.batches]
    # opaque / alpha-key first, then the blended layers in file order without depth writes
    order = [i for i in range(len(model.batches)) if blends[i] < 2] + [i for i in range(len(model.batches)) if blends[i] >= 2]
    for bi_ in order:
        if alphas is not None and alphas[bi_] < 0.02:
            continue
        b = model.batches[bi_]
        sec = model.sections[b[2]]
        tex = textures.get(bi_)
        blend = blends[bi_]
        unit = tul[b[9]] if b[9] < len(tul) else -1
        buv = uv1 if unit == 1 else uv
        tri_idx = np.array(model.tris[sec["istart"]:sec["istart"] + sec["icount"]], dtype=np.int64).reshape(-1, 3)
        gidx = np.array(model.lookup, dtype=np.int64)[tri_idx]
        for t in gidx:
            x, y, z = px[t], py[t], d[t]
            xmin, xmax = int(max(0, math.floor(x.min()))), int(min(W - 1, math.ceil(x.max())))
            ymin, ymax = int(max(0, math.floor(y.min()))), int(min(H - 1, math.ceil(y.max())))
            if xmin > xmax or ymin > ymax:
                continue
            den = (y[1] - y[2]) * (x[0] - x[2]) + (x[2] - x[1]) * (y[0] - y[2])
            if abs(den) < 1e-9:
                continue
            gx, gy = np.meshgrid(np.arange(xmin, xmax + 1) + 0.5, np.arange(ymin, ymax + 1) + 0.5)
            a = ((y[1] - y[2]) * (gx - x[2]) + (x[2] - x[1]) * (gy - y[2])) / den
            bb = ((y[2] - y[0]) * (gx - x[2]) + (x[0] - x[2]) * (gy - y[2])) / den
            c = 1 - a - bb
            inside = (a >= 0) & (bb >= 0) & (c >= 0)
            if not inside.any():
                continue
            zz = a * z[0] + bb * z[1] + c * z[2]
            sub = zb[ymin:ymax + 1, xmin:xmax + 1]
            m = inside & (zz > sub)
            if not m.any():
                continue
            p3 = pos[t]
            fn = np.cross(p3[1] - p3[0], p3[2] - p3[0])
            nn = np.linalg.norm(fn)
            shade = 0.45 + 0.55 * abs(fn @ light) / nn if nn > 0 else 0.7
            if tex is not None:
                th, tw_ = tex.shape[:2]
                uu = a * buv[t[0], 0] + bb * buv[t[1], 0] + c * buv[t[2], 0]
                vv = a * buv[t[0], 1] + bb * buv[t[1], 1] + c * buv[t[2], 1]
                tx = (np.mod(uu, 1.0) * tw_).astype(int).clip(0, tw_ - 1)
                ty = (np.mod(vv, 1.0) * th).astype(int).clip(0, th - 1)
                col = tex[ty, tx]
                if blend == 1:
                    m = m & (col[..., 3] >= 128)
                rgb = col[..., :3] * shade
            else:
                rgb = np.ones(m.shape + (3,)) * 200 * shade
            reg = img[ymin:ymax + 1, xmin:xmax + 1]
            if blend < 2 or tex is None:
                sub[m] = zz[m]
                reg[m] = rgb[m]
                continue
            al = col[..., 3:4] / 255.0 * (alphas[bi_] if alphas is not None else 1.0)
            if blend == 2:  # alpha
                reg[m] = (rgb * al + reg * (1 - al))[m]
            elif blend in (3, 4):  # additive (4: alpha-weighted)
                reg[m] = (reg + rgb * (al if blend == 4 else 1.0))[m]
            else:  # modulate / modulate 2x
                reg[m] = (reg * col[..., :3] / 255.0 * (2.0 if blend == 6 else 1.0))[m]
    return Image.fromarray(img.clip(0, 255).astype(np.uint8))


def contact_sheet(model, frames, textures, out_path, size=300, title=""):
    """frames: list of (label, seq_index, t_ms). Common box from all frames so scale is comparable."""
    posed = []
    for label, si, t in frames:
        p, _, uv = skinned_positions(model, si, t)
        al = batch_alphas(model, si, t)
        vis = sorted({model.lookup[j] for bi_, b in enumerate(model.batches) if al[bi_] >= 0.02
                      for j in range(model.sections[b[2]]["vstart"], model.sections[b[2]]["vstart"] + model.sections[b[2]]["vcount"])})
        posed.append((label, p, uv, al, vis))
    allp = np.concatenate([p[vis] if vis else p for _, p, _, _, vis in posed])
    lim = np.percentile(np.abs(allp), 99.9)
    ext = max(float(np.abs(allp[:, :]).max()), 1e-3)
    box_f = ((-allp[:, 1].max(), -allp[:, 1].min()), (allp[:, 2].min(), allp[:, 2].max()))
    box_s = ((allp[:, 0].min(), allp[:, 0].max()), (allp[:, 2].min(), allp[:, 2].max()))
    cols = len(posed)
    sheet = Image.new("RGB", (cols * size, 2 * size + 18), (20, 20, 24))
    dr = ImageDraw.Draw(sheet)
    for i, (label, p, uv, al, _) in enumerate(posed):
        sheet.paste(raster(model, p, uv, textures, "front", size, box_f, alphas=al), (i * size, 18))
        sheet.paste(raster(model, p, uv, textures, "side", size, box_s, alphas=al), (i * size, 18 + size))
        dr.text((i * size + 4, 3), label, fill=(230, 230, 230))
    sheet.save(out_path)
    return {"max_abs_coord": ext, "p99.9_abs": float(lim)}
