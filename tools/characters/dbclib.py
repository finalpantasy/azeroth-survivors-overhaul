#!/usr/bin/env python3
"""Tiny WDBC reader/writer for the character pipeline (all fields uint32; strings via offsets)."""
from __future__ import annotations

import struct


class DBC:
    def __init__(self, data: bytes):
        magic, self.rows_n, self.fields, self.rec, ss = struct.unpack_from("<4sIIII", data)
        if magic != b"WDBC":
            raise ValueError("not WDBC")
        if self.rec != self.fields * 4:
            raise ValueError("record size mismatch")
        end = 20 + self.rows_n * self.rec
        if end + ss != len(data):
            raise ValueError("length mismatch")
        self.rows = [list(struct.unpack_from("<" + "I" * self.fields, data, 20 + i * self.rec)) for i in range(self.rows_n)]
        self.strings = bytearray(data[end:])
        self._cache: dict[str, int] = {}

    def string(self, off: int) -> str:
        e = self.strings.find(b"\0", off)
        return bytes(self.strings[off:e]).decode("ascii", "replace")

    def add_string(self, s: str) -> int:
        if s == "":
            return 0
        if s in self._cache:
            return self._cache[s]
        off = len(self.strings)
        self.strings += s.encode("ascii") + b"\0"
        self._cache[s] = off
        return off

    def pack(self) -> bytes:
        out = struct.pack("<4sIIII", b"WDBC", len(self.rows), self.fields, self.rec, len(self.strings))
        for r in self.rows:
            out += struct.pack("<" + "I" * self.fields, *r)
        return out + bytes(self.strings)
