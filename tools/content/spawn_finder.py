"""Rank spawn points per zone: inside the zone (AreaTable parent), terrain shipped, no pruned WMO within
the stream radius (else the loading screen hangs, see adt_placements.py), and flat ground.
Usage: python spawn_finder.py <Data dir> <chain-list.txt> <AreaTable.csv> <map> <tx0> <tx1> <ty0> <ty1> <zone id> [<zone id>...]
Prints per zone the 5 flattest clear points: roughness (yd over 80 yd), clearance (yd), x, y, subzone, z.
"""
import csv, struct, sys
from adt_placements import adt, placements, load_files, exists, TILE
from adt_height import height

RADIUS = 660  # lab: a pruned WMO at 536 yd blocked loading, 634 yd did not; keep a margin


def scan_tiles(data, files, mapn, txs, tys):
    chunks, miss, present = {}, [], set()
    for tx in txs:
        for ty in tys:
            d = adt(data, mapn, tx, ty)
            if d is None: continue
            present.add((tx, ty)); o = 0
            while o + 8 <= len(d):
                n = struct.unpack_from('<I', d, o + 4)[0]
                if d[o:o + 4] == b'KNCM':
                    h = o + 8; px, py = struct.unpack_from('<2f', d, h + 0x68)
                    chunks[(int(px // (TILE / 16)), int(py // (TILE / 16)))] = (px, py, struct.unpack_from('<I', d, h + 0x34)[0])
                o += 8 + n
            for k, p, x, y, z in placements(d):
                if k == 'wmo' and not exists(files, p): miss.append((x, y))
    return chunks, miss, present


def area_at(chunks, x, y):
    for kx in (int(x // (TILE / 16)), int(x // (TILE / 16)) + 1):
        for ky in (int(y // (TILE / 16)), int(y // (TILE / 16)) + 1):
            c = chunks.get((kx, ky))
            if c and c[0] - TILE / 16 <= x <= c[0] and c[1] - TILE / 16 <= y <= c[1]: return c[2]


def spots(data, mapn, chunks, miss, present, zone, parent, step=50):
    out = []
    xs = [c[0] for c in chunks.values()]; ys = [c[1] for c in chunks.values()]
    for x in range(int(min(xs)), int(max(xs)), step):
        for y in range(int(min(ys)), int(max(ys)), step):
            a = area_at(chunks, x, y)
            if a is None or (a != zone and parent.get(a) != zone): continue
            if not all((int(32 - (y + dy) / TILE), int(32 - (x + dx) / TILE)) in present
                       for dx in (-RADIUS, 0, RADIUS) for dy in (-RADIUS, 0, RADIUS)): continue
            near = min((((mx - x) ** 2 + (my - y) ** 2) ** .5 for mx, my in miss), default=9999)
            if near < RADIUS: continue
            out.append((x, y, a, round(near)))
    return out


def rough(data, mapn, x, y):
    try:
        hs = [height(data, mapn, x + dx, y + dy)[0] for dx in (-40, 0, 40) for dy in (-40, 0, 40)]
    except Exception:
        return None
    if None in hs: return None
    return round(max(hs) - min(hs), 1), round(hs[4], 1)


if __name__ == '__main__':
    data, listing, at, mapn = sys.argv[1:5]
    tx0, tx1, ty0, ty1 = map(int, sys.argv[5:9])
    zones = list(map(int, sys.argv[9:]))
    rows = list(csv.DictReader(open(at, encoding='utf8')))
    parent = {int(r['ID']): int(r['ParentAreaID']) for r in rows}
    name = {int(r['ID']): r['AreaName'] for r in rows}
    chunks, miss, present = scan_tiles(data, load_files(listing), mapn, range(tx0, tx1 + 1), range(ty0, ty1 + 1))
    for zone in zones:
        cand = spots(data, mapn, chunks, miss, present, zone, parent)
        cand.sort(key=lambda c: -c[3])
        ranked = []
        for x, y, a, near in cand[:400]:
            r = rough(data, mapn, x, y)
            if r: ranked.append((r[0], near, x, y, name.get(a, a), r[1]))
        ranked.sort()
        print(f'{zone} {name.get(zone)}: {len(cand)} clear points')
        for r in ranked[:5]: print('  ', r)
