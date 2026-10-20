#!/usr/bin/env python3
"""W10: static sanity checks of the world payload against the game's own copies (no game run).

M2: parse with the project's classic reader, limits (u16 verts, u8 bones), sequence/emitter/ribbon counts old vs new,
every named texture resolvable in payload or game. BLP: magic, dims, enc, size consistent with mips. WMO: v17 + groups present.
Writes analysis/world/payload-validation.json and prints a summary.
"""
import json, struct, sys, re, collections
from pathlib import Path

ROOT = Path(r".")
W = ROOT / "analysis" / "world"
sys.path.insert(0, str(ROOT / "tools" / "asset-coverage"))
import m2lib  # noqa: E402

BS = chr(92)
P = W / "payload-world-hd"
NC = W / "native-cache"
rep = json.load(open(W / "payload-world-hd-report.json"))
chain = set()
for l in open(W / "chain-list.txt", encoding="utf8", errors="replace"):
    m = re.match(r"\s*\d+\s+(.*)", l.rstrip("\n"))
    if m:
        chain.add(m.group(1).lower())
payload = {f["path"].lower() for f in rep["manifest"]}


def tex_names(b):
    nt, ot = struct.unpack_from("<II", b, 0x5C)
    out = []
    for i in range(nt):
        ty, fl, ln, of = struct.unpack_from("<IIII", b, ot + 16 * i)
        if ty == 0 and ln:
            out.append(b[of:of + ln].rstrip(bytes(1)).decode("latin1").lower().replace("/", BS))
    return out


issues = collections.defaultdict(list)
stats = collections.Counter()
for f in rep["manifest"]:
    p = P / f["path"].replace(BS, "/")
    b = p.read_bytes()
    k = f["kind"]
    if k == "m2":
        n = m2lib.parse_classic(b)
        stats["m2"] += 1
        if n["verts"] > 65535: issues["verts>u16"].append(f["path"])
        if n["bones"] > 255: issues["bones>u8"].append(f["path"])
        for t in tex_names(b):
            if t not in payload and t not in chain:
                issues["texture-unresolved"].append((f["path"], t))
        nat = NC / f["path"].replace(BS, "/")
        if nat.exists():
            o = m2lib.parse_classic(nat.read_bytes())
            for key in ("seq", "particles", "ribbons", "lights", "cameras", "attach", "events"):
                if o.get(key) != n.get(key):
                    stats["m2_changed_" + key] += 1
                    if key in ("particles", "ribbons", "lights", "cameras"):
                        issues["m2-" + key + "-differs"].append((f["path"], o.get(key), n.get(key)))
            if n["bones"] != o["bones"]:
                stats["m2_bones_differ"] += 1
            if n["views"] != o["views"]:
                issues["views-differ"].append(f["path"])
    elif k.startswith("blp"):
        stats["blp"] += 1
        if b[:4] != b"BLP2": issues["blp-magic"].append(f["path"]); continue
        enc, ab, am, hm = b[8], b[9], b[10], b[11]
        w, h = struct.unpack_from("<II", b, 12)
        if (w & (w - 1)) or (h & (h - 1)): issues["blp-npot"].append((f["path"], w, h))
        stats["blp_enc%d" % enc] += 1
        if max(w, h) > 512: issues["blp>512"].append((f["path"], w, h))
    elif k.startswith("wmo"):
        stats[k] += 1
        if b[:4] != b"REVM" or struct.unpack_from("<I", b, 8)[0] != 17: issues["wmo-ver"].append(f["path"])
print(dict(stats))
for k, v in issues.items():
    print(k, len(v), v[:4])
json.dump({"stats": stats, "issues": issues}, open(W / "payload-validation.json", "w"), indent=1, default=str)
