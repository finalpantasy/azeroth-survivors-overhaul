#!/usr/bin/env python3
"""Readback check: MPQ listing == payload tree (path+size), then extract every model/DBC and a sample of
textures through the native reader and compare bytes.  Works on an MPQ file or on a Data dir (effective chain).

  python verify_mpq.py <mpq-or-data-dir> [--payload DIR]
"""
import argparse
import hashlib
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INSPECT = ROOT / "tools" / "mpq-inspect.exe"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("target")
    ap.add_argument("--payload", default=str(ROOT / "mod-source" / "characters-hd" / "payload"))
    ap.add_argument("--sample", type=int, default=60, help="extra textures to byte-compare")
    a = ap.parse_args()
    payload = Path(a.payload)
    exp = {}
    for r, _, fs in os.walk(payload):
        for f in fs:
            p = Path(r) / f
            exp[str(p.relative_to(payload)).replace("/", chr(92)).lower()] = (str(p.relative_to(payload)).replace("/", chr(92)), p)
    out = subprocess.run([str(INSPECT), a.target, "list"], capture_output=True, text=True).stdout
    got = {}
    for line in out.splitlines():
        m = re.match(r"^\s*(\d+)\s+(.+?)\s*$", line)
        if m:
            got[m.group(2).lower()] = int(m.group(1))
    bad = [k for k, (_, p) in exp.items() if got.get(k) != p.stat().st_size]
    print(f"payload {len(exp)} files; in target with matching size: {len(exp) - len(bad)}; mismatches: {len(bad)}")
    if bad:
        print(bad[:10])
    keys = sorted(exp)
    must = [k for k in keys if k.endswith((".m2", ".dbc"))]
    step = max(1, len(keys) // a.sample)
    sample = must + [k for k in keys[::step] if k not in must]
    n_ok = 0
    with tempfile.TemporaryDirectory() as d:
        for k in sample:
            name, p = exp[k]
            o = Path(d) / "x.bin"
            if o.exists():
                o.unlink()
            r = subprocess.run([str(INSPECT), a.target, "extract", name, str(o)], capture_output=True, text=True)
            if r.returncode or not o.exists() or o.read_bytes() != p.read_bytes():
                print("READBACK MISMATCH", name)
                bad.append(k)
            else:
                n_ok += 1
    print(f"byte-compared {n_ok}/{len(sample)} entries (all .m2/.dbc + sample)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
