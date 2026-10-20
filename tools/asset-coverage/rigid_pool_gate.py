#!/usr/bin/env python3
"""Run the rigid_item_convert gate over a cached pool of retail item models; write rigid-pool.csv + a reason histogram.

Usage: rigid_pool_gate.py <cache-dir> <candidates.json> <ids-items-all.txt> <out.csv>
"""
import collections
import csv
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rigid_item_convert as R  # noqa: E402


def main():
    cache, cand, names_file, out = map(Path, sys.argv[1:5])
    names = {l.split(";")[0]: l.strip().split(";")[1] for l in names_file.read_text().splitlines() if ";" in l}
    reasons = collections.Counter()
    rows = []
    for fid in json.loads(cand.read_text()):
        name = names[fid]
        try:
            model = (cache / f"{fid}.bin").read_bytes()
            chunks = R.split_chunks(model)
            skin_id = struct.unpack_from("<I", chunks["SFID"], 0)[0]
            skin = (cache / f"{skin_id}.bin").read_bytes()
            info = R.inspect(model, skin)
            txid = list(struct.unpack(f"<{len(chunks['TXID']) // 4}I", chunks["TXID"])) if "TXID" in chunks else []
            used = sorted({info["tlookup"][b[8]] for b in info["batches"]})
            types = [info["tex"][s][0] for s in used]
            self_contained = all(info["tex"][s][0] == 0 and s < len(txid) and txid[s] for s in used)
            rows.append([fid, name, "pass", info["version"], info["vcount"], len(info["tris"]) // 3, len(info["sections"]),
                         len(info["batches"]), info["bones"], len(used), "|".join(map(str, types)), int(self_contained), 0])
            reasons["pass"] += 1
            reasons["pass_self_contained_textures" if self_contained else "pass_needs_replaceable_texture"] += 1
        except Exception as e:  # noqa: BLE001
            try:
                R.inspect(model, skin, True, True)
                reasons["relaxable_with_flags (texture animation drop / duplicate attachments)"] += 1
                relaxed = 1
            except Exception:  # noqa: BLE001
                relaxed = 0
            msg = str(e).split(":")[0][:60]
            reasons["fail: " + msg] += 1
            rows.append([fid, name, "fail: " + str(e)[:80], "", "", "", "", "", "", "", "", "", relaxed])
    with out.open("w", newline="", encoding="utf8") as f:
        w = csv.writer(f)
        w.writerow(["fdid", "path", "gate", "version", "verts", "tris", "sections", "batches", "bones", "tex_slots_used", "tex_types", "self_contained_textures", "relaxable_fail"])
        w.writerows(rows)
    for k, v in reasons.most_common():
        print(f"{v:6}  {k}")


if __name__ == "__main__":
    main()
