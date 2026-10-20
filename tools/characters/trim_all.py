#!/usr/bin/env python3
"""Decimate all 16 HD player M2s into analysis/characters/trim/<tag>/Character/... and report drawn-triangle cost.
  trim_all.py <tag> [--ratio R] [--max-err E] [--hair-ratio H]   (extra args pass through to decimate_m2.py)"""
import subprocess, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from m2lib import M2
R = Path(__file__).resolve().parents[2]
SRC = R / "mod-source/characters-hd/payload"
tag = sys.argv[1]
out = R / "analysis/characters/trim" / tag
def by(path):
    d = {}
    for s in M2(Path(path).read_bytes()).submeshes():
        d[s["id"]] = d.get(s["id"], 0) + s["tcount"] // 3
    return d
def look(b):
    body = b.get(0, 0)
    hair = sorted(v for k, v in b.items() if 0 < k < 100)
    dflt = body + b.get(1, 0) + sum(v for k, v in b.items() if k >= 100 and k % 100 == 1)
    g = {}
    for k, v in b.items():
        if k >= 100:
            g[k // 100] = max(g.get(k // 100, 0), v)
    return dflt, body + (max(hair) if hair else 0) + sum(g.values())
rows = []
for m in sorted(SRC.glob("Character/*/*/*HD.m2")):
    rel = m.relative_to(SRC)
    dst = out / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([sys.executable, str(Path(__file__).parent / "decimate_m2.py"), str(m), str(dst)] + sys.argv[2:], capture_output=True, text=True)
    if r.returncode:
        print(r.stdout, r.stderr); sys.exit(1)
    a, b = look(by(m)), look(by(dst))
    nat = by(R / "analysis/characters/native-m2" / (m.stem[:-2] + ".m2"))
    n = look(nat)
    rows.append((rel.parts[1], rel.parts[2], n, a, b))
    print(f"{rel.parts[1]} {rel.parts[2]}: default look native {n[0]} HD {a[0]} -> trim {b[0]} ({b[0]/n[0]:.1f}x)  max look native {n[1]} HD {a[1]} -> trim {b[1]} ({b[1]/n[1]:.1f}x)")
