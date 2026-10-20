#!/usr/bin/env python3
"""Join every native shipped path (mpq-list) to the community listfile by exact lower-case path.

Output: native-to-fdid.csv (native_path;bytes;fdid;ext) and unmatched.csv; summary printed per top-level class.
The legacy name usually keeps its original FileDataID in modern clients, which is what makes membership probing possible.
"""
import argparse
import collections
import re
import sys

BS = chr(92)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", required=True)
    ap.add_argument("--listfile", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    names = collections.defaultdict(list)
    for line in open(a.listfile, encoding="utf8"):
        fid, _, nm = line.rstrip("\n").partition(";")
        names[nm].append(int(fid))
    seen = set()
    stat = collections.defaultdict(lambda: [0, 0])
    with open(a.out, "w", encoding="utf8") as o, open(a.out.replace(".csv", "-unmatched.csv"), "w", encoding="utf8") as u:
        o.write("native_path;bytes;fdid;ext\n")
        for line in open(a.list, encoding="utf8", errors="replace"):
            m = re.match(r"\s*(\d+)\s+(.*)$", line.rstrip("\n"))
            if not m:
                continue
            size, path = int(m.group(1)), m.group(2)
            low = path.lower().replace(BS, "/")
            if low in seen:
                continue
            seen.add(low)
            ext = low.rsplit(".", 1)[-1] if "." in low else ""
            if ext not in ("m2", "blp", "wmo", "adt", "wdt", "skin"):
                continue
            cls = low.split("/")[0]
            key = (cls, ext)
            stat[key][0] += 1
            ids = names.get(low)
            if not ids:
                u.write(f"{low};{size}\n")
                continue
            stat[key][1] += 1
            o.write(f"{low};{size};{ids[0]};{ext}\n")
    for (cls, ext), (n, mt) in sorted(stat.items()):
        if n >= 20:
            print(f"{cls:14} {ext:4} native {n:6} listfile-matched {mt:6} ({100*mt/n:5.1f}%)")


if __name__ == "__main__":
    main()
