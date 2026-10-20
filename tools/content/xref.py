"""Find RIP-relative references in .text to a VA range [lo,hi). Usage: xref.py lo hi"""
import numpy as np, sys
from pe import PE
def xrefs(p, lo, hi):
    n, va, vs, raw, rs = p.secs[0]
    buf = np.frombuffer(p.data, dtype=np.uint8, count=rs, offset=raw)
    out = []
    CH = 8_000_000
    for s in range(0, rs - 8, CH):
        e = min(rs - 4, s + CH)
        b = buf[s:e+4].astype(np.int64)
        d = b[:-3] | (b[1:-2] << 8) | (b[2:-1] << 16) | (b[3:] << 24)
        d = np.where(d >= 2**31, d - 2**32, d)
        idx = np.arange(s, s + len(d), dtype=np.int64)
        nextva = p.base + va + idx + 4
        for extra in (0, 1, 4):   # disp followed by imm8/imm32
            t = nextva + extra + d
            m = np.nonzero((t >= lo) & (t < hi))[0]
            for k in m:
                out.append((int(p.base + va + idx[k]), int(t[k]), extra))
    return out
if __name__ == '__main__':
    p = PE(); lo = int(sys.argv[1], 16); hi = int(sys.argv[2], 16)
    for a, t, ex in xrefs(p, lo, hi): print(hex(a), hex(t), ex)
