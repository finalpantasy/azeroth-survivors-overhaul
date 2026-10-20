"""Mesh decimation for skinned models: quadric-error half-edge collapse (pure Python, deterministic).

A vertex is only ever moved onto one of its existing neighbours, so every surviving vertex keeps its own position,
normal, UVs and bone weights: skinning stays valid without re-weighting. Vertices that share a position (UV seams,
geoset borders) form one group and collapse together onto a single neighbouring group, so seams never crack.
Open edges (holes, seams) carry extra perpendicular planes so outlines keep their shape, and a collapse between
vertices with different bone weights pays a penalty, so joints keep their vertices. Collapses that fold a
triangle over or break the mesh's local topology (link condition) are refused.
"""
from __future__ import annotations

import heapq
import math
import struct

import m2ir
from m2ir import require


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _plane_q(n, d, w):
    a, b, c = n
    return [w * x for x in (a * a, a * b, a * c, a * d, b * b, b * c, b * d, c * c, c * d, d * d)]


def _q_eval(q, p):
    x, y, z = p
    return (q[0] * x * x + 2 * q[1] * x * y + 2 * q[2] * x * z + 2 * q[3] * x + q[4] * y * y + 2 * q[5] * y * z
            + 2 * q[6] * y + q[7] * z * z + 2 * q[8] * z + q[9])


def _q_add(q, r):
    for i in range(10):
        q[i] += r[i]


def decimate(m: m2ir.Model, target_vertices: int, weight_penalty: float = 0.002, boundary_weight: float = 10.0):
    """Reduce the skin to at most target_vertices distinct vertices (in place). Returns a report dict."""
    n = len(m.lookup)
    pos, wts = [], []
    for g in m.lookup:
        o = g * 48
        pos.append(struct.unpack_from("<3f", m.vertices, o))
        w = m.vertices[o + 12:o + 16]
        b = m.vertices[o + 16:o + 20]
        d = {}
        for k in range(4):
            if w[k]:
                d[b[k]] = d.get(b[k], 0) + w[k] / 255.0
        wts.append(d)
    sec_of = [0] * n
    tri_sec = []
    tris = []
    for si, s in enumerate(m.sections):
        for i in range(s["vstart"], s["vstart"] + s["vcount"]):
            sec_of[i] = si
        for t in range(s["istart"], s["istart"] + s["icount"], 3):
            tris.append(list(m.tris[t:t + 3]))
            tri_sec.append(si)
    alive_t = [True] * len(tris)
    vtris = [set() for _ in range(n)]
    for ti, t in enumerate(tris):
        if len(set(t)) < 3:
            alive_t[ti] = False
            continue
        for v in t:
            vtris[v].add(ti)

    lo = [min(p[k] for p in pos) for k in range(3)]
    hi = [max(p[k] for p in pos) for k in range(3)]
    diag2 = sum((hi[k] - lo[k]) ** 2 for k in range(3)) or 1.0
    eps = math.sqrt(diag2) * 1e-5

    # groups of coincident vertices
    key_of = {}
    gid = [0] * n
    for i, p in enumerate(pos):
        k = tuple(round(c / eps) for c in p)
        gid[i] = key_of.setdefault(k, len(key_of))
    ng = len(key_of)
    members = [[] for _ in range(ng)]
    for i in range(n):
        members[gid[i]].append(i)

    # quadrics (area weights normalised by the mean area, so costs are in length^2)
    areas = []
    for ti, t in enumerate(tris):
        if alive_t[ti]:
            nrm = _cross(_sub(pos[t[1]], pos[t[0]]), _sub(pos[t[2]], pos[t[0]]))
            areas.append(math.sqrt(_dot(nrm, nrm)) / 2)
    mean_area = (sum(areas) / len(areas)) if areas else 1.0
    gq = [[0.0] * 10 for _ in range(ng)]
    edge_count = {}
    for ti, t in enumerate(tris):
        if not alive_t[ti]:
            continue
        for k in range(3):
            e = (min(t[k], t[(k + 1) % 3]), max(t[k], t[(k + 1) % 3]))
            edge_count[e] = edge_count.get(e, 0) + 1
    for ti, t in enumerate(tris):
        if not alive_t[ti]:
            continue
        p0, p1, p2 = pos[t[0]], pos[t[1]], pos[t[2]]
        nrm = _cross(_sub(p1, p0), _sub(p2, p0))
        ln = math.sqrt(_dot(nrm, nrm))
        if ln < 1e-20:
            continue
        nu = (nrm[0] / ln, nrm[1] / ln, nrm[2] / ln)
        q = _plane_q(nu, -_dot(nu, p0), (ln / 2) / mean_area)
        for v in t:
            _q_add(gq[gid[v]], q)
        for k in range(3):
            a, b = t[k], t[(k + 1) % 3]
            if edge_count[(min(a, b), max(a, b))] == 1:
                ed = _sub(pos[b], pos[a])
                bn = _cross(ed, nu)
                bl = math.sqrt(_dot(bn, bn))
                if bl > 1e-20:
                    bn = (bn[0] / bl, bn[1] / bl, bn[2] / bl)
                    bq = _plane_q(bn, -_dot(bn, pos[a]), boundary_weight)
                    _q_add(gq[gid[a]], bq)
                    _q_add(gq[gid[b]], bq)

    alive_v = [True] * n

    def nbrs(v):
        out = set()
        for ti in vtris[v]:
            out.update(tris[ti])
        out.discard(v)
        return out

    def wdiff(a, b):
        wa, wb = wts[a], wts[b]
        return sum(abs(wa.get(k, 0) - wb.get(k, 0)) for k in set(wa) | set(wb))

    def local_ok(a, b):
        na, nb = nbrs(a), nbrs(b)
        shared = [ti for ti in vtris[a] if b in tris[ti]]
        if not shared or len(na & nb) != len(shared):
            return False
        pb = pos[b]
        for ti in vtris[a]:
            t = tris[ti]
            if b in t:
                continue
            ps = [pos[v] for v in t]
            old = _cross(_sub(ps[1], ps[0]), _sub(ps[2], ps[0]))
            ps = [pb if v == a else pos[v] for v in t]
            new = _cross(_sub(ps[1], ps[0]), _sub(ps[2], ps[0]))
            lo_, ln_ = math.sqrt(_dot(old, old)), math.sqrt(_dot(new, new))
            if ln_ < 1e-20 or (lo_ > 1e-20 and _dot(old, new) < 0.2 * lo_ * ln_):
                return False
        return True

    def plan(A, B):
        """Per member of A, the neighbour in B it collapses onto; None if some member has none."""
        bset = set(members[B])
        pairs = []
        for a in members[A]:
            cand = sorted(nbrs(a) & bset)
            if not cand:
                return None
            pairs.append((a, cand[0]))
        return pairs

    def best(A):
        if not members[A]:
            return None
        ngr = sorted({gid[v] for a in members[A] for v in nbrs(a)} - {A})
        top = None
        for B in ngr:
            pairs = plan(A, B)
            if pairs is None:
                continue
            c = _q_eval(gq[A], pos[members[B][0]]) + weight_penalty * diag2 * max(wdiff(a, b) for a, b in pairs)
            if top is None or c < top[0]:
                top = (c, B)
        return top

    version = [0] * ng
    heap = []

    def push(A):
        version[A] += 1
        r = best(A)
        if r is not None:
            heapq.heappush(heap, (r[0], A, r[1], version[A]))

    for A in range(ng):
        push(A)

    def distinct():
        return len({m.lookup[i] for i in range(n) if alive_v[i]})

    count = distinct()
    start = count
    collapses = 0
    worst = 0.0
    while count > target_vertices and heap:
        c, A, B, ver = heapq.heappop(heap)
        if ver != version[A] or not members[A] or not members[B]:
            continue
        pairs = plan(A, B)
        if pairs is None or not all(local_ok(a, b) for a, b in pairs):
            # refuse this target; try A's other targets later by lowering its priority
            version[A] += 1
            r = None
            ngr = sorted({gid[v] for a in members[A] for v in nbrs(a)} - {A, B})
            for B2 in ngr:
                p2 = plan(A, B2)
                if p2 is None or not all(local_ok(a, b) for a, b in p2):
                    continue
                c2 = _q_eval(gq[A], pos[members[B2][0]]) + weight_penalty * diag2 * max(wdiff(a, b) for a, b in p2)
                if r is None or c2 < r[0]:
                    r = (c2, B2)
            if r is not None:
                heapq.heappush(heap, (r[0], A, r[1], version[A]))
            continue
        touched = set()
        for a, b in pairs:
            for ti in list(vtris[a]):
                t = tris[ti]
                touched.update(t)
                if b in t:
                    alive_t[ti] = False
                    for v in t:
                        vtris[v].discard(ti)
                else:
                    t[t.index(a)] = b
                    vtris[b].add(ti)
            vtris[a].clear()
            alive_v[a] = False
        _q_add(gq[B], gq[A])
        members[A] = []
        collapses += 1
        worst = max(worst, c)
        count = distinct()
        for G in sorted({gid[v] for v in touched if alive_v[v]} | {B}):
            push(G)

    # rebuild sections, skin lookup, props, triangles; then compact global vertices
    new_lookup, new_props, new_tris, new_secs = [], bytearray(), [], []
    for si, s in enumerate(m.sections):
        st = [tris[ti] for ti in range(len(tris)) if alive_t[ti] and tri_sec[ti] == si]
        used = sorted({v for t in st for v in t})
        if not st:
            used = []
        v0, i0 = len(new_lookup), len(new_tris)
        lmap = {v: v0 + k for k, v in enumerate(used)}
        new_lookup += [m.lookup[v] for v in used]
        for v in used:
            new_props += m.props[4 * v:4 * v + 4]
        for t in st:
            new_tris += [lmap[v] for v in t]
        s = dict(s)
        s.update(vstart=v0, vcount=len(used), istart=i0, icount=len(new_tris) - i0)
        new_secs.append(s)
    used_g = sorted(set(new_lookup))
    gmap = {g: i for i, g in enumerate(used_g)}
    m.vertices = b"".join(m.vertices[g * 48:(g + 1) * 48] for g in used_g)
    m.lookup = [gmap[g] for g in new_lookup]
    m.props, m.tris, m.sections = bytes(new_props), new_tris, new_secs
    require(len(m.tris) <= 65535, "decimation produced too many indices")
    return {"target_vertices": target_vertices, "vertices_before": start, "vertices_after": len(used_g),
            "triangles_after": len(new_tris) // 3, "collapses": collapses,
            "worst_cost_rel_diag": round(math.sqrt(max(worst, 0) / diag2), 5),
            "weight_penalty": weight_penalty, "boundary_weight": boundary_weight}
