"""Terrain height at a world point from a 1.12 ADT (MCNK position + nearest MCVT vertex).
Usage: python adt_height.py <Data dir> <map name> <x> <y> [<x> <y> ...]
"""
import struct, subprocess, sys, tempfile, os

MPQ = os.path.join(os.path.dirname(__file__), '..', 'mpq-inspect.exe')
TILE = 1600 / 3


def tile_of(x, y): return int(32 - y / TILE), int(32 - x / TILE)


def chunks(path):
    d = open(path, 'rb').read(); o = 0
    while o + 8 <= len(d):
        tag, n = d[o:o + 4], struct.unpack_from('<I', d, o + 4)[0]
        if tag == b'KNCM':
            h = o + 8
            px, py, pz = struct.unpack_from('<3f', d, h + 0x68)
            mcvt = struct.unpack_from('<I', d, h + 0x14)[0]
            hv = struct.unpack_from('<145f', d, o + mcvt + 8)
            yield px, py, pz, hv
        o += 8 + n


def height(data, mapn, x, y):
    tx, ty = tile_of(x, y)
    out = os.path.join(tempfile.gettempdir(), f'{mapn}_{tx}_{ty}.adt')
    if not os.path.exists(out):
        subprocess.run([MPQ, data, 'extract', f'World\\Maps\\{mapn}\\{mapn}_{tx}_{ty}.adt', out], check=True, capture_output=True)
    best = None
    for px, py, pz, hv in chunks(out):
        # chunk covers x in (px - 33.33, px], y in (py - 33.33, py]
        if px - TILE / 16 <= x <= px and py - TILE / 16 <= y <= py:
            unit = TILE / 16 / 8
            i = round((px - x) / unit); j = round((py - y) / unit)
            return pz + hv[i * 17 + j], (tx, ty)
    return best, (tx, ty)


if __name__ == '__main__':
    data, mapn = sys.argv[1], sys.argv[2]
    v = list(map(float, sys.argv[3:]))
    for k in range(0, len(v), 2):
        print(v[k], v[k + 1], height(data, mapn, v[k], v[k + 1]))
