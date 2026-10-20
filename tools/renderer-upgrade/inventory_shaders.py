#!/usr/bin/env python3
"""Inventory every embedded WGSL shader in a pinned Azeroth Survivors executable (read-only).

Method:
  1. Exact-match every known source file (public wenilla 6934a85 shaders and Bevy 0.18.1 crate
     shaders from the local cargo registry) against the EXE bytes, LF and CRLF.
  2. Find every remaining WGSL-looking text run (around `@vertex`/`@fragment`/`@compute`/
     `#define_import_path`/`@group(`) not covered by an exact match and report it as
     native-modified or unknown, with its nearest known source by shared prefix/suffix.
  3. For each blob: entry points, @group/@binding declarations, imports, shader-def tests.
  4. For each blob start: locate references (RIP-relative LEA in .text, absolute pointer in
     data sections) so the patch method (fixed slot vs relocatable) can be decided.

Usage: python -I inventory_shaders.py <exe> <wenilla_src_dir> <cargo_registry_src_dir> <out.json>
"""

from __future__ import annotations

import hashlib
import json
import re
import struct
import sys
from pathlib import Path

TEXT_BYTES = set(range(0x20, 0x7F)) | {0x09, 0x0A, 0x0D} | set(range(0x80, 0x100))
MARKERS = [b"@vertex", b"@fragment", b"@compute", b"#define_import_path", b"@group("]


def pe_sections(data: bytes):
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    nsec = struct.unpack_from("<H", data, pe + 6)[0]
    opt = struct.unpack_from("<H", data, pe + 20)[0]
    image_base = struct.unpack_from("<Q", data, pe + 24 + 24)[0]
    secs = []
    base = pe + 24 + opt
    for i in range(nsec):
        o = base + 40 * i
        name = data[o:o + 8].rstrip(b"\0").decode()
        vsize, va, rsize, raw = struct.unpack_from("<IIII", data, o + 8)
        secs.append({"name": name, "va": va, "vsize": vsize, "raw": raw, "rsize": rsize})
    return image_base, secs


def off_to_rva(secs, off):
    for s in secs:
        if s["raw"] <= off < s["raw"] + s["rsize"]:
            return s["va"] + off - s["raw"], s["name"]
    return None, None


def find_all(data: bytes, needle: bytes):
    out, i = [], data.find(needle)
    while i != -1:
        out.append(i)
        i = data.find(needle, i + 1)
    return out


def known_sources(wenilla: Path, registry: Path):
    items = []
    for p in sorted(wenilla.rglob("*.wgsl")):
        items.append(("wenilla:" + p.relative_to(wenilla).as_posix(), p))
    for crate in sorted(registry.glob("bevy*-0.18.1")):
        for p in sorted(crate.rglob("*.wgsl")):
            items.append(("bevy:" + p.relative_to(registry).as_posix(), p))
    return items


def analyze(text: str) -> dict:
    entries = re.findall(r"@(vertex|fragment|compute)(?:\s*@workgroup_size\([^)]*\))?\s*fn\s+(\w+)", text)
    bindings = []
    for m in re.finditer(r"@group\((\d+)\)\s*@binding\((\d+)\)\s*var(<[^>]*>)?\s+(\w+)\s*:\s*([^;]+);", text):
        bindings.append({"group": int(m.group(1)), "binding": int(m.group(2)),
                         "space": (m.group(3) or "").strip("<>"), "name": m.group(4),
                         "type": " ".join(m.group(5).split())})
    imports = sorted(set(re.findall(r"#import\s+([\w:]+)", text)))
    import_path = re.findall(r"#define_import_path\s+([\w:]+)", text)
    defs = sorted(set(re.findall(r"#(?:ifdef|ifndef|if)\s+(\w+)", text)))
    return {"entry_points": [f"{k}:{n}" for k, n in entries], "bindings": bindings,
            "imports": imports, "define_import_path": import_path[0] if import_path else None,
            "shader_defs_tested": defs}


_LEA = {}


def LEA_INDEX(data, secs):
    """Map target RVA -> [(insn_rva, file_offset)] for every `lea r64,[rip+disp32]` in .text."""
    if _LEA:
        return _LEA
    text = next(s for s in secs if s["name"] == ".text")
    t0, t1 = text["raw"], text["raw"] + text["rsize"]
    for m in re.finditer(rb"[\x48\x4c]\x8d[\x05\x0d\x15\x1d\x25\x2d\x35\x3d]", data[t0:t1]):
        i = m.start()
        if t0 + i + 7 > t1:
            continue
        disp = struct.unpack_from("<i", data, t0 + i + 3)[0]
        ins_rva = text["va"] + i
        _LEA.setdefault(ins_rva + 7 + disp, []).append((ins_rva, t0 + i))
    return _LEA


def references(data: bytes, image_base: int, secs, off: int, length: int):
    rva, _ = off_to_rva(secs, off)
    va = image_base + rva
    text = next(s for s in secs if s["name"] == ".text")
    t0, t1 = text["raw"], text["raw"] + text["rsize"]
    leas = []
    for ins_rva, foff in LEA_INDEX(data, secs).get(rva, []):
        window = data[foff + 7:foff + 67]
        pat = struct.pack("<I", length)
        leas.append({"insn_va": hex(image_base + ins_rva), "file_offset": foff,
                     "length_imm_within_next_60_bytes": window.find(pat) if pat in window else None})
    ptrs = []
    for s in secs:
        if s["name"] in (".rdata", ".data"):
            seg = data[s["raw"]:s["raw"] + s["rsize"]]
            for j in find_all(seg, struct.pack("<Q", va)):
                nxt = struct.unpack_from("<Q", seg, j + 8)[0] if j + 16 <= len(seg) else None
                ptrs.append({"section": s["name"], "file_offset": s["raw"] + j,
                             "next_qword": nxt, "is_ptr_len_pair": nxt == length})
    return {"va": hex(va), "lea_refs": leas, "pointer_refs": ptrs}


def main():
    exe, wen, reg, out = map(Path, sys.argv[1:5])
    data = exe.read_bytes()
    image_base, secs = pe_sections(data)
    sha = hashlib.sha256(data).hexdigest()
    covered = []
    matches = []
    for name, p in known_sources(wen, reg):
        raw = p.read_bytes()
        lf = raw.replace(b"\r\n", b"\n")
        crlf = lf.replace(b"\n", b"\r\n")
        found = []
        for variant, blob in (("LF", lf), ("CRLF", crlf)):
            if blob == crlf and lf == crlf:
                continue
            for o in find_all(data, blob):
                found.append({"line_endings": variant, "offset": o, "length": len(blob)})
                covered.append((o, o + len(blob)))
        matches.append({"source": name, "bytes_lf": len(lf),
                        "sha256_lf": hashlib.sha256(lf).hexdigest(), "exe_matches": found})

    def is_covered(o):
        return any(a <= o < b for a, b in covered)

    # Unmatched WGSL runs.
    runs = {}
    for mk in MARKERS:
        for o in find_all(data, mk):
            if is_covered(o):
                continue
            a = o
            while a > 0 and data[a - 1] in TEXT_BYTES:
                a -= 1
            b = o
            while b < len(data) and data[b] in TEXT_BYTES:
                b += 1
            runs[(a, b)] = True
    unknown = []
    lf_sources = [(m["source"], (wen if m["source"].startswith("wenilla:") else reg)) for m in matches]
    src_texts = {}
    for name, p in known_sources(wen, reg):
        src_texts[name] = p.read_bytes().replace(b"\r\n", b"\n")
    for (a, b) in sorted(runs):
        blob = data[a:b]
        text = blob.decode("utf-8", "replace").replace("\r\n", "\n")
        # Nearest known source by longest common prefix and suffix (normalised LF).
        best = None
        bl = blob.replace(b"\r\n", b"\n")
        for name, s in src_texts.items():
            pre = 0
            while pre < min(len(s), len(bl)) and s[pre] == bl[pre]:
                pre += 1
            idx = bl.find(s[-200:]) if len(s) > 200 else -1
            score = pre + (200 if idx != -1 else 0)
            if best is None or score > best[0]:
                best = (score, name, pre, idx)
        unknown.append({"offset": a, "length": b - a, "crlf": b"\r\n" in blob,
                        "first_line": text.strip().splitlines()[0][:120] if text.strip() else "",
                        "nearest_source": best[1] if best else None,
                        "common_prefix_bytes": best[2] if best else 0,
                        "source_suffix_found_at": best[3] if best else -1,
                        "analysis": analyze(text)})
    for m in matches:
        if m["exe_matches"]:
            o = m["exe_matches"][0]
            t = data[o["offset"]:o["offset"] + o["length"]].decode("utf-8", "replace")
            m["analysis"] = analyze(t)
            m["refs"] = references(data, image_base, secs, o["offset"], o["length"])
    for u in unknown:
        u["refs"] = references(data, image_base, secs, u["offset"], u["length"])
    report = {"exe": str(exe), "exe_sha256": sha, "exe_size": len(data), "image_base": hex(image_base),
              "sections": secs,
              "known_sources_total": len(matches),
              "known_sources_embedded_exact": sum(1 for m in matches if m["exe_matches"]),
              "matches": matches, "unmatched_wgsl_runs": unknown}
    out.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(f"sha {sha[:16]} known {len(matches)} exact {report['known_sources_embedded_exact']} "
          f"unmatched_runs {len(unknown)}")


if __name__ == "__main__":
    main()
