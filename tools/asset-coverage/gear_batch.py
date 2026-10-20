#!/usr/bin/env python3
"""Batch rigid gear conversion into ONE isolated payload + ItemDisplayInfo edit list (nothing installed).

picks.json = {"batch": "R1", "items": [{"id": "sword-1h-northrend", "kind": "Weapon", "fdid": 148059, "name": "Sword1H_Northrend_D_02_Static",
              "displays": [1542], "note": "..."}]}
Per item: fetch model/skin/BLP (offline CASC, read only), run the rigidity gate with every documented relaxation, convert with
rigid_item_convert.build, copy the BLP, record triangle/texture budgets.  Output:
  <out>/payload/Item/ObjectComponents/<Kind>/<name>.m2|.blp
  <out>/gear-report.json   (per item: ok/fail reason, stats, relaxations used, bytes)
  <out>/display-edits.json (ItemDisplayInfo model-name edits: display id, column, new basename)
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("ric", HERE / "rigid_item_convert.py")
ric = importlib.util.module_from_spec(spec)
sys.modules["ric"] = ric
spec.loader.exec_module(ric)


SCALE_MIN, SCALE_MAX = 0.45, 1.3


def scale_info(info, s):
    """Uniformly scale model-space positions (vertices + attachment points); normals/UVs are unchanged."""
    vb = bytearray(info["vblob"])
    for i in range(info["vcount"]):
        x, y, z = struct.unpack_from("<3f", vb, i * 48)
        struct.pack_into("<3f", vb, i * 48, x * s, y * s, z * s)
    info["vblob"] = bytes(vb)
    info["positions"] = [(x * s, y * s, z * s) for x, y, z in info["positions"]]
    info["attachments"] = [(a, tuple(c * s for c in pos)) for a, pos in info["attachments"]]


def translate_info(info, d):
    """Move the model: vertex positions and attachment points by d (model units); normals/UVs are unchanged."""
    vb = bytearray(info["vblob"])
    for i in range(info["vcount"]):
        x, y, z = struct.unpack_from("<3f", vb, i * 48)
        struct.pack_into("<3f", vb, i * 48, x + d[0], y + d[1], z + d[2])
    info["vblob"] = bytes(vb)
    info["positions"] = [(x + d[0], y + d[1], z + d[2]) for x, y, z in info["positions"]]
    info["attachments"] = [(a, tuple(c + o for c, o in zip(pos, d))) for a, pos in info["attachments"]]


def rotate_shield(info):
    """Retail shield models have their top toward -X and a long axis along X (the client's attachment adds a 90 degree turn the
    native engine does not): rotate every position/normal about Y so -X becomes +Z (x' = z, z' = -x).  Attachments move with it."""
    vb = bytearray(info["vblob"])
    for i in range(info["vcount"]):
        for off in (0, 20):
            x, y, z = struct.unpack_from("<3f", vb, i * 48 + off)
            struct.pack_into("<3f", vb, i * 48 + off, z, y, -x)
    info["vblob"] = bytes(vb)
    info["positions"] = [(z, y, -x) for x, y, z in info["positions"]]
    info["attachments"] = [(a, (pos[2], pos[1], -pos[0])) for a, pos in info["attachments"]]


def convert_one(item, cache, listfile, profile, payload):
    fd = item["fdid"]
    ric.fetch(cache, profile, [fd])
    model = ric.read_cache(cache, fd)
    ch = ric.split_chunks(model)
    skin_id = struct.unpack_from("<I", ch["SFID"], 0)[0]
    ric.fetch(cache, profile, [skin_id])
    skin = ric.read_cache(cache, skin_id)
    info = ric.inspect(model, skin, True, True, True, True)
    if item["kind"] == "Shield" and not item.get("no_rotate"):
        rotate_shield(info)
    scale = max(SCALE_MIN, min(SCALE_MAX, item["scale"])) if item.get("scale") else 1.0
    if scale != 1.0:
        scale_info(info, scale)
    if item.get("offset"):
        translate_info(info, item["offset"])
    relax = {"texture_animations": info["counts"]["texture_animations"][0], **info["dropped_effects"]}
    relax["frozen_identity_bone_tracks"] = info["frozen_tracks"]
    relax = {k: v for k, v in relax.items() if v}
    overrides = {int(k): int(v) for k, v in item.get("tex", {}).items()}
    try:
        pick = ric.make_listfile_pick(listfile, fd)
    except Exception:  # noqa: BLE001  (no sibling BLP: only explicit --tex overrides can work)
        pick = None
    slots = ric.derive_textures(info, overrides, pick)
    ric.fetch(cache, profile, list(set(slots.values())))
    kind = item["kind"]
    base_dir = f"Item\\ObjectComponents\\{kind}"
    uniq = sorted(set(slots.values()), key=lambda f: min(s for s, x in slots.items() if x == f))
    name = item["name"]
    tex_stem = item.get("tex_stem", name)       # shared by L/R shoulders and the 16 helm variants so the BLP is stored once
    path_of = {x: base_dir + chr(92) + tex_stem + ("" if len(uniq) == 1 else f"_{i}") + ".blp" for i, x in enumerate(uniq)}
    tex_internal = {s: path_of[x] for s, x in slots.items()}
    md20, stats = ric.build(info, name, tex_internal, name)
    ric.validate_output(md20, info)
    tex_bytes = 0
    tex_meta = []
    for x in uniq:
        blp = ric.read_cache(cache, x)
        if blp[:4] != b"BLP2":
            raise RuntimeError(f"texture {x} is not BLP2")
        enc, alpha, atype, mips = blp[8], blp[9], blp[10], blp[11]
        w, h = struct.unpack_from("<II", blp, 12)
        p = payload / Path(path_of[x].replace("\\", "/"))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(blp)
        tex_bytes += len(blp)
        tex_meta.append({"fdid": x, "w": w, "h": h, "enc": enc, "alpha": alpha, "alpha_type": atype, "mips": mips, "bytes": len(blp)})
    mp = payload / Path((base_dir + chr(92) + name + ".m2").replace("\\", "/"))
    mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_bytes(md20)
    return {"ok": True, "model_fdid": fd, "skin_fdid": skin_id, "name": name, "kind": kind, "verts": stats["vertices"],
            "tris": stats["triangles"], "model_bytes": len(md20), "textures": tex_meta, "texture_bytes": tex_bytes,
            "relaxations": relax, "scale": round(scale, 4), "offset": [round(v, 4) for v in item["offset"]] if item.get("offset") else None, "model_sha256": ric.sha256(md20),
            "attachments": stats.get("attachments"), "source_model": getattr(pick, "chosen", None) and list(pick.chosen)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--picks", type=Path, required=True)
    ap.add_argument("--cache", type=Path, required=True)
    ap.add_argument("--listfile", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--profile", default="retail", choices=("retail", "forever"))
    ap.add_argument("--scales", type=Path, help="scales.json from gear_scales.py (id -> scale matching the native model's size)")
    a = ap.parse_args()
    out = a.out_dir.resolve()
    if out.exists():
        raise SystemExit(f"refusing to overwrite {out}")
    payload = out / "payload"
    payload.mkdir(parents=True)
    spec_ = json.loads(a.picks.read_text(encoding="utf8"))
    if a.scales:
        sc = json.loads(a.scales.read_text(encoding="utf8"))
        for it in spec_["items"]:
            it["scale"] = (sc.get(it["id"]) or {}).get("scale")
            it["offset"] = (sc.get(it["id"]) or {}).get("offset")
    results, edits = [], []
    for it in spec_["items"]:
        try:
            r = convert_one(it, a.cache, a.listfile, a.profile, payload)
        except Exception as exc:  # noqa: BLE001
            r = {"ok": False, "model_fdid": it["fdid"], "name": it["name"], "kind": it["kind"], "error": f"{type(exc).__name__}: {exc}"}
        r["id"] = it["id"]
        r["displays"] = it.get("displays", [])
        r["note"] = it.get("note", "")
        results.append(r)
        print(("OK  " if r["ok"] else "FAIL"), it["id"], r.get("verts", ""), r.get("tris", ""), r.get("relaxations", r.get("error", "")), flush=True)
        if r["ok"]:
            for d in it.get("displays", []):
                edits.append({"display": d, "new_basename": it["name"] + ".mdx", "kind": it["kind"], "id": it["id"], "column": it.get("column", 1)})
    done = {r["id"] for r in results if r["ok"]}
    for e in spec_.get("edits", []):
        if all(x in done for x in e["requires"]):
            edits.append({k: v for k, v in e.items() if k != "requires"})
    ok = [r for r in results if r["ok"]]
    summary = {"batch": spec_.get("batch"), "items": len(results), "ok": len(ok), "failed": len(results) - len(ok),
               "verts_total": sum(r["verts"] for r in ok), "tris_total": sum(r["tris"] for r in ok),
               "texture_bytes_total": sum(r["texture_bytes"] for r in ok), "model_bytes_total": sum(r["model_bytes"] for r in ok),
               "max_tris": max((r["tris"] for r in ok), default=0), "installed": False}
    (out / "gear-report.json").write_text(json.dumps({"summary": summary, "results": results}, indent=1), encoding="utf8")
    (out / "display-edits.json").write_text(json.dumps(edits, indent=1), encoding="utf8")
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
