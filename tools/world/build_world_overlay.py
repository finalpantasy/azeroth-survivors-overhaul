#!/usr/bin/env python3
"""W10: build the HD world payload (doodads, textures, WMOs) from the pinned VanillaHD Patch-B archive.

Everything is selected so the engine can load it unchanged:
  * M2: classic MD20 v256 only, same path as a file the game already ships (same-name overlay).
  * WMO: v17 only; a Patch-B building replaces a native one when the path matches, or when Patch-B's copy is the
    same name with a leading underscore on the root and its groups (`_X.wmo`, `_X_000.wmo` -> `X.wmo`, `X_000.wmo`)
    and the group counts are equal.
  * BLP: every Patch-B texture whose path the game already ships, plus textures the chosen M2/WMO name that the game
    lacks and Patch-B has.
Reads the owner's local copy of the Patch-B GitHub archive; writes a loose payload tree + report. Never touches the
installed game. Output paths use the game's own casing.
Usage: python -I build_world_overlay.py --zip <Patch-B.zip> --out <dir> [--no-wmo] [--no-m2]
"""
import argparse, hashlib, json, re, struct, subprocess, sys, zipfile
from pathlib import Path

ROOT = Path(r".")
WORK = ROOT / "analysis" / "world"
BS = chr(92)
NUL = bytes(1)


def chain_casing():
    d = {}
    for l in open(WORK / "chain-list.txt", encoding="utf8", errors="replace"):
        m = re.match(r"\s*(\d+)\s+(.*)", l.rstrip("\n"))
        if m:
            d[m.group(2).lower()] = (m.group(2), int(m.group(1)))
    return d


def m2_textures(b):
    nt, ot = struct.unpack_from("<II", b, 0x5C)
    out = []
    for i in range(nt):
        if ot + 16 * i + 16 > len(b):
            break
        ty, fl, ln, of = struct.unpack_from("<IIII", b, ot + 16 * i)
        if ty == 0 and ln:
            out.append(b[of:of + ln].rstrip(NUL).decode("latin1"))
    return out


def wmo_chunks(b):
    i = 0
    while i + 8 <= len(b):
        tag = b[i:i + 4][::-1].decode("latin1")
        size = struct.unpack_from("<I", b, i + 4)[0]
        yield tag, b[i + 8:i + 8 + size]
        i += 8 + size


def wmo_root_info(b):
    info = {"groups": None, "textures": []}
    for tag, blob in wmo_chunks(b):
        if tag == "MOHD":
            info["groups"] = struct.unpack_from("<I", blob, 4)[0]
        elif tag == "MOTX":
            info["textures"] = [s.decode("latin1") for s in blob.split(NUL) if s]
    return info


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--no-wmo", action="store_true")
    ap.add_argument("--no-m2", action="store_true")
    a = ap.parse_args()
    out = Path(a.out)
    if out.exists() and any(out.iterdir()):
        sys.exit("output dir must be new/empty: %s" % out)
    ch = chain_casing()
    z = zipfile.ZipFile(a.zip)
    pb = {}
    for i in z.infolist():
        if i.is_dir():
            continue
        p = i.filename.split("/", 1)[1].replace("/", BS)
        pb[p.lower()] = (p, i)
    chosen = {}      # target path (game casing) -> (source key, kind)
    skipped = []
    texnames = set()

    def put(target_lower, src_lower, kind, data=None):
        game_path = ch[target_lower][0]
        chosen[game_path] = {"src": pb[src_lower][0], "kind": kind}

    if not a.no_m2:
        for k, (p, i) in pb.items():
            if not k.endswith(".m2") or k not in ch:
                continue
            b = z.read(i)
            if b[:4] != b"MD20" or struct.unpack_from("<I", b, 4)[0] != 256:
                skipped.append({"path": p, "why": "not MD20 v256"})
                continue
            tl = [t.lower().replace("/", BS) for t in m2_textures(b)]
            gone = [t for t in tl if t not in pb and t not in ch]
            if gone:
                skipped.append({"path": p, "why": "texture not in Patch-B or game: " + gone[0]})
                continue
            put(k, k, "m2")
            texnames.update(tl)
    wmo_pairs = []
    if not a.no_wmo:
        roots = [k for k in pb if k.endswith(".wmo") and not re.search(r"_\d{3}\.wmo$", k)]
        for k in roots:
            dirp, base = k.rsplit(BS, 1)
            cands = [(k, k)] if k in ch else []
            if base.startswith("_") and (dirp + BS + base[1:]) in ch:
                cands.append((dirp + BS + base[1:], k))
            for target, src in cands:
                sb = z.read(pb[src][1])
                if sb[:4] != b"REVM" or struct.unpack_from("<I", sb, 8)[0] != 17:
                    skipped.append({"path": pb[src][0], "why": "not WMO v17"}); continue
                si = wmo_root_info(sb)
                # native group count: read native root from the installed chain
                nat = WORK / "native-cache" / ch[target][0].replace(BS, "/")
                if not nat.exists():
                    nat.parent.mkdir(parents=True, exist_ok=True)
                    subprocess.run([str(ROOT / "tools" / "mpq-inspect.exe"), str(ROOT / "Data"), "extract", ch[target][0], str(nat)], capture_output=True)
                ni = wmo_root_info(nat.read_bytes()) if nat.exists() else {"groups": None}
                n = si["groups"]
                stem_src = src[:-4]; stem_tgt = target[:-4]
                grp = [(stem_src + "_%03d.wmo" % g, stem_tgt + "_%03d.wmo" % g) for g in range(n or 0)]
                gone = [t for t in (x.lower().replace("/", BS) for x in si["textures"]) if t not in pb and t not in ch]
                ok = n and n == ni["groups"] and all(s in pb and t in ch for s, t in grp) and not gone
                rec = {"root": ch[target][0], "src": pb[src][0], "groups_new": n, "groups_native": ni["groups"], "accepted": bool(ok), "missing_textures": gone}
                wmo_pairs.append(rec)
                if not ok:
                    continue
                put(target, src, "wmo-root")
                for s, t in grp:
                    put(t, s, "wmo-group")
                texnames.update(t.lower() for t in si["textures"])
                break
    # textures: every Patch-B BLP the game already has, plus textures named by chosen models that Patch-B has
    missing_everywhere = []
    for k, (p, i) in pb.items():
        if k.endswith(".blp") and k in ch and not k.startswith("tileset" + BS):
            put(k, k, "blp")
    for t in sorted(texnames):
        t = t.replace("/", BS)
        if t in ch and ch[t][0] in chosen:
            continue
        if t in pb:
            if t in ch:
                put(t, t, "blp")
            else:
                chosen[pb[t][0]] = {"src": pb[t][0], "kind": "blp-new"}
        elif t not in ch:
            missing_everywhere.append(t)
    # write payload
    total = 0
    files = []
    for tgt, meta in sorted(chosen.items(), key=lambda kv: kv[0].lower()):
        i = pb[meta["src"].lower()][1]
        data = z.read(i)
        dest = out / tgt.replace(BS, "/")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        total += len(data)
        files.append({"path": tgt, "kind": meta["kind"], "src": meta["src"], "bytes": len(data),
                      "sha256": hashlib.sha256(data).hexdigest(), "git_blob_sha1": hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest(),
                      "native_bytes": ch.get(tgt.lower(), (None, None))[1]})
    rep = {"source_zip": str(a.zip), "files": len(files), "bytes": total, "skipped": skipped,
           "wmo_pairs": wmo_pairs, "textures_missing_everywhere": missing_everywhere, "manifest": files}
    json.dump(rep, open(out.parent / (out.name + "-report.json"), "w"), indent=1)
    import collections
    c = collections.Counter(f["kind"] for f in files)
    print(dict(c), "bytes", total, "skipped", len(skipped), "missing textures", len(missing_everywhere))
    print("wmo pairs", [(w["root"].split(BS)[-1], w["groups_new"], w["groups_native"], w["accepted"]) for w in wmo_pairs])


if __name__ == "__main__":
    main()
