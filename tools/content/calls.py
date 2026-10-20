"""Find direct E8/E9 call/jmp sites to a target VA. Usage: calls.py VA"""
import numpy as np, sys
from pe import PE
def callers(p, target):
    n, va, vs, raw, rs = p.secs[0]
    b = np.frombuffer(p.data, dtype=np.uint8, count=rs, offset=raw)
    out = []
    for op in (0xE8, 0xE9):
        idx = np.nonzero(b[:-5] == op)[0]
        d = (b[idx+1].astype(np.int64) | (b[idx+2].astype(np.int64) << 8) | (b[idx+3].astype(np.int64) << 16) | (b[idx+4].astype(np.int64) << 24))
        d = np.where(d >= 2**31, d - 2**32, d)
        t = p.base + va + idx + 5 + d
        for k in np.nonzero(t == target)[0]: out.append((op, p.base + va + int(idx[k])))
    return out
if __name__ == '__main__':
    p = PE()
    from asm import func_of
    for t in sys.argv[1:]:
        for op, a in callers(p, int(t, 16)): print(t, hex(op), hex(a), 'in', hex(func_of(a)[0]))
