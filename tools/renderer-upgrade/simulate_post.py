#!/usr/bin/env python3
"""Offline numpy model of patches/ffx_post.wgsl `post_scene`, applied to a screenshot.

Used to check a runtime capture: if the candidate's frame matches post(baseline) better than it
matches the baseline itself, the patched combine ran. Also reports how many pixels take the FXAA
branch versus the sharpen branch, and the high-frequency energy change.

Approximation: the shader runs on the scene target (renderScale x window size, gamma space,
before glow and UI); this runs on the final window-size screenshot. UI areas are excluded by ROI.

Usage: python -I simulate_post.py <baseline.png> <candidate.png> [--fxaa F] [--sharpen S]
       [--edge-min E] [--edge-rel R] [--subpix P] [--roi x0,y0,x1,y1] [--out pred.png]
"""
import argparse

import numpy as np
from PIL import Image


def load(p):
    return np.asarray(Image.open(p).convert("RGB"), dtype=np.float64) / 255.0


def bilinear(img, x, y):
    """Sample img (H,W,3) at pixel coords x,y (texel centers at +0.5), clamp to edge."""
    h, w, _ = img.shape
    x = np.clip(x - 0.5, 0, w - 1)
    y = np.clip(y - 0.5, 0, h - 1)
    x0 = np.floor(x).astype(int)
    y0 = np.floor(y).astype(int)
    x1 = np.minimum(x0 + 1, w - 1)
    y1 = np.minimum(y0 + 1, h - 1)
    fx = (x - x0)[..., None]
    fy = (y - y0)[..., None]
    a = img[y0, x0] * (1 - fx) + img[y0, x1] * fx
    b = img[y1, x0] * (1 - fx) + img[y1, x1] * fx
    return a * (1 - fy) + b * fy


def luma(c):
    return c @ np.array([0.299, 0.587, 0.114])


def post(img, fxaa=1.0, sharpen=0.35, edge_min=0.0312, edge_rel=0.125, subpix=0.5):
    h, w, _ = img.shape
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    px, py = xx + 0.5, yy + 0.5
    tap = lambda dx, dy: bilinear(img, px + dx, py + dy)
    c = img
    n, s, e, wv = tap(0, -1), tap(0, 1), tap(1, 0), tap(-1, 0)
    lc, ln, ls, le, lw = map(luma, (c, n, s, e, wv))
    lmax = np.maximum.reduce([lc, ln, ls, le, lw])
    lmin = np.minimum.reduce([lc, ln, ls, le, lw])
    rng = lmax - lmin
    edge = (fxaa > 0) & (rng >= np.maximum(edge_min, lmax * edge_rel))

    # Sharpen branch.
    mn = np.minimum.reduce([c, n, s, e, wv])
    mx = np.maximum.reduce([c, n, s, e, wv])
    amp = np.sqrt(np.clip(np.minimum(mn, 1 - mx) / np.maximum(mx, 1e-4), 0, 1))
    lobe = amp * -0.125
    sharp = np.clip((c + (n + s + e + wv) * lobe) / (1 + 4 * lobe), 0, 1)
    out_sharp = c + (sharp - c) * sharpen if sharpen > 0 else c

    # FXAA branch.
    lnw, lne, lsw, lse = (luma(tap(dx, dy)) for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1)))
    edge_h = abs(lnw + lsw - 2 * lw) + 2 * abs(ln + ls - 2 * lc) + abs(lne + lse - 2 * le)
    edge_v = abs(lnw + lne - 2 * ln) + 2 * abs(lw + le - 2 * lc) + abs(lsw + lse - 2 * ls)
    hor = edge_h >= edge_v
    l_neg = np.where(hor, ln, lw)
    l_pos = np.where(hor, ls, le)
    g_neg, g_pos = abs(l_neg - lc), abs(l_pos - lc)
    neg = g_neg >= g_pos
    grad = 0.25 * np.maximum(g_neg, g_pos)
    step = np.where(neg, -1.0, 1.0)
    local_avg = np.where(neg, 0.5 * (l_neg + lc), 0.5 * (l_pos + lc))
    ex = px + np.where(hor, 0, 0.5 * step)
    ey = py + np.where(hor, 0.5 * step, 0)
    ax, ay = np.where(hor, 1.0, 0.0), np.where(hor, 0.0, 1.0)
    ua_x, ua_y = ex - ax, ey - ay
    ub_x, ub_y = ex + ax, ey + ay
    end_a = luma(bilinear(img, ua_x, ua_y)) - local_avg
    end_b = luma(bilinear(img, ub_x, ub_y)) - local_avg
    done_a = abs(end_a) >= grad
    done_b = abs(end_b) >= grad
    for st in (1.5, 2.0, 2.0, 4.0, 8.0):
        ma = ~done_a
        ua_x, ua_y = np.where(ma, ua_x - ax * st, ua_x), np.where(ma, ua_y - ay * st, ua_y)
        end_a = np.where(ma, luma(bilinear(img, ua_x, ua_y)) - local_avg, end_a)
        done_a = np.where(ma, abs(end_a) >= grad, done_a)
        mb = ~done_b
        ub_x, ub_y = np.where(mb, ub_x + ax * st, ub_x), np.where(mb, ub_y + ay * st, ub_y)
        end_b = np.where(mb, luma(bilinear(img, ub_x, ub_y)) - local_avg, end_b)
        done_b = np.where(mb, abs(end_b) >= grad, done_b)
    dist_a = np.where(hor, px - ua_x, py - ua_y)
    dist_b = np.where(hor, ub_x - px, ub_y - py)
    a_near = dist_a < dist_b
    dist = np.minimum(dist_a, dist_b)
    span = np.maximum(dist_a + dist_b, 1e-6)
    end_near = np.where(a_near, end_a, end_b)
    good = (end_near < 0) != (lc < local_avg)
    off = np.where(good, 0.5 - dist / span, 0.0)
    avg = (2 * (ln + ls + le + lw) + lnw + lne + lsw + lse) / 12
    sa = np.clip(abs(avg - lc) / np.maximum(rng, 1e-9), 0, 1)
    sb = (-2 * sa + 3) * sa * sa
    off = np.maximum(off, sb * sb * subpix)
    ox = px + np.where(hor, 0, off * step)
    oy = py + np.where(hor, off * step, 0)
    out_fxaa = np.clip(bilinear(img, ox, oy), 0, 1)

    out = np.where(edge[..., None], out_fxaa, out_sharp)
    return out, edge


def hf(img):
    l = luma(img)
    lap = 4 * l[1:-1, 1:-1] - l[:-2, 1:-1] - l[2:, 1:-1] - l[1:-1, :-2] - l[1:-1, 2:]
    return lap.std()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("baseline")
    ap.add_argument("candidate", nargs="?")
    ap.add_argument("--fxaa", type=float, default=1.0)
    ap.add_argument("--sharpen", type=float, default=0.35)
    ap.add_argument("--edge-min", type=float, default=0.0312)
    ap.add_argument("--edge-rel", type=float, default=0.125)
    ap.add_argument("--subpix", type=float, default=0.5)
    ap.add_argument("--roi", default="0,120,1280,480")
    ap.add_argument("--out")
    a = ap.parse_args()
    x0, y0, x1, y1 = map(int, a.roi.split(","))
    b = load(a.baseline)
    pred, edge = post(b, a.fxaa, a.sharpen, a.edge_min, a.edge_rel, a.subpix)
    r = lambda im: im[y0:y1, x0:x1]
    print(f"fxaa-branch pixels {edge[y0:y1, x0:x1].mean() * 100:.1f}%  "
          f"hf baseline {hf(r(b)):.4f} predicted {hf(r(pred)):.4f} "
          f"({(hf(r(pred)) / hf(r(b)) - 1) * 100:+.1f}%)")
    if a.candidate:
        c = load(a.candidate)
        print(f"hf candidate {hf(r(c)):.4f} ({(hf(r(c)) / hf(r(b)) - 1) * 100:+.1f}%)  "
              f"mean|cand-base| {abs(r(c) - r(b)).mean() * 255:.3f}  "
              f"mean|cand-pred| {abs(r(c) - r(pred)).mean() * 255:.3f}  "
              f"mean|pred-base| {abs(r(pred) - r(b)).mean() * 255:.3f}  (0-255 units)")
    if a.out:
        Image.fromarray((np.clip(pred, 0, 1) * 255 + 0.5).astype(np.uint8)).save(a.out)


if __name__ == "__main__":
    main()
