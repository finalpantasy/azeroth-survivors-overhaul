#!/usr/bin/env python3
"""S1 candidate: community VanillaHD Patch-A (pinned commit) effect models that are classic-format drop-ins for the
inventoried spell models, plus every texture they (and only they) reference that VanillaHD ships.

Layout: analysis/spells/s1/tree/<native path>; also writes s1/manifest.json (per model risk flags) and packs s1/s1.MPQ.
No Blizzard bytes outside local analysis. Nothing installed.
"""
import hashlib
import json
import shutil
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(r".")
A = ROOT / "analysis/spells"
S1 = A / "s1"
BS = chr(92)
INSPECT = str(ROOT / "tools/mpq-inspect.exe")
sys.path.insert(0, str(ROOT / "tools/spells"))
from spell_inventory import m2_info  # noqa: E402


def sha(b):
    return hashlib.sha256(b).hexdigest()


def main():
    cmp_ = json.load(open(A / "vhd-compare.json"))
    base = json.load(open(A / "baseline.json"))
    V = A / "vanillahd"
    idx = {}
    for p in V.rglob("*"):
        if p.is_file() and ".git" not in p.parts:
            idx[str(p.relative_to(V)).replace("\\", "/").lower()] = p
    tree = S1 / "tree"
    if tree.exists():
        shutil.rmtree(tree)
    manifest = {"source": "VanillaHD/Patch-A dcf3647e80104d03c02e1540c61cce3ca7e24766", "models": {}, "textures": {}}
    for m, r in sorted(cmp_["models"].items()):
        if not r.get("vhd") or r.get("identical") is not False or "verts" not in r:
            continue
        k = m.replace(BS, "/")
        src = idx[k]
        dst = tree / m.replace(BS, "/")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        manifest["models"][m] = {k2: r[k2] for k2 in ("bytes", "native_bytes", "verts", "native_verts", "particles", "native_particles", "ribbons", "native_ribbons")}
        manifest["models"][m]["sha256"] = sha(src.read_bytes())
        manifest["models"][m]["abilities"] = base["models"][m]["abilities"]
        # textures the candidate needs
        for tn in r["textures"]:
            t = tn.lower()
            tk = t.replace(BS, "/")
            if tk in idx:
                d2 = tree / tk
                d2.parent.mkdir(parents=True, exist_ok=True)
                if not d2.exists():
                    shutil.copyfile(idx[tk], d2)
                    manifest["textures"][t] = {"sha256": sha(idx[tk].read_bytes()), "bytes": idx[tk].stat().st_size,
                                               "native_bytes": base["textures"].get(t, {}).get("bytes")}
            elif t not in base["textures"]:
                manifest["models"][m].setdefault("missing_textures", []).append(tn)
    json.dump(manifest, open(S1 / "manifest.json", "w"), indent=1)
    out = S1 / "s1.MPQ"
    subprocess.run([sys.executable, str(ROOT / "tools/pack-mpq.py"), str(tree), str(out)], check=True)
    print("models", len(manifest["models"]), "textures", len(manifest["textures"]), "mpq bytes", out.stat().st_size)
    miss = {m: v["missing_textures"] for m, v in manifest["models"].items() if v.get("missing_textures")}
    print("models with textures present in neither VanillaHD nor native baseline:", miss)


if __name__ == "__main__":
    main()
