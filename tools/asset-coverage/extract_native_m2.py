#!/usr/bin/env python3
"""Extract every native .m2 (read-only) from the installed MPQ chain into a work cache and census the headers.

Uses mpq-inspect.exe extract (chain-aware, honors patch priority). Runs at BelowNormal; children inherit it.
Outputs: <out>/m2-census.csv with one row per model (path, bytes, verts, bones, seqs, triangles, particles, ...).
The cache keeps only the model bytes for later bounded reuse; nothing touches the game folder.
"""
import argparse
import csv
import ctypes
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import m2lib  # noqa: E402

BS = chr(92)


def lower_priority():
    if os.name == "nt":
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--inspect", required=True)
    ap.add_argument("--list", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=3)
    a = ap.parse_args()
    lower_priority()
    out = Path(a.out)
    cache = out / "m2cache"
    cache.mkdir(parents=True, exist_ok=True)
    names = {}
    for line in open(a.list, encoding="utf8", errors="replace"):
        m = re.match(r"\s*(\d+)\s+(.*)$", line.rstrip("\n"))
        if m and m.group(2).lower().endswith(".m2"):
            names.setdefault(m.group(2).lower(), (m.group(2), int(m.group(1))))
    items = sorted(names.values())
    print("models", len(items), flush=True)

    def work(item):
        path, size = item
        dest = cache / (path.replace(BS, "__"))
        if not dest.exists() or dest.stat().st_size == 0:
            r = subprocess.run([a.inspect, a.data, "extract", path, str(dest)], capture_output=True, text=True)
            if r.returncode != 0:
                return path, size, None, r.stderr.strip()[:120]
        try:
            info = m2lib.parse_any(dest.read_bytes())
        except Exception as e:  # noqa: BLE001
            return path, size, None, repr(e)
        return path, size, info, ""

    rows, bad = [], []
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        for i, (path, size, info, err) in enumerate(ex.map(work, items)):
            if info is None:
                bad.append((path, err))
                continue
            rows.append((path, size, info))
            if i % 1000 == 0:
                print(i, flush=True)
    keys = ["path", "bytes", "version", "verts", "bones", "seq", "triangles", "submeshes", "batches", "textures",
            "texture_types", "particles", "ribbons", "attach", "cameras", "lights", "views"]
    with open(out / "m2-census.csv", "w", newline="", encoding="utf8") as f:
        w = csv.writer(f)
        w.writerow(keys)
        for path, size, info in rows:
            w.writerow([path, size] + [info.get(k, "") if k != "texture_types" else "|".join(map(str, info.get(k, []))) for k in keys[2:]])
    with open(out / "m2-census-errors.txt", "w", encoding="utf8") as f:
        for p, e in bad:
            f.write(f"{p}\t{e}\n")
    print("done rows", len(rows), "errors", len(bad), flush=True)


if __name__ == "__main__":
    main()
