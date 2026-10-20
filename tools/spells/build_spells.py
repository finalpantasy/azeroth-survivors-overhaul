#!/usr/bin/env python3
"""Build the spell-upgrade payload from a plan (analysis/spells/plan.json) into analysis/spells/out/{tree,spells-patch.MPQ}.

Plan:
{
 "models": [{"native": "spells\\\\x.m2", "source": "<abs file>", "alias_dir": "Spells\\\\AzerothSurvivorsHD\\\\S1",
             "textures": {"<texture name in source model, lower>": "<abs BLP file or null for keep>"}, "clamp_from_native": true, "note": ""}],
 "textures": [{"native": "spells\\\\x.blp", "source": "<abs BLP/PNG/etc>", "mode": "copy|dxt1|dxt5", "max_edge": 512}]
}
Models are written at the native path (overlay slot patch-0 wins over base patch.MPQ; files that exist in official
patch-2..8 would be shadowed, so such native paths are rejected here). Texture aliases keep other native users of a shared
texture untouched.
"""
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(r".")
A = ROOT / "analysis/spells"
BS = chr(92)
sys.path.insert(0, str(ROOT / "tools/spells"))
import m2_patch  # noqa: E402
from blp_write import encode_blp2  # noqa: E402


def sha(b):
    return hashlib.sha256(b).hexdigest()


def shadowed_paths():
    out = set()
    for n in range(2, 9):
        r = subprocess.run([str(ROOT / "tools/mpq-inspect.exe"), str(ROOT / f"Data/patch-{n}.MPQ"), "list"], capture_output=True, text=True)
        for ln in r.stdout.splitlines():
            parts = ln.split(None, 1)
            if len(parts) == 2 and parts[0].isdigit():
                out.add(parts[1].strip().lower().replace("/", BS))
    return out


def main():
    plan_path = Path(sys.argv[1] if len(sys.argv) > 1 else A / "plan.json")
    outdir = Path(sys.argv[2] if len(sys.argv) > 2 else A / "out")
    plan = json.load(open(plan_path))
    tree = outdir / "tree"
    if tree.exists():
        shutil.rmtree(tree)
    tree.mkdir(parents=True)
    shadow = shadowed_paths()
    base = json.load(open(A / "baseline.json"))
    manifest = {"plan": str(plan_path), "models": {}, "textures": {}, "rejected": {}}

    def put(rel, data):
        p = tree / rel.replace(BS, "/")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return sha(data)

    for t in plan.get("textures", []):
        nat = t["native"].lower().replace("/", BS)
        if nat in shadow:
            manifest["rejected"][nat] = "shadowed by official patch-2..8"
            continue
        src = Path(t["source"])
        if t.get("mode", "copy") == "copy":
            data = src.read_bytes()
        else:
            im = Image.open(src).convert("RGBA")
            me = t.get("max_edge", 512)
            if max(im.size) > me:
                f = me / max(im.size)
                im = im.resize((max(1, round(im.size[0] * f)), max(1, round(im.size[1] * f))), Image.LANCZOS)
            data = encode_blp2(im, t["mode"])
        h = put(nat, data)
        manifest["textures"][nat] = {"sha256": h, "bytes": len(data), "native_bytes": base["textures"].get(nat, {}).get("bytes"), "source": str(src)}
    for m in plan.get("models", []):
        nat = m["native"].lower().replace("/", BS)
        if nat in shadow:
            manifest["rejected"][nat] = "shadowed by official patch-2..8"
            continue
        data = Path(m["source"]).read_bytes()
        info = {"source": m["source"], "note": m.get("note", "")}
        if m.get("clamp_from_native", True):
            nb = (A / "baseline/m2" / nat.replace(BS, "__")).read_bytes()
            data, ch = m2_patch.set_seq_clamp(data, nb)
            info["clamp_changes"] = ch
        ren = {}
        tex = m2_patch.textures(data)
        for t in tex:
            nm = t["name"].lower()
            if nm in m.get("textures", {}) and m["textures"][nm]:
                new = m["alias_dir"] + BS + nm.split(BS)[-1].upper()
                ren[t["i"]] = new
                src = Path(m["textures"][nm])
                if new.lower() not in manifest["textures"]:
                    d = src.read_bytes()
                    manifest["textures"][new.lower()] = {"sha256": put(new, d), "bytes": len(d), "source": str(src), "alias_of": nm}
        if ren:
            data = m2_patch.rename_textures(data, ren)
            info["renamed"] = {tex[i]["name"]: v for i, v in ren.items()}
        info["sha256"] = put(nat, data)
        info["bytes"] = len(data)
        manifest["models"][nat] = info
    out = outdir / "spells-patch.MPQ"
    if out.exists():
        out.unlink()
    subprocess.run([sys.executable, str(ROOT / "tools/pack-mpq.py"), str(tree), str(out)], check=True)
    manifest["mpq"] = {"path": str(out), "bytes": out.stat().st_size, "sha256": sha(out.read_bytes())}
    tb_new = sum(v["bytes"] for v in manifest["textures"].values())
    tb_old = sum(v.get("native_bytes") or 0 for v in manifest["textures"].values())
    manifest["texture_bytes"] = {"new": tb_new, "native_replaced": tb_old}
    json.dump(manifest, open(outdir / "manifest.json", "w"), indent=1)
    print("models", len(manifest["models"]), "textures", len(manifest["textures"]), "rejected", len(manifest["rejected"]), manifest["rejected"])
    print("mpq", manifest["mpq"]["bytes"], "texture bytes new", tb_new, "native replaced", tb_old)


if __name__ == "__main__":
    main()
