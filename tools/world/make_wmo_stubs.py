#!/usr/bin/env python3
"""W10: generate stub WMOs for every WMO that ADTs place but no shipped archive contains (the game prunes them; a missing WMO
stays 'placement pending' and hangs the loading screen, see tools/content/adt_placements.py).

A stub = the game's own smallest WMO (KeepWall\\WallPost02: 580 B root + 1 group of 1,840 B) copied under the missing path
(root + <base>_000.wmo). Local-only data: derived from the player's own game files, never bundled. Output is a loose tree.
Usage: python make_wmo_stubs.py --out <empty dir> [--template World\\wmo\\Azeroth\\Buildings\\KeepWall\\WallPost02.wmo]
"""
import argparse, re, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
from inventory_world import parse_adt, CACHE  # noqa: E402

INSPECT = ROOT / "tools" / "mpq-inspect.exe"
BS = chr(92)


def chain_set():
    s = set()
    for l in open(ROOT / "analysis" / "world" / "chain-list.txt", encoding="utf8", errors="replace"):
        m = re.match(r"\s*\d+\s+(.*)", l.rstrip("\n"))
        if m:
            s.add(m.group(1).lower())
    return s


def missing_wmos():
    chain = chain_set()
    miss = {}
    for adt in sorted(CACHE.rglob("*.adt")):
        r = parse_adt(adt.read_bytes())
        for n, c in r["wmo_by_name"].items():
            k = n.lower().replace("/", BS)
            if k not in chain:
                miss.setdefault(k, 0)
                miss[k] += c
    return miss


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--template", default=BS.join(["World", "wmo", "Azeroth", "Buildings", "KeepWall", "WallPost02.wmo"]))
    a = ap.parse_args()
    out = Path(a.out)
    if out.exists() and any(out.iterdir()):
        sys.exit("output dir must be empty")
    out.mkdir(parents=True, exist_ok=True)
    tmpl = a.template
    base = tmpl[:-4]
    blobs = {}
    for src, key in ((tmpl, "root"), (base + "_000.wmo", "group")):
        d = out.parent / (out.name + "-tmpl-" + key)
        r = subprocess.run([str(INSPECT), str(ROOT / "Data"), "extract", src, str(d)], capture_output=True)
        if r.returncode or not d.exists():
            sys.exit("cannot extract " + src)
        blobs[key] = d.read_bytes()
        d.unlink()
    miss = missing_wmos()
    for p in sorted(miss):
        rel = Path(p.replace(BS, "/"))
        (out / rel).parent.mkdir(parents=True, exist_ok=True)
        (out / rel).write_bytes(blobs["root"])
        g = Path(str(rel)[:-4] + "_000.wmo")
        (out / g).write_bytes(blobs["group"])
    print("stubs for", len(miss), "missing WMOs,", sum(miss.values()), "placements; template", tmpl, {k: len(v) for k, v in blobs.items()})


if __name__ == "__main__":
    main()
