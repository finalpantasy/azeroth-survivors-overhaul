"""Disassemble the function (from .pdata) containing a VA. Usage: dis.py VA [maxlines]"""
import sys, struct, bisect, capstone
from pe import PE
p = PE()
_n, pva, pvs, praw, prs = [s for s in p.secs if s[0] == '.pdata'][0]
fn = []
for k in range(0, pvs, 12):
    b, e, u = struct.unpack_from('<III', p.data, praw + k)
    if b == 0: break
    fn.append((b, e))
fn.sort(); starts = [f[0] for f in fn]
def func_of(va):
    rva = va - p.base; i = bisect.bisect_right(starts, rva) - 1
    # merge chained unwind chunks: take the chunk containing rva
    return p.base + fn[i][0], p.base + fn[i][1]
md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64); md.detail = False
def dis(va, maxl=4000, mark=None):
    s, e = func_of(va); o = p.va2off(s)
    out = []
    for i in md.disasm(p.data[o:o + (e - s)], s):
        out.append(f"{'>>' if mark and i.address<=mark<i.address+i.size else '  '}{i.address:x} {i.mnemonic} {i.op_str}")
    return s, e, out
if __name__ == '__main__':
    va = int(sys.argv[1], 16); m = int(sys.argv[2]) if len(sys.argv) > 2 else 400
    s, e, out = dis(va, mark=va)
    print(f'func {s:x}-{e:x} ({e-s} bytes)')
    print('\n'.join(out[:m]))
