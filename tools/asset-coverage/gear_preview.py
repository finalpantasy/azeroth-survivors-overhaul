#!/usr/bin/env python3
"""Offline contact-sheet renderer for rigid retail M2 candidates (picking aid, not an engine capture).

Renders each candidate (model FDID) from two orthogonal views with its sibling/own texture using a tiny z-buffer
rasterizer, so a human can look at the silhouette and texture before spending a lab run.  Local analysis only:
the sheets contain Blizzard art.

  gear_preview.py --cache work/cache-items --listfile work/listfile-filtered.csv --out sheet.png --fetch retail 147534 147431 ...
"""
from __future__ import annotations

import argparse
import importlib.util
import io
import struct
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("ric", HERE / "rigid_item_convert.py")
ric = importlib.util.module_from_spec(spec)
sys.modules["ric"] = ric
spec.loader.exec_module(ric)


def candidate(cache, fdid, listfile_pick_factory, fetch_profile=None):
    if fetch_profile:
        ric.fetch(cache, fetch_profile, [fdid])
    model = ric.read_cache(cache, fdid)
    ch = ric.split_chunks(model)
    skin_id = struct.unpack_from("<I", ch["SFID"], 0)[0]
    if fetch_profile:
        ric.fetch(cache, fetch_profile, [skin_id])
    info = ric.inspect(model, ric.read_cache(cache, skin_id), True, True)
    txid = ch.get("TXID")
    txids = list(struct.unpack(f"<{len(txid) // 4}I", txid)) if txid else []
    pick = listfile_pick_factory(fdid)
    slots = ric.derive_textures(info, {}, pick)
    if fetch_profile:
        ric.fetch(cache, fetch_profile, list(set(slots.values())))
    return info, slots, pick


def decode_blp(data):
    try:
        im = Image.open(io.BytesIO(data)).convert("RGBA")
        return np.asarray(im)
    except Exception as exc:  # noqa: BLE001
        return None


def render(info, tex, view, size=256):
    vb = info["vblob"]
    n = info["vcount"]
    pos = np.array([struct.unpack_from("<3f", vb, i * 48) for i in range(n)], dtype=np.float32)
    uv = np.array([struct.unpack_from("<2f", vb, i * 48 + 32) for i in range(n)], dtype=np.float32)
    lookup, tris = info["lookup"], info["tris"]
    if view == "side":
        px, py, pz = pos[:, 0], pos[:, 2], pos[:, 1]
    elif view == "top":
        px, py, pz = pos[:, 0], pos[:, 1], pos[:, 2]
    else:
        px, py, pz = pos[:, 1], pos[:, 2], pos[:, 0]
    sx, sy = px.max() - px.min(), py.max() - py.min()
    s = (size - 16) / max(sx, sy, 1e-6)
    X = (px - px.min()) * s + 8
    Y = size - ((py - py.min()) * s + 8)
    img = np.full((size, size, 4), (28, 28, 34, 255), dtype=np.uint8)
    zb = np.full((size, size), -1e9, dtype=np.float32)
    lights = np.array([0.4, 0.5, 0.75])
    for t in range(0, len(tris), 3):
        a, b, c = (lookup[tris[t + k]] for k in range(3))
        x0, y0, x1, y1, x2, y2 = X[a], Y[a], X[b], Y[b], X[c], Y[c]
        minx, maxx = int(max(0, min(x0, x1, x2))), int(min(size - 1, max(x0, x1, x2) + 1))
        miny, maxy = int(max(0, min(y0, y1, y2))), int(min(size - 1, max(y0, y1, y2) + 1))
        den = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
        if abs(den) < 1e-9 or maxx < minx or maxy < miny:
            continue
        gx, gy = np.meshgrid(np.arange(minx, maxx + 1) + 0.5, np.arange(miny, maxy + 1) + 0.5)
        w0 = ((y1 - y2) * (gx - x2) + (x2 - x1) * (gy - y2)) / den
        w1 = ((y2 - y0) * (gx - x2) + (x0 - x2) * (gy - y2)) / den
        w2 = 1 - w0 - w1
        m = (w0 >= -0.001) & (w1 >= -0.001) & (w2 >= -0.001)
        if not m.any():
            continue
        z = w0 * pz[a] + w1 * pz[b] + w2 * pz[c]
        u = w0 * uv[a, 0] + w1 * uv[b, 0] + w2 * uv[c, 0]
        v = w0 * uv[a, 1] + w1 * uv[b, 1] + w2 * uv[c, 1]
        if tex is not None:
            th, tw = tex.shape[:2]
            col = tex[(np.mod(v, 1.0) * th).astype(int) % th, (np.mod(u, 1.0) * tw).astype(int) % tw]
        else:
            col = np.full(u.shape + (4,), 160, dtype=np.uint8)
        ys, xs = np.nonzero(m)
        for yy, xx in zip(ys, xs):
            X_, Y_ = minx + xx, miny + yy
            if z[yy, xx] > zb[Y_, X_] and col[yy, xx, 3] > 40:
                zb[Y_, X_] = z[yy, xx]
                img[Y_, X_] = col[yy, xx]
    return Image.fromarray(img, "RGBA")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", type=Path, required=True)
    ap.add_argument("--listfile", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--fetch", choices=("retail", "forever"))
    ap.add_argument("--cols", type=int, default=3)
    ap.add_argument("fdids", type=int, nargs="+")
    a = ap.parse_args()
    names = {}
    for line in a.listfile.read_text(encoding="utf8").splitlines():
        f, _, n = line.partition(";")
        if f.isdigit():
            names[int(f)] = n
    cells = []
    for fd in a.fdids:
        label = names.get(fd, str(fd)).rsplit("/", 1)[-1]
        try:
            info, slots, pick = candidate(a.cache, fd, lambda f: ric.make_listfile_pick(a.listfile, f), a.fetch)
            blp = ric.read_cache(a.cache, list(slots.values())[0])
            tex = decode_blp(blp)
            views = [render(info, tex, "side"), render(info, tex, "top")]
            tag = f"{fd} {label}  v{info['vcount']} t{len(info['tris']) // 3} tex={list(slots.values())[0]} {'ok' if tex is not None else 'NODECODE'}"
        except Exception as exc:  # noqa: BLE001
            views = [Image.new("RGBA", (256, 256), (60, 20, 20, 255))] * 2
            tag = f"{fd} {label}  FAIL {exc}"[:110]
        cell = Image.new("RGBA", (512, 280), (0, 0, 0, 255))
        cell.paste(views[0], (0, 24))
        cell.paste(views[1], (256, 24))
        ImageDraw.Draw(cell).text((4, 6), tag, fill=(255, 255, 255, 255))
        cells.append(cell)
    rows = (len(cells) + a.cols - 1) // a.cols
    sheet = Image.new("RGBA", (a.cols * 512, rows * 280), (0, 0, 0, 255))
    for i, c in enumerate(cells):
        sheet.paste(c, ((i % a.cols) * 512, (i // a.cols) * 280))
    sheet.convert("RGB").save(a.out)
    print("wrote", a.out)


if __name__ == "__main__":
    main()
