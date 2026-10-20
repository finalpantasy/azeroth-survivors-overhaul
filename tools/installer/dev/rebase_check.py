#!/usr/bin/env python3
"""Rebase check of EXE recipes against a new official EXE (read-only; writes only the JSON report).

For every `exe_patches[*].edits[*]` of every recipe it reads the original bytes from the OLD official EXE (verified by the
recipe's expect_sha256), then looks for the same bytes, with up to CTX bytes of context on each side, in the NEW EXE and reports
whether the anchor is found uniquely and how far it moved. Also summarizes both PE headers (sections), because edits that
rewrite PE fields or append a section depend on the layout.

    python rebase_check.py --old <exe 0.103.0> --new <exe 0.103.1> --out report.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path

HERE = Path(__file__).resolve().parent
RECIPES = HERE.parent / "recipes"
CTX = 64


def pe_summary(b: bytes) -> dict:
    e_lfanew = struct.unpack_from("<I", b, 0x3C)[0]
    nsec, = struct.unpack_from("<H", b, e_lfanew + 6)
    opt_size, = struct.unpack_from("<H", b, e_lfanew + 20)
    opt = e_lfanew + 24
    out = {"e_lfanew": e_lfanew, "sections_n": nsec, "size_of_image": struct.unpack_from("<I", b, opt + 56)[0],
           "size_of_headers": struct.unpack_from("<I", b, opt + 60)[0], "entry_rva": struct.unpack_from("<I", b, opt + 16)[0],
           "checksum": struct.unpack_from("<I", b, opt + 64)[0], "sections": []}
    sec = opt + opt_size
    for i in range(nsec):
        name, vsize, va, rsize, rptr = struct.unpack_from("<8sIIII", b, sec + 40 * i)
        out["sections"].append({"name": name.rstrip(b"\0").decode("ascii", "replace"), "vsize": vsize, "va": va, "raw_size": rsize, "raw_ptr": rptr})
    return out


def find_all(hay: bytes, needle: bytes, limit: int = 4) -> list[int]:
    res, start = [], 0
    while len(res) < limit:
        i = hay.find(needle, start)
        if i < 0:
            break
        res.append(i)
        start = i + 1
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--old", type=Path, required=True)
    ap.add_argument("--new", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--recipes", type=Path, default=RECIPES)
    ap.add_argument("--edits", type=Path, help="JSON list of {name, offset, length} against the OLD exe (any patch, not only installer recipes): "
                                              "prints where the same bytes (with context) sit in the NEW exe")
    a = ap.parse_args()
    old, new = a.old.read_bytes(), a.new.read_bytes()
    rep: dict = {"old_sha256": hashlib.sha256(old).hexdigest(), "new_sha256": hashlib.sha256(new).hexdigest(),
                 "old_bytes": len(old), "new_bytes": len(new), "pe_old": pe_summary(old), "pe_new": pe_summary(new), "recipes": {}}
    if a.edits:
        rows = []
        for e in json.loads(a.edits.read_text(encoding="utf-8")):
            off, ln = e["offset"], e["length"]
            row = {"name": e.get("name"), "offset": off, "length": ln}
            for c in (CTX, 32, 16, 12):
                seg = old[max(0, off - c):off + ln + c]
                hits = [h + (off - max(0, off - c)) for h in find_all(new, seg)]
                if len(hits) == 1:
                    row.update(new_offset=hits[0], shift=hits[0] - off, context=c, status="found")
                    break
            else:
                row["status"] = "NOT FOUND/AMBIGUOUS (code with moved RIP-relative targets, or header/section fields)"
            rows.append(row)
        rep["edits"] = rows
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(rep, indent=1) + "\n", encoding="utf-8")
        print(json.dumps(rows))
        return 0
    for f in sorted(a.recipes.glob("*.json")):
        r = json.loads(f.read_text(encoding="utf-8"))
        patches = r.get("outputs", {}).get("exe_patches", [])
        if not patches:
            continue
        entry = {"base_ok": rep["old_sha256"] == patches[0]["expect_exe_sha256"], "edits": [], "append_bytes": 0}
        for p in patches:
            entry["append_bytes"] += len(p.get("append_b64", "")) * 3 // 4
            for e in p["edits"]:
                off, ln = e["offset"], e["length"]
                orig = old[off:off + ln]
                ok = hashlib.sha256(orig).hexdigest() == e["expect_sha256"]
                ctx_lo, ctx_hi = max(0, off - CTX), min(len(old), off + ln + CTX)
                exact = find_all(new, orig) if ln >= 16 else []
                ctxm = find_all(new, old[ctx_lo:ctx_hi])
                row = {"offset": off, "length": ln, "old_bytes_match_recipe": ok, "exact_matches": exact if ln >= 16 else None,
                       "context_matches": ctxm, "context_len": ctx_hi - ctx_lo}
                if len(ctxm) == 1:
                    row["new_offset"] = ctxm[0] + (off - ctx_lo)
                    row["shift"] = row["new_offset"] - off
                    row["status"] = "re-anchorable"
                elif len(exact) == 1:
                    row["new_offset"] = exact[0]
                    row["shift"] = exact[0] - off
                    row["status"] = "re-anchorable (context differs)"
                else:
                    row["status"] = "NOT FOUND" if not ctxm and not exact else "AMBIGUOUS"
                entry["edits"].append(row)
        entry["summary"] = {s: sum(1 for x in entry["edits"] if x["status"].startswith(s)) for s in ("re-anchorable", "NOT FOUND", "AMBIGUOUS")}
        rep["recipes"][r["id"]] = entry
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(rep, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({k: v["summary"] for k, v in rep["recipes"].items()}), json.dumps(rep["pe_new"]["sections"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
