#!/usr/bin/env python3
"""Drawn-triangle estimate per model: submesh ids are geoset ids (group = id//100, variant = id%100).
default look = body (id 0) + hair variant 1 (id 1.. smallest) + variant 01 of each group 1xx..; max look = per group the largest variant.
Compares native vs HD. Output analysis/characters/drawn-cost.md"""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from m2lib import M2
R = Path(__file__).resolve().parents[2]
rep = json.load(open(R / "analysis/characters/build-report.json"))
def tri(m):
    subs = m.submeshes()
    by = {}
    for s in subs:
        by.setdefault(s["id"], 0); by[s["id"]] += s["tcount"] // 3
    return by
def look(by):
    body = by.get(0, 0)
    hair = sorted(v for k, v in by.items() if 0 < k < 100)
    dflt = body + (by.get(1, 0)) + sum(v for k, v in by.items() if k >= 100 and k % 100 == 1)
    groups = {}
    for k, v in by.items():
        if k >= 100:
            groups[k // 100] = max(groups.get(k // 100, 0), v)
    mx = body + (max(hair) if hair else 0) + sum(groups.values())
    return dflt, mx
rows = []
for p in rep["models"]:
    base = p["native_path"].rsplit(chr(92), 1)[1].rsplit(".", 1)[0]
    n = tri(M2((R / f"analysis/characters/native-m2/{base}.m2").read_bytes()))
    h = tri(M2((R / "mod-source/characters-hd/payload" / Path(*p["private_path"].split(chr(92)))).read_bytes()))
    nd, nm = look(n); hd, hm = look(h)
    rows.append((p["race"], p["sex"], sum(n.values()), sum(h.values()), nd, hd, nm, hm, n.get(0, 0), h.get(0, 0)))
L = ["# Drawn triangles per model (default look / max look) native vs HD", "",
     "| model | all geosets nat -> HD | body(id0) nat -> HD | default look nat -> HD (x) | max look nat -> HD (x) |", "|---|---|---|---|---|"]
for r in rows:
    L.append(f"| {r[0]} {r[1]} | {r[2]:,} -> {r[3]:,} | {r[8]:,} -> {r[9]:,} | {r[4]:,} -> {r[5]:,} ({r[5]/max(r[4],1):.1f}x) | {r[6]:,} -> {r[7]:,} ({r[7]/max(r[6],1):.1f}x) |")
(R / "analysis/characters/drawn-cost.md").write_text("\n".join(L) + "\n")
print("\n".join(L))
