#!/usr/bin/env python3
"""Census of the shipped MPQ chain listing (mpq-inspect list output) by top-level folder and extension."""
import collections
import re
import sys

BS = chr(92)


def load(path):
    rows = []
    for line in open(path, encoding="utf8", errors="replace"):
        m = re.match(r"\s*(\d+)\s+(.*)$", line.rstrip("\n"))
        if m:
            rows.append((int(m.group(1)), m.group(2)))
    return rows


def main(path):
    rows = load(path)
    print("files", len(rows), "bytes", sum(s for s, _ in rows))
    tot = collections.defaultdict(lambda: [0, 0])
    sub = collections.defaultdict(lambda: [0, 0])
    ext = collections.defaultdict(lambda: [0, 0])
    for s, p in rows:
        parts = p.split(BS)
        tot[parts[0]][0] += 1
        tot[parts[0]][1] += s
        if len(parts) > 2:
            k = parts[0] + BS + parts[1]
            sub[k][0] += 1
            sub[k][1] += s
        e = p.rsplit(".", 1)[-1].lower() if "." in p else ""
        ext[e][0] += 1
        ext[e][1] += s
    for t, (n, s) in sorted(tot.items(), key=lambda x: -x[1][1]):
        print(f"{t:22} {n:7} {s/1e6:9.1f}MB")
    print("--- ext")
    for e, (n, s) in sorted(ext.items(), key=lambda x: -x[1][0])[:30]:
        print(f"{e:8} {n:7} {s/1e6:9.1f}MB")
    print("--- sub (Character/Creature/Item/Spells/World/Interface/Sound top)")
    for k, (n, s) in sorted(sub.items()):
        if k.split(BS)[0] in ("Character", "Item", "Spells", "Environment"):
            print(f"{k:50} {n:6} {s/1e6:8.1f}MB")
    cs = [(k, v) for k, v in sub.items() if k.startswith("Creature" + BS)]
    print("creature folders", len(cs), "files", sum(v[0] for _, v in cs))
    ws = [(k, v) for k, v in sub.items() if k.startswith("World" + BS)]
    for k, v in sorted(ws):
        print(f"{k:50} {v[0]:6} {v[1]/1e6:8.1f}MB")


if __name__ == "__main__":
    main(sys.argv[1])
