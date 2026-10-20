"""Tiny PE helpers for the v0.103.0 EXE: file offset <-> VA, pointer search."""
import pefile, struct, re, sys
EXE = r"./AzerothSurvivors.exe"

class PE:
    def __init__(self, path=EXE):
        self.path = path
        self.data = open(path, 'rb').read()
        self.pe = pefile.PE(data=self.data, fast_load=True)
        self.base = self.pe.OPTIONAL_HEADER.ImageBase
        self.secs = [(s.Name.rstrip(b'\0').decode(), s.VirtualAddress, s.Misc_VirtualSize, s.PointerToRawData, s.SizeOfRawData) for s in self.pe.sections]
    def off2va(self, off):
        for n, va, vs, raw, rs in self.secs:
            if raw <= off < raw + rs: return self.base + va + off - raw
    def va2off(self, va):
        rva = va - self.base
        for n, v, vs, raw, rs in self.secs:
            if v <= rva < v + max(vs, rs): return raw + rva - v
    def sec_of(self, off):
        for n, va, vs, raw, rs in self.secs:
            if raw <= off < raw + rs: return n
    def ptrs_to(self, va):
        pat = struct.pack('<Q', va)
        return [m.start() for m in re.finditer(re.escape(pat), self.data)]
    def u64(self, off): return struct.unpack_from('<Q', self.data, off)[0]
    def u32(self, off): return struct.unpack_from('<I', self.data, off)[0]
    def str_at_va(self, va, n):
        o = self.va2off(va); return self.data[o:o+n] if o is not None else None
