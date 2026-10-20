#!/usr/bin/env python3
"""Cost per model: native vs community HD (vertices, triangles, batches, bones, sequences, M2 bytes,
default-look texture bytes/VRAM estimate). Writes analysis/characters/COST.md + cost.json.

Reads the loose payload (mod-source/characters-hd/payload), the native model extracts in
analysis/characters/native-m2, and native textures through mpq-inspect.exe (read only).
Flags any model over 2x native triangles and prints the mitigation recorded for it.
"""
from __future__ import annotations

import json
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dbclib import DBC  # noqa: E402
from m2lib import M2  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
PAYLOAD = ROOT / "mod-source" / "characters-hd" / "payload"
REPORTS = ROOT / "analysis" / "characters"
NATIVE_M2 = REPORTS / "native-m2"
INSPECT = ROOT / "tools" / "mpq-inspect.exe"
DATA = ROOT / "Data"
report = json.loads((REPORTS / "build-report.json").read_text())


def blp_info(b: bytes) -> dict:
    if b[:4] != b"BLP2":
        return dict(error="not BLP2", stored=len(b), w=0, h=0, vram=0)
    typ, enc, adepth, aenc, mips, w, h = struct.unpack_from("<IBBBBII", b, 4)
    offs = struct.unpack_from("<16I", b, 20)
    n = sum(1 for o in offs if o)
    stored = len(b)
    if enc == 2:  # DXT: GPU-resident size computed from dimensions (some HD headers carry junk size fields)
        blk = 8 if aenc == 0 else 16
        vram = sum(max(1, (max(1, w >> i) + 3) // 4) * max(1, (max(1, h >> i) + 3) // 4) * blk for i in range(n))
    else:
        vram = int(w * h * 4 * 4 / 3)
    return dict(w=w, h=h, enc=enc, aenc=aenc, mips=n, stored=stored, vram=vram)


def native_blp(path: str) -> bytes | None:
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "x.blp"
        r = subprocess.run([str(INSPECT), str(DATA), "extract", path, str(out)], capture_output=True, text=True)
        return out.read_bytes() if r.returncode == 0 and out.exists() else None


cs = DBC((REPORTS / "native-dbc" / "CharSections.dbc").read_bytes())          # native rows
cs_new = DBC((PAYLOAD / "DBFilesClient" / "CharSections.dbc").read_bytes())   # HD rows
SEX = {0: "Male", 1: "Female"}
rows_out = []
for pm in report["models"]:
    race_id = {"Human": 1, "Orc": 2, "Dwarf": 3, "NightElf": 4, "Scourge": 5, "Tauren": 6, "Gnome": 7, "Troll": 8}[pm["race"]]
    sex = 0 if pm["sex"] == "Male" else 1
    hd_path = PAYLOAD.joinpath(*pm["private_path"].split("\\"))
    hd = M2(hd_path.read_bytes())
    base = pm["native_path"].rsplit("\\", 1)[1].rsplit(".", 1)[0]
    nat = M2((NATIVE_M2 / f"{base}.m2").read_bytes())
    hs, ns = hd.stats(), nat.stats()
    # default look = flag-0 rows with variation 0 / colour 0 (skin 0, face 0, hair 0, facial 0, underwear 0)
    tot = {"native": dict(stored=0, vram=0, n=0, maxdim=0), "hd": dict(stored=0, vram=0, n=0, maxdim=0)}
    for r in cs.rows:
        if r[1] != race_id or r[2] != sex or r[9] != 0 or r[4] != 0 or r[5] != 0:
            continue
        rn = next(x for x in cs_new.rows if x[0] == r[0])
        for which, d, row in (("native", cs, r), ("hd", cs_new, rn)):
            for i in range(6, 9):
                p = d.string(row[i])
                if not p:
                    continue
                if which == "hd":
                    f = PAYLOAD.joinpath(*p.split("\\"))
                    b = f.read_bytes() if f.is_file() else native_blp(p)
                else:
                    b = native_blp(p)
                if not b:
                    continue
                inf = blp_info(b)
                t = tot[which]
                t["stored"] += inf["stored"]; t["vram"] += inf["vram"]; t["n"] += 1
                t["maxdim"] = max(t["maxdim"], inf["w"], inf["h"])
    tri_ratio = hs["triangles_skin0"] / ns["triangles_skin0"]
    rows_out.append(dict(
        display=pm["display"], race=pm["race"], sex=pm["sex"],
        native=dict(vertices=ns["vertices"], triangles=ns["triangles_skin0"], batches=ns["batches_skin0"], bones=ns["bones"],
                    sequences=ns["sequences"], m2_bytes=ns["file_bytes"], tex=tot["native"]),
        hd=dict(vertices_loaded=hd.count("vertices"), vertices_file=pm["vertices_full"], triangles=hs["triangles_skin0"],
                batches=hs["batches_skin0"], bones=hs["bones"], sequences=hs["sequences"], m2_bytes=hs["file_bytes"], tex=tot["hd"]),
        triangle_ratio=round(tri_ratio, 2), over_2x=tri_ratio > 2.0,
        staged_texture_bytes=pm["texture_bytes"],
    ))
(REPORTS / "cost.json").write_text(json.dumps(rows_out, indent=1))

L = ["# Character model cost: native vs community HD (skin 0 = the view the engine draws)\n",
     "Counts parsed from the shipped M2s (MD20 v256). `verts` is the loaded vertex count: HD files carry 3-5x more vertex records than any view "
     "references, the staged copies use the count-only trim (header 0x44) so only the referenced prefix is decoded. Triangles/batches/bones/sequences "
     "are not reduced by the trim. Texture columns are the default look (skin/face/hair/facial hair/underwear, variation 0, colour 0, flag 0): "
     "file count, stored BLP bytes, estimated GPU bytes (DXT as stored, otherwise RGBA8+mips), largest side.\n",
     "| Model | verts nat -> HD | tris nat -> HD (x) | batches | bones | seq | M2 MB | default-look tex nat -> HD (MB stored / max px) |",
     "|---|---|---|---|---|---|---|---|"]
for r in rows_out:
    n, h = r["native"], r["hd"]
    flag = " **>2x**" if r["over_2x"] else ""
    L.append(f"| {r['race']} {r['sex']} (disp {r['display']}) | {n['vertices']:,} -> {h['vertices_loaded']:,} (file {h['vertices_file']:,}) | "
             f"{n['triangles']:,} -> {h['triangles']:,} ({r['triangle_ratio']}x){flag} | {n['batches']} -> {h['batches']} | {n['bones']} -> {h['bones']} | "
             f"{n['sequences']} -> {h['sequences']} | {n['m2_bytes']/1e6:.1f} -> {h['m2_bytes']/1e6:.1f} | "
             f"{n['tex']['stored']/1e6:.2f} / {n['tex']['maxdim']} -> {h['tex']['stored']/1e6:.2f} / {h['tex']['maxdim']} |")
over = [r for r in rows_out if r["over_2x"]]
L += ["", f"{len(over)} of {len(rows_out)} models exceed 2x native triangles (every HD body is 3.5x-6x). Trim plan / mitigation: see "
      "`analysis/characters/TRIM-PLAN.md`.", ""]
(REPORTS / "COST.md").write_text("\n".join(L), encoding="utf-8")
print("\n".join(L))
