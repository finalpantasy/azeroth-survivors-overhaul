#!/usr/bin/env python3
"""W10: stage the isolated world preview `previews/world-v0.103.0` (never the main install).

Data = the installed main Data (hardlinks for the read-only official archives and patch-0, byte-checked) with
patch-1.MPQ replaced by a MERGED overlay: the installed HD terrain (terrain-hd.MPQ, extracted byte-exact) plus the
world payload built by build_world_overlay.py. EXE = a copy of the official v0.103.0 EXE.
Usage: python -I stage_world_preview.py --payload <dir> [--name world-v0.103.0] [--tag merged-v1]
"""
import argparse, hashlib, json, os, shutil, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(r".")
PROJECT = ROOT.parent
INSPECT = ROOT / "tools" / "mpq-inspect.exe"
PACK = ROOT / "tools" / "pack-mpq.py"
EXE_SHA = "6382e9ae654295293b82d48fb07a1aa999132775bda10c6c6f80d973d2c1ddfb"
LINKED = ["patch.MPQ", "patch-0.MPQ", "patch-2.MPQ", "patch-3.MPQ", "patch-4.MPQ", "patch-5.MPQ", "patch-6.MPQ", "patch-7.MPQ", "patch-8.MPQ"]


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--payload", required=True, nargs="+")
    ap.add_argument("--rebuild", action="store_true", help="delete and recreate THIS preview (only previews/world-*)")
    ap.add_argument("--name", default="world-v0.103.0")
    a = ap.parse_args()
    dest = PROJECT / "previews" / a.name
    if dest.exists():
        if not (a.rebuild and a.name.startswith("world-") and dest.parent == PROJECT / "previews"):
            sys.exit("preview exists: %s (use --rebuild, or choose another --name)" % dest)
        shutil.rmtree(dest)
    payloads = [Path(x) for x in a.payload]
    exe = ROOT / "AzerothSurvivors.exe"
    if sha(exe) != EXE_SHA:
        sys.exit("main EXE is not the official v0.103.0 build; refusing")
    tmp = Path(tempfile.mkdtemp(prefix="world-merge-", dir=str(ROOT / "analysis" / "world")))
    try:
        terrain = tmp / "terrain"
        # 1. terrain overlay, byte-exact, from the installed patch-1
        lst = subprocess.run([str(INSPECT), str(ROOT / "Data" / "patch-1.MPQ"), "list"], capture_output=True, text=True,
                             encoding="utf8", errors="replace").stdout.splitlines()
        n = 0
        for l in lst:
            parts = l.strip().split(None, 1)
            if len(parts) != 2:
                continue
            rel = parts[1]
            d = terrain / rel.replace(chr(92), "/")
            d.parent.mkdir(parents=True, exist_ok=True)
            r = subprocess.run([str(INSPECT), str(ROOT / "Data" / "patch-1.MPQ"), "extract", rel, str(d)], capture_output=True)
            if r.returncode or not d.exists():
                sys.exit("terrain extract failed for " + rel)
            n += 1
        # 2. merge: world payload must not collide with terrain paths
        merged = tmp / "merged"
        shutil.copytree(terrain, merged)
        coll = []
        have = {str(f.relative_to(merged)).lower().replace(chr(92), "/") for f in merged.rglob("*") if f.is_file()}
        allf = [(pl, f) for pl in payloads for f in pl.rglob("*") if f.is_file()]
        for pl, f in allf:
            if True:
                rel = f.relative_to(pl)
                if str(rel).lower().replace(chr(92), "/") in have:
                    coll.append(str(rel))
                    continue
                t = merged / rel
                t.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, t)
        if coll:
            print("WARNING path collisions (terrain kept):", len(coll), coll[:5])
        mpq = tmp / "patch-1.MPQ"
        subprocess.run([sys.executable, "-I", str(PACK), str(merged), str(mpq)], check=True)
        # 3. assemble the preview
        (dest / "Data").mkdir(parents=True)
        shutil.copy2(exe, dest / "AzerothSurvivors.exe")
        links = {}
        for name in LINKED:
            os.link(str(ROOT / "Data" / name), str(dest / "Data" / name))
            links[name] = {"method": "hardlink", "bytes": (dest / "Data" / name).stat().st_size}
        shutil.move(str(mpq), str(dest / "Data" / "patch-1.MPQ"))
        man = {"preview": a.name, "exe_sha256": EXE_SHA, "terrain_files": n, "payloads": [str(x) for x in payloads],
               "payload_files": len(allf),
               "patch1_sha256": sha(dest / "Data" / "patch-1.MPQ"), "patch1_bytes": (dest / "Data" / "patch-1.MPQ").stat().st_size,
               "linked": links, "collisions": coll,
               "note": "Hardlinked archives are read-only. patch-1 = installed HD terrain + world payload. Saves/config come from the lab case."}
        (dest / "manifest.json").write_text(json.dumps(man, indent=1))
        print(json.dumps({k: v for k, v in man.items() if k != "linked"}, indent=1))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
