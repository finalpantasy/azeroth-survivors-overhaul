#!/usr/bin/env python3
"""World inventory (W10): which tilesets / doodads / WMOs each native map's ADTs reference, and which of the
tilesets the installed HD terrain overlay (patch-1.MPQ) already replaces.

Read-only: extracts ADTs with mpq-inspect.exe from the installed chain into a work cache (BelowNormal),
parses MTEX/MMDX/MWMO, writes JSON + CSV under analysis/world/.
Usage: python -I inventory_world.py [--maps azeroth kalimdor pvpzone01 ...]
"""
import argparse, csv, ctypes, json, os, re, struct, subprocess, sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(r".")
INSPECT = ROOT / "tools" / "mpq-inspect.exe"
DATA = ROOT / "Data"
OVERLAY = ROOT / "mod-source" / "terrain-hd.MPQ"
LIST = ROOT / "analysis" / "native-asset-list.txt"
OUT = ROOT / "analysis" / "world"
CACHE = OUT / "adt-cache"
BS = chr(92)


def lower():
    if os.name == "nt":
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)


def native_list():
    rows = []
    for l in open(LIST, encoding="utf8", errors="replace"):
        m = re.match(r"\s*(\d+)\s+(.*)", l.rstrip("\n"))
        if m:
            rows.append((int(m.group(1)), m.group(2)))
    return rows


def overlay_list():
    out = subprocess.run([str(INSPECT), str(OVERLAY), "list"], capture_output=True, text=True, encoding="utf8", errors="replace").stdout
    d = {}
    for l in out.splitlines():
        m = re.match(r"\s*(\d+)\s+(.*)", l)
        if m:
            d[m.group(2).lower()] = int(m.group(1))
    return d


def extract(path, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size:
        return True
    r = subprocess.run([str(INSPECT), str(DATA), "extract", path, str(dest)], capture_output=True)
    return r.returncode == 0 and dest.exists()


def chunks(b):
    i = 0
    while i + 8 <= len(b):
        tag = b[i:i + 4][::-1].decode("latin1")
        size = struct.unpack_from("<I", b, i + 4)[0]
        yield tag, b[i + 8:i + 8 + size], i
        i += 8 + size


def strs(blob):
    return [s.decode("latin1") for s in blob.split(b"\0") if s]


def parse_adt(b):
    r = {"tex": [], "m2": [], "wmo": [], "m2_placements": 0, "wmo_placements": 0, "m2_by_name": {}, "wmo_by_name": {}}
    mmdx = mwmo = b""; mmid = mwid = (); mddf = modf = b""
    for tag, blob, _ in chunks(b):
        if tag == "MTEX":
            r["tex"] = strs(blob)
        elif tag == "MMDX":
            mmdx = blob; r["m2"] = strs(blob)
        elif tag == "MWMO":
            mwmo = blob; r["wmo"] = strs(blob)
        elif tag == "MMID":
            mmid = struct.unpack("<%dI" % (len(blob) // 4), blob)
        elif tag == "MWID":
            mwid = struct.unpack("<%dI" % (len(blob) // 4), blob)
        elif tag == "MDDF":
            mddf = blob
        elif tag == "MODF":
            modf = blob
        elif tag == "MCNK":
            break
    def name_at(blob, off):
        e = blob.find(bytes(1), off)
        return blob[off:e].decode("latin1")
    for i in range(len(mddf) // 36):
        nid = struct.unpack_from("<I", mddf, i * 36)[0]
        if nid < len(mmid):
            n = name_at(mmdx, mmid[nid]).lower(); r["m2_by_name"][n] = r["m2_by_name"].get(n, 0) + 1
    for i in range(len(modf) // 64):
        nid = struct.unpack_from("<I", modf, i * 64)[0]
        if nid < len(mwid):
            n = name_at(mwmo, mwid[nid]).lower(); r["wmo_by_name"][n] = r["wmo_by_name"].get(n, 0) + 1
    r["m2_placements"] = len(mddf) // 36; r["wmo_placements"] = len(modf) // 64
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--maps", nargs="*")
    a = ap.parse_args()
    lower()
    rows = native_list()
    adts = defaultdict(list)
    for size, p in rows:
        parts = p.split(BS)
        if len(parts) == 4 and parts[0].lower() == "world" and parts[1].lower() == "maps" and p.lower().endswith(".adt"):
            adts[parts[2].lower()].append(p)
    maps = [m.lower() for m in a.maps] if a.maps else sorted(adts)
    ov = overlay_list()
    native_tex = {p.lower(): s for s, p in rows if p.lower().startswith("tileset" + BS)}
    jobs = [(p, CACHE / p.replace(BS, "/")) for m in maps for p in adts[m]]
    with ThreadPoolExecutor(3) as ex:
        ok = list(ex.map(lambda j: extract(*j), jobs))
    print("extracted", sum(ok), "/", len(jobs))
    result = {}
    for m in maps:
        tex = defaultdict(int); m2 = defaultdict(int); wmo = defaultdict(int)
        n = 0; m2p = 0; wmop = 0
        for p in adts[m]:
            f = CACHE / p.replace(BS, "/")
            if not f.exists():
                continue
            r = parse_adt(f.read_bytes()); n += 1
            for t in r["tex"]: tex[t.lower()] += 1
            for t, n_ in r["m2_by_name"].items(): m2[t] += n_
            for t, n_ in r["wmo_by_name"].items(): wmo[t] += n_
            m2p += r["m2_placements"]; wmop += r["wmo_placements"]
        hd = [t for t in tex if t in ov]
        missing = [t for t in tex if t not in native_tex and t not in ov]
        result[m] = {
            "adts": n, "tilesets": len(tex), "tilesets_hd": len(hd), "tilesets_old": len(tex) - len(hd),
            "tileset_hd_bytes": sum(ov[t] for t in hd),
            "tileset_old_native_bytes": sum(native_tex.get(t, 0) for t in tex if t not in ov),
            "tilesets_missing_everywhere": missing,
            "doodad_models": len(m2), "doodad_placements": m2p, "wmo_models": len(wmo), "wmo_placements": wmop,
            "old_tilesets": sorted(t for t in tex if t not in ov),
            "tileset_list": sorted(tex),
            "doodads": sorted(m2.items(), key=lambda kv: -kv[1]),
            "wmos": sorted(wmo.items(), key=lambda kv: -kv[1]),
        }
        print(m, {k: v for k, v in result[m].items() if isinstance(v, (int, float))})
    OUT.mkdir(parents=True, exist_ok=True)
    json.dump(result, open(OUT / "world-inventory.json", "w"), indent=1)


if __name__ == "__main__":
    main()
