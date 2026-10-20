"""Minimal WDBC reader (1.12-era layout) for the coverage tools: rows as lists of raw u32 plus string-block lookup."""
import struct


class Dbc:
    def __init__(self, path):
        b = open(path, "rb").read()
        if b[:4] != b"WDBC":
            raise ValueError(f"not a WDBC file: {path}")
        self.count, self.fields, self.rec_size, self.str_size = struct.unpack_from("<4I", b, 4)
        self.packed = self.fields * 4 != self.rec_size
        if False:
            raise ValueError("record size is not fields*4")
        self.base = 20
        self.data = b
        self.str_off = self.base + self.count * self.rec_size

    def row(self, i):
        return struct.unpack_from(f"<{self.fields}I", self.data, self.base + i * self.rec_size)

    def rows(self):
        for i in range(self.count):
            yield self.row(i)

    def string(self, off):
        if off == 0 or off >= self.str_size:
            return ""
        s = self.str_off + off
        e = self.data.index(b"\0", s)
        return self.data[s:e].decode("utf8", "replace")

    def f32(self, v):
        return struct.unpack("<f", struct.pack("<I", v))[0]

    def i32(self, v):
        return v - (1 << 32) if v >= (1 << 31) else v
