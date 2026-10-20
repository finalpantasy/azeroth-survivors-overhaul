"""WDBC (1.12-style DBC) cell edits. New strings are appended to the string block."""
from __future__ import annotations

import struct
from typing import Any

from .util import InstallerError


class Dbc:
    def __init__(self, data: bytes, name: str = "DBC") -> None:
        if len(data) < 20:
            raise InstallerError(f"{name}: truncated WDBC header", code="dbc-invalid")
        magic, self.records, self.fields, self.record_size, self.string_size = struct.unpack_from("<4s4I", data)
        if magic != b"WDBC":
            raise InstallerError(f"{name}: expected WDBC, found {magic!r}", code="dbc-invalid")
        if 20 + self.records * self.record_size + self.string_size != len(data):
            raise InstallerError(f"{name}: header sizes do not match the file length", code="dbc-invalid")
        if self.fields * 4 != self.record_size:
            raise InstallerError(f"{name}: only 4-byte fields are supported", code="dbc-invalid")
        self.name = name
        self.data = bytearray(data)
        self._ids: dict[int, int] | None = None

    def _row_index(self, row_id: int) -> int:
        if self._ids is None:
            self._ids = {}
            for i in range(self.records):
                rid = struct.unpack_from("<I", self.data, 20 + i * self.record_size)[0]
                if rid in self._ids:
                    raise InstallerError(f"{self.name}: duplicate row id {rid}", code="dbc-invalid")
                self._ids[rid] = i
        if row_id not in self._ids:
            raise InstallerError(f"{self.name}: row {row_id} does not exist", code="dbc-invalid")
        return self._ids[row_id]

    def _cell_offset(self, row_id: int, column: int) -> int:
        if column >= self.fields:
            raise InstallerError(f"{self.name}: column {column} is out of range ({self.fields} fields)", code="dbc-invalid")
        return 20 + self._row_index(row_id) * self.record_size + column * 4

    def get_uint(self, row_id: int, column: int) -> int:
        return struct.unpack_from("<I", self.data, self._cell_offset(row_id, column))[0]

    def get_string(self, row_id: int, column: int) -> str:
        offset = self.get_uint(row_id, column)
        base = 20 + self.records * self.record_size
        if offset >= self.string_size:
            raise InstallerError(f"{self.name}: string offset {offset} is outside the string block", code="dbc-invalid")
        end = self.data.find(b"\0", base + offset, base + self.string_size)
        if end < 0:
            raise InstallerError(f"{self.name}: unterminated string", code="dbc-invalid")
        return bytes(self.data[base + offset:end]).decode("ascii")

    def get(self, row_id: int, column: int, kind: str) -> Any:
        return self.get_string(row_id, column) if kind == "string" else self.get_uint(row_id, column)

    def set_uint(self, row_id: int, column: int, value: int) -> None:
        struct.pack_into("<I", self.data, self._cell_offset(row_id, column), value)

    def set_string(self, row_id: int, column: int, value: str) -> None:
        if "\0" in value:
            raise InstallerError("DBC strings cannot contain NUL", code="dbc-invalid")
        encoded = value.encode("ascii") + b"\0"
        offset = self._cell_offset(row_id, column)
        struct.pack_into("<I", self.data, offset, self.string_size)
        self.data.extend(encoded)
        self.string_size += len(encoded)
        struct.pack_into("<I", self.data, 16, self.string_size)

    def tobytes(self) -> bytes:
        return bytes(self.data)


def apply_edits(data: bytes, edits: list[dict[str, Any]], name: str = "DBC") -> bytes:
    """Apply recipe ``edits``; every edit must find ``expect_before`` or the whole edit fails."""
    dbc = Dbc(data, name)
    for edit in edits:
        before = dbc.get(edit["row_id"], edit["column"], edit["type"])
        if before != edit["expect_before"]:
            raise InstallerError(
                f"{name} row {edit['row_id']} column {edit['column']} is {before!r}, expected {edit['expect_before']!r}",
                "The game data differs from what this recipe was written for; update the game or the recipe.",
                "dbc-mismatch")
        if edit["type"] == "string":
            dbc.set_string(edit["row_id"], edit["column"], edit["value"])
        else:
            dbc.set_uint(edit["row_id"], edit["column"], edit["value"])
    return dbc.tobytes()
