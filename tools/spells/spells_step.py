#!/usr/bin/env python3
"""Installer entry point for the Retail spell-effect converter (as-recipe/1 `run-tool` step, tool id `spells-fx-converter`).

Converts the 161 spell effect models the game uses from Retail (modern MD21) to native MD20 v256 *with* translated
particle and ribbon emitters (fx_convert.py), and copies their textures (Retail BLP2 DXT files already carry full mip chains: shipped byte for byte, header mip flag normalised to 1) under a private
alias path so the files in the official patch-2..8 never need overriding.

    spells_step.py --source-dir <casc-export step> --game-dir <game-extract step> --output-dir <out>

--source-dir : exported FileDataIDs as <fdid>.bin (models, skins, textures)
--game-dir   : native models (template for the animation lookup) at their archive paths + DBFilesClient/AnimationData.dbc
Output       : <out>/payload/<native paths>, <out>/payload/Spells/AzerothSurvivorsHD/R/<fdid>.BLP, conversion-report.json.
No MPQ is packed: the installer packs the slot itself.

The installer runs exactly one hash-pinned script, so this file pins everything it imports (PINNED); update with
`python spells_step.py --print-pins`, then re-hash this file in installer/tools/registry.json.
Standard library only (runs under `python -I`).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKIN = HERE.parent / "skinned-converter"
PINNED = {
    "fx_models.json": "93cefe10ffb8d7fa55f61a6eebdcdae07f08498ca711ca1f8b50718a66f934ba",
    "fx_particles.py": "c279e4bdece4635dd31335dd49d5ed8906a83b66f9f6fe12346046a2c3f56950",
    "fx_convert.py": "eebf0707c9eb58c5ef8cef13b8ff1df00fcd2c48aabd588aa139592c7f17e786",
    "../skinned-converter/m2ir.py": "6aff8e742506277fa245a61cf1f6ff17bd7624277efe68cd2e654d40ed3cdbb3",
    "../skinned-converter/skinned_convert.py": "7f6c42deae5986b1a91010a3836d105e81fa62aaf62462cad16ce5ce20d2128f",
}
BS = chr(92)
ALIAS = "Spells/AzerothSurvivorsHD/R/"


def digest(rel):
    return hashlib.sha256((HERE / rel).read_bytes()).hexdigest()


def index_dir(root):
    return {str(p.relative_to(root)).replace(BS, "/").lower(): p for p in root.rglob("*") if p.is_file()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-dir", type=Path)
    ap.add_argument("--game-dir", type=Path)
    ap.add_argument("--output-dir", type=Path)
    ap.add_argument("--print-pins", action="store_true")
    a = ap.parse_args()
    if a.print_pins:
        print(json.dumps({rel: digest(rel) for rel in PINNED}, indent=4))
        return 0
    for rel, want in PINNED.items():
        got = digest(rel)
        if got != want:
            print(f"spells_step: {rel} hash {got[:12]} does not match the pinned {want[:12] or '(unset)'}", file=sys.stderr)
            return 2
    sys.path.insert(0, str(HERE))
    sys.path.insert(0, str(SKIN))
    import fx_convert as fx
    import m2ir
    import skinned_convert as sc

    models = json.loads((HERE / "fx_models.json").read_text(encoding="utf8"))
    game = index_dir(a.game_dir)
    fb = sc.load_animation_fallbacks(None, a.output_dir, game_dir=a.game_dir)
    out_root = a.output_dir / "payload"
    out_root.mkdir(parents=True, exist_ok=True)
    seen_tex = {}
    report = {"models": {}, "textures": {}}
    for name, fdid in sorted(models.items()):
        key = name.replace(BS, "/")
        mpath = a.source_dir / f"{fdid}.bin"
        mb = mpath.read_bytes()
        ch = m2ir.split_chunks(mb)
        skin_id = struct.unpack("<%dI" % (len(ch["SFID"]) // 4), ch["SFID"])[0]
        skin = (a.source_dir / f"{skin_id}.bin").read_bytes()
        tmpl = game[key.lower()].read_bytes()
        used = []

        def tp(slot, t, used=used):
            fd = t["fdid"]
            if not fd or not (a.source_dir / f"{fd}.bin").is_file():
                raise m2ir.ConvertError(f"{name}: texture FileDataID {fd} not in the export")
            used.append(fd)
            return (ALIAS + f"{fd}.BLP").replace("/", BS)

        rep = {"fdid": fdid}
        try:
            out = fx.convert(mb, skin, {}, key.split("/")[-1][:-3], tmpl, fb, tp, rep)
        except m2ir.ConvertError as exc:
            print(f"spells_step: {exc}", file=sys.stderr)
            return 1
        dst = out_root / key
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(out)
        for fd in used:
            if fd in seen_tex:
                continue
            raw = (a.source_dir / f"{fd}.bin").read_bytes()
            enc = bytearray(raw)
            if enc[:4] != b"BLP2" or enc[8] != 2:
                print(f"spells_step: texture {fd} is not a DXT BLP2", file=sys.stderr)
                return 1
            w, h = struct.unpack_from("<2I", enc, 12)
            if enc[11]:
                enc[11] = 1   # a few Retail files carry 2 (generated mips); the native engine knows 0/1
            enc = bytes(enc)
            p = out_root / (ALIAS + f"{fd}.BLP")
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(enc)
            seen_tex[fd] = len(enc)
            report["textures"][str(fd)] = {"size": f"{w}x{h}",
                                          "src_bytes": len(raw), "bytes": len(enc)}
        rep["textures_used"] = sorted(set(used))
        rep["sha256"] = hashlib.sha256(out).hexdigest()
        rep["bytes"] = len(out)
        report["models"][name] = rep
    report["totals"] = {"models": len(report["models"]), "textures": len(seen_tex), "texture_bytes": sum(seen_tex.values()),
                        "particles": sum(r.get("particles", 0) for r in report["models"].values()),
                        "ribbons": sum(r.get("ribbons", 0) for r in report["models"].values())}
    (a.output_dir / "conversion-report.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf8")
    print(json.dumps(report["totals"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
