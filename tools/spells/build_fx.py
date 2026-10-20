#!/usr/bin/env python3
"""Batch: convert every inventoried spell effect model from Retail (fallback Forever) CASC dumps to native MD20 v256
with translated particle/ribbon emitters, re-encode their textures (BLP2 DXT + mips) under a private alias path, and
write analysis/spells/fx-out/{tree,manifest.json}.

Retail art is Blizzard data: output stays in local analysis / the local preview only, nothing is published.
Usage: build_fx.py [--only substr] [--profiles retail,forever]
"""
import argparse
import hashlib
import io
import json
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

ROOT = Path(r".")
sys.path.insert(0, str(ROOT / "tools/spells"))
sys.path.insert(0, str(ROOT / "tools/skinned-converter"))
import fx_test  # noqa: E402
import blp_write  # noqa: E402
import skinned_convert as sc  # noqa: E402

BS = chr(92)
A = ROOT / "analysis/spells"
OUT = A / "fx-out"
ALIAS = "Spells" + BS + "AzerothSurvivorsHD" + BS + "R" + BS
INSPECT = str(ROOT / "tools/mpq-inspect.exe")
MAX_DIM = 512


def sha(b):
    return hashlib.sha256(b).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--profiles", default="retail,forever")
    a = ap.parse_args()
    profiles = a.profiles.split(",")
    lf = fx_test.load_lf()
    base = json.load(open(A / "baseline.json"))
    fdids = json.load(open(A / "casc/model-fdids.json"))
    work = A / "work"
    fb = sc.load_animation_fallbacks(A / "baseline-data", work)
    tree = OUT / "tree"
    if tree.exists():
        shutil.rmtree(tree)
    tree.mkdir(parents=True)
    tex_done = {}      # alias path (lower) -> record
    manifest = {"models": {}, "textures": {}, "failed": {}}
    for name in sorted(fdids):
        if a.only and a.only not in name:
            continue
        done = None
        errs = {}
        for prof in profiles:
            used = []
            missing = []

            def tp(slot, t, prof=prof, used=used, missing=missing):
                fd = t["fdid"]
                src = A / f"casc/tex-{prof}/{fd}.bin"
                if not src.exists():
                    missing.append(fd)
                    return (t["path"] or "").replace("/", BS) or ("Spells" + BS + "MISSING_" + str(fd) + ".BLP")
                nm = lf.get(fd, str(fd)).split("/")[-1]
                stem = Path(nm).stem
                alias = f"{ALIAS}{stem}_{fd}.BLP"
                used.append((fd, alias))
                return alias

            try:
                out, rep = fx_test.convert_one(name, prof, lf, fb, tp)
            except Exception as e:  # noqa: BLE001
                errs[prof] = repr(e)[:200] + " @ " + traceback.format_exc().splitlines()[-3].strip()
                continue
            if missing:
                errs[prof] = f"missing textures {sorted(set(missing))}"
                continue
            done = (prof, out, rep, used)
            break
        if not done:
            manifest["failed"][name] = errs
            continue
        prof, out, rep, used = done
        dst = tree / name.replace(BS, "/")
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(out)
        for fd, alias in used:
            key = alias.lower()
            if key in tex_done:
                continue
            raw = (A / f"casc/tex-{prof}/{fd}.bin").read_bytes()
            im = blp_write.decode(raw)
            w0, h0 = im.size
            if max(im.size) > MAX_DIM:
                f = MAX_DIM / max(im.size)
                im = im.resize((max(4, round(im.size[0] * f)), max(4, round(im.size[1] * f))))
            has_alpha = im.getchannel("A").getextrema()[0] < 255
            enc = blp_write.encode_blp2(im, "dxt5" if has_alpha else "dxt1", mips=True)
            p = tree / alias.replace(BS, "/")
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(enc)
            tex_done[key] = {"fdid": fd, "profile": prof, "src": f"{w0}x{h0}", "out": f"{im.size[0]}x{im.size[1]}",
                             "src_bytes": len(raw), "bytes": len(enc), "alpha": has_alpha}
        rep["textures_used"] = [al for _, al in used]
        rep["sha256"] = sha(out)
        rep["bytes"] = len(out)
        manifest["models"][name] = rep
    manifest["textures"] = tex_done
    json.dump(manifest, open(OUT / "manifest.json", "w"), indent=1, default=str)
    mpq = OUT / "fx.MPQ"
    if mpq.exists():
        mpq.unlink()
    subprocess.run([sys.executable, str(ROOT / "tools/pack-mpq.py"), str(tree), str(mpq)], check=True, capture_output=True)
    tb = sum(v["bytes"] for v in tex_done.values())
    print("models", len(manifest["models"]), "failed", len(manifest["failed"]), "textures", len(tex_done), "tex bytes", tb,
          "mpq", mpq.stat().st_size)
    for k, v in manifest["failed"].items():
        print("FAIL", k, v)


if __name__ == "__main__":
    main()
