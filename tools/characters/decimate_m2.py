#!/usr/bin/env python3
"""Skinning-safe triangle decimation of a vanilla (v256) character M2.

Half-edge collapse driven by area-weighted quadric error. Vertices are never moved or created: a collapse
redirects one vertex's triangles to a neighbour, so UVs, normals and bone weights of the surviving vertices stay
exactly as authored. Seam/border vertices (edges used by one triangle in the submesh) are locked, flips and
UV/bone changes are rejected, and every collapse is bounded by --max-err (RMS distance, model units).

Only view 0 (the one the engine renders) changes: its triangle index list and the submesh tstart/tcount fields;
the new list is written into the old triangle array (always shorter), so no offsets move.

  decimate_m2.py <in.m2> <out.m2> [--ratio R] [--max-err E] [--keep-hair]
"""
import argparse
import heapq
import struct
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from m2lib import M2  # noqa: E402

HAIR_IDS = range(1, 11)          # group-0 hair styles in these models (ids 1..10)
SKIP_GROUPS = {15}               # cloak: alpha cloth, leave alone


def group(gid):
    return gid // 100


def decimate_submesh(P, U, B, tris, target, max_err, uv_w=4.0):
    """P,U,B: per local vertex position(n,3), uv(n,2), bone key(n,8 as 4 idx + 4 w). tris: (m,3) int. Returns new tris."""
    n = len(P)
    tris = [tuple(t) for t in tris if len(set(t)) == 3]
    alive = [True] * len(tris)
    vt = [set() for _ in range(n)]
    for i, t in enumerate(tris):
        for v in t:
            vt[v].add(i)
    # boundary / seam vertices
    ecount = {}
    for t in tris:
        for a, b in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0])):
            k = (a, b) if a < b else (b, a)
            ecount[k] = ecount.get(k, 0) + 1
    locked = np.zeros(n, bool)
    for (a, b), c in ecount.items():
        if c != 2:
            locked[a] = locked[b] = True
    # also lock vertices sharing a position with another local vertex (normal/uv split seams)
    pos_key = {}
    for v in range(n):
        if vt[v]:
            pos_key.setdefault(tuple(np.round(P[v], 4)), []).append(v)
    for vs in pos_key.values():
        if len(vs) > 1:
            for v in vs:
                locked[v] = True

    Q = np.zeros((n, 4, 4))
    area = np.zeros(n)
    for t in tris:
        p0, p1, p2 = P[t[0]], P[t[1]], P[t[2]]
        c = np.cross(p1 - p0, p2 - p0)
        a2 = np.linalg.norm(c)
        if a2 < 1e-12:
            continue
        nn = c / a2
        pl = np.append(nn, -nn @ p0)
        K = np.outer(pl, pl) * (a2 / 2)
        for v in t:
            Q[v] += K
            area[v] += a2 / 2

    def tri_normal(t, over=None):
        a, b, c = (over.get(v, v) if over else v for v in t)
        return np.cross(P[b] - P[a], P[c] - P[a])

    def cost(a, b):
        """cost of collapsing a into b (b survives, stays at P[b])."""
        if locked[a]:
            return None
        ar = area[a] + area[b]
        if ar <= 0:
            return None
        p = np.append(P[b], 1.0)
        e = p @ (Q[a] + Q[b]) @ p / ar
        e = max(e, 0.0)
        # texture / skin preservation
        du = np.linalg.norm(U[a] - U[b])
        if B[a][:4].tolist() != B[b][:4].tolist():
            if not (B[a][4:].argmax() == B[b][4:].argmax() and B[a][:4][B[a][4:].argmax()] == B[b][:4][B[b][4:].argmax()]):
                return None
        dw = np.abs(B[a][4:] - B[b][4:]).sum()
        return e + (uv_w * du) ** 2 * 1e-3 + dw * 1e-6

    def neighbors(v):
        s = set()
        for ti in vt[v]:
            for u in tris[ti]:
                if u != v:
                    s.add(u)
        return s

    ver = [0] * n
    heap = []

    def push(a, b):
        c = cost(a, b)
        if c is not None:
            heapq.heappush(heap, (c, a, b, ver[a], ver[b]))

    for v in range(n):
        if vt[v]:
            for u in neighbors(v):
                push(v, u)
    cur = sum(alive)
    max_e2 = max_err * max_err
    achieved = 0.0
    while heap and cur > target:
        c, a, b, va, vb = heapq.heappop(heap)
        if ver[a] != va or ver[b] != vb or not vt[a] or not vt[b]:
            continue
        if c > max_e2:
            break
        shared = vt[a] & vt[b]
        if len(shared) != 2:               # only manifold interior edges
            continue
        # link condition: common neighbours of a,b must be exactly the two opposite vertices
        na, nb = neighbors(a), neighbors(b)
        opp = set()
        for ti in shared:
            opp |= set(tris[ti]) - {a, b}
        if (na & nb) != opp:
            continue
        # flip / uv-flip / degenerate test on the surviving ring
        ok = True
        for ti in vt[a] - shared:
            t = tris[ti]
            n0 = tri_normal(t)
            n1 = tri_normal(t, {a: b})
            l0, l1 = np.linalg.norm(n0), np.linalg.norm(n1)
            if l1 < 1e-10 or l0 < 1e-10 or (n0 @ n1) / (l0 * l1) < 0.4:
                ok = False
                break
            uvs0 = [U[v] for v in t]
            uvs1 = [U[b if v == a else v] for v in t]
            s0 = (uvs0[1][0] - uvs0[0][0]) * (uvs0[2][1] - uvs0[0][1]) - (uvs0[1][1] - uvs0[0][1]) * (uvs0[2][0] - uvs0[0][0])
            s1 = (uvs1[1][0] - uvs1[0][0]) * (uvs1[2][1] - uvs1[0][1]) - (uvs1[1][1] - uvs1[0][1]) * (uvs1[2][0] - uvs1[0][0])
            if s0 * s1 < 0:
                ok = False
                break
        if not ok:
            continue
        # apply
        for ti in shared:
            alive[ti] = False
            for v in tris[ti]:
                vt[v].discard(ti)
            cur -= 1
        for ti in list(vt[a]):
            t = tris[ti]
            tris[ti] = tuple(b if v == a else v for v in t)
            vt[b].add(ti)
        vt[a] = set()
        Q[b] = Q[b] + Q[a]
        area[b] += area[a]
        achieved = max(achieved, c)
        ver[a] += 1
        ver[b] += 1
        for u in neighbors(b):
            ver[u] += 0
            push(u, b)
            push(b, u)
    out = [t for t, al in zip(tris, alive) if al]
    return out, achieved ** 0.5


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--ratio", type=float, default=0.5, help="target fraction of triangles kept per decimated submesh")
    ap.add_argument("--max-err", type=float, default=0.01, help="RMS collapse error cap (model units)")
    ap.add_argument("--hair-ratio", type=float, default=1.0, help="ratio for hair styles (1.0 = untouched)")
    ap.add_argument("--min-tris", type=int, default=120, help="leave submeshes below this alone")
    a = ap.parse_args()
    raw = bytearray(Path(a.src).read_bytes())
    m = M2(bytes(raw))
    nv, ov = m.arr(0x44)
    nviews, oviews = m.arr(0x4C)
    v0 = m.view(0)
    idx = np.frombuffer(bytes(raw[v0["ofs_indices"]: v0["ofs_indices"] + 2 * v0["n_indices"]]), "<u2")
    tri0 = np.frombuffer(bytes(raw[v0["ofs_tri"]: v0["ofs_tri"] + 2 * v0["n_tri_indices"]]), "<u2")
    used = int(idx[tri0].max()) + 1
    verts = np.frombuffer(bytes(raw[ov: ov + 48 * used]), dtype=np.dtype([
        ("pos", "<f4", 3), ("w", "u1", 4), ("bi", "u1", 4), ("n", "<f4", 3), ("uv", "<f4", 2), ("uv2", "<f4", 2)]))
    subs = m.submeshes(0)
    new_tri = []
    new_sub = []
    before = after = 0
    worst = 0.0
    for s in subs:
        t = tri0[s["tstart"]: s["tstart"] + s["tcount"]].reshape(-1, 3).astype(int)
        gid = s["id"]
        ratio = a.ratio
        if gid in HAIR_IDS:
            ratio = a.hair_ratio
        elif group(gid) in SKIP_GROUPS or len(t) < a.min_tris:
            ratio = 1.0
        if ratio >= 0.999:
            nt = [tuple(x) for x in t]
        else:
            loc = np.unique(t)
            remap = {int(v): i for i, v in enumerate(loc)}
            vv = verts[idx[loc]]
            P = vv["pos"].astype(float)
            U = vv["uv"].astype(float)
            B = np.concatenate([vv["bi"].astype(float), vv["w"].astype(float) / 255.0], axis=1)
            tt = np.vectorize(remap.get)(t)
            res, err = decimate_submesh(P, U, B, tt, int(len(t) * ratio), a.max_err)
            worst = max(worst, err)
            nt = [tuple(int(loc[v]) for v in x) for x in res]
        before += len(t)
        after += len(nt)
        new_sub.append((len(new_tri) * 3, len(nt) * 3))
        new_tri.extend(nt)
    flat = np.array(new_tri, "<u2").reshape(-1)
    # only view 0 is rendered by the engine (views 1-3 have different submesh tables and stay untouched)
    vw = v0
    if vw["ofs_tri"] + len(flat) * 2 > len(raw):
        raise SystemExit("triangle array would overflow file")
    raw[vw["ofs_tri"]: vw["ofs_tri"] + 2 * vw["n_tri_indices"]] = bytes(2 * vw["n_tri_indices"])
    raw[vw["ofs_tri"]: vw["ofs_tri"] + len(flat) * 2] = flat.tobytes()
    struct.pack_into("<I", raw, oviews + 8, len(flat))          # n_tri_indices
    for i, (ts, tc) in enumerate(new_sub):
        struct.pack_into("<HH", raw, vw["ofs_sub"] + i * 32 + 8, ts, tc)   # tstart, tcount
    Path(a.dst).write_bytes(bytes(raw))
    print(f"{Path(a.src).name}: tris {before} -> {after} ({after / before:.2f}), worst collapse rms err {worst:.4f}")


if __name__ == "__main__":
    main()
