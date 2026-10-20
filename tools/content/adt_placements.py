"""List ADT doodad/WMO placements and flag the ones whose model is missing from the MPQ chain.
A spawn is only safe when no missing placement sits inside the stream radius: the loading screen
counts a failed placement as still pending and never clears (seen in the Westfall lab run, 2026-10-19).
Usage: python adt_placements.py <Data dir> <chain-list.txt> <map> <x> <y> [radius]
"""
import os, struct, subprocess, sys, tempfile

MPQ = os.path.join(os.path.dirname(__file__), '..', 'mpq-inspect.exe')
TILE = 1600 / 3
ORIGIN = 32 * TILE
BS = chr(92)


def adt(data, mapn, tx, ty):
    out = os.path.join(tempfile.gettempdir(), f'{mapn}_{tx}_{ty}.adt')
    if not os.path.exists(out):
        r = subprocess.run([MPQ, data, 'extract', f'World{BS}Maps{BS}{mapn}{BS}{mapn}_{tx}_{ty}.adt', out], capture_output=True)
        if r.returncode: return None
    return open(out, 'rb').read()


def names(blob):
    out, o = {}, 0
    for s in blob.split(b'\0'):
        out[o] = s.decode('latin1'); o += len(s) + 1
    return out


def placements(d):
    """Yield (kind, model path, world x, world y, z) for MDDF (m2) and MODF (wmo) entries."""
    ch, o = {}, 0
    while o + 8 <= len(d):
        tag, n = d[o:o + 4][::-1].decode('latin1'), struct.unpack_from('<I', d, o + 4)[0]
        if tag in ('MMDX', 'MWMO', 'MDDF', 'MODF'): ch[tag] = d[o + 8:o + 8 + n]
        o += 8 + n
    mm, mw = names(ch.get('MMDX', b'')), names(ch.get('MWMO', b''))
    mmid = list(mm.keys()); mwid = list(mw.keys())
    # MMID/MWID are offset tables; entries index them in file order, which matches the name order
    for i in range(0, len(ch.get('MDDF', b'')), 36):
        nid, _, px, pz, py = struct.unpack_from('<II3f', ch['MDDF'], i)
        if nid < len(mmid): yield 'm2', mm[mmid[nid]], ORIGIN - py, ORIGIN - px, pz
    for i in range(0, len(ch.get('MODF', b'')), 64):
        nid, _, px, pz, py = struct.unpack_from('<II3f', ch['MODF'], i)
        if nid < len(mwid): yield 'wmo', mw[mwid[nid]], ORIGIN - py, ORIGIN - px, pz


def load_files(listing):
    files = set()
    for l in open(listing, encoding='utf8', errors='replace'):
        p = l.split(None, 1)
        if len(p) == 2: files.add(p[1].strip().lower())
    return files


def exists(files, path):
    p = path.lower()
    return p in files or p.replace('.mdx', '.m2').replace('.mdl', '.m2') in files


def missing_near(data, files, mapn, x, y, radius):
    """Missing placements within radius (yd) of (x, y), nearest first. Tiles not shipped are reported as such."""
    tx0, ty0 = int(32 - y / TILE), int(32 - x / TILE)
    span = int(radius / TILE) + 1
    miss, absent_tiles, seen = [], [], set()
    for tx in range(tx0 - span, tx0 + span + 1):
        for ty in range(ty0 - span, ty0 + span + 1):
            d = adt(data, mapn, tx, ty)
            if d is None: absent_tiles.append((tx, ty)); continue
            for kind, path, wx, wy, wz in placements(d):
                dist = ((wx - x) ** 2 + (wy - y) ** 2) ** .5
                key = (path, round(wx), round(wy))
                if dist <= radius and key not in seen and not exists(files, path):
                    seen.add(key); miss.append((round(dist), kind, path.split(BS)[-1], round(wx), round(wy)))
    return sorted(miss), absent_tiles


if __name__ == '__main__':
    data, listing, mapn, x, y = sys.argv[1], sys.argv[2], sys.argv[3], float(sys.argv[4]), float(sys.argv[5])
    radius = float(sys.argv[6]) if len(sys.argv) > 6 else 600
    miss, absent = missing_near(data, load_files(listing), mapn, x, y, radius)
    print('absent tiles:', absent)
    for m in miss: print(*m)
