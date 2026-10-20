#!/usr/bin/env python3
"""Minimal schema-less WDC5 (DB2) reader for offline analysis of the pinned Retail/Forever clients.

No schema is known, so columns are exposed by index.  A column whose values all resolve to printable zero-terminated
strings inside the table's string block is flagged as a string column (heuristic, see `Db2.string_columns`).
Sparse (flag 0x1) tables and encrypted sections are not decoded; `Db2.skipped` says what was left out.
"""
import struct

HDR_FMT = "<4sI128s9IHH7I"
HDR_SIZE = struct.calcsize(HDR_FMT)


def _bits(buf, bit_off, nbits):
    """Little-endian bit field read from bytes."""
    start = bit_off >> 3
    end = (bit_off + nbits + 7) >> 3
    v = int.from_bytes(buf[start:end], "little")
    return (v >> (bit_off & 7)) & ((1 << nbits) - 1)


class Db2:
    def __init__(self, data):
        d = bytes(data)
        self.data = d
        (magic, _ver, _schema, self.record_count, self.field_count, self.record_size, self.string_size, self.table_hash,
         self.layout_hash, self.min_id, self.max_id, _loc, self.flags, self.id_index, self.total_field_count, _bpo,
         _lookup, fsi_size, common_size, pallet_size, nsec) = struct.unpack_from(HDR_FMT, d, 0)
        if magic != b"WDC5":
            raise ValueError(f"unsupported magic {magic!r}")
        self.skipped = []
        p = HDR_SIZE
        secs = []
        for _ in range(nsec):
            secs.append(struct.unpack_from("<QIIIIIIII", d, p))
            p += 40
        self.fstruct = [struct.unpack_from("<hH", d, p + 4 * i) for i in range(self.field_count)]
        p += 4 * self.field_count
        self.fsi = [struct.unpack_from("<HHIIIII", d, p + 24 * i) for i in range(self.field_count)]
        p += fsi_size
        pallet = d[p:p + pallet_size]
        p += pallet_size
        common = d[p:p + common_size]
        # per-field slices of pallet / common blocks
        po = co = 0
        self.pal, self.com = {}, {}
        for i, (fo, fs, add, st, c1, c2, c3) in enumerate(self.fsi):
            if st in (3, 4):
                self.pal[i] = [struct.unpack_from("<I", pallet, po + 4 * k)[0] for k in range(add // 4)]
                po += add
            elif st == 2:
                n = add // 8
                self.com[i] = {struct.unpack_from("<I", common, co + 8 * k)[0]: struct.unpack_from("<I", common, co + 8 * k + 4)[0] for k in range(n)}
                co += add
        self.rows = []  # list of (id, [values])
        self.strings = b""
        self._str_base = []
        self.sparse = bool(self.flags & 1)
        for si, (key, off, nrec, strsz, rec_end, idsz, relsz, omcnt, copycnt) in enumerate(secs):
            if key != 0:
                self.skipped.append(f"section {si}: encrypted")
                continue
            if self.sparse:
                self.skipped.append("sparse table")
                continue
            rec0 = off
            strs = d[rec0 + nrec * self.record_size: rec0 + nrec * self.record_size + strsz]
            ids_off = rec0 + nrec * self.record_size + strsz
            ids = list(struct.unpack_from(f"<{idsz // 4}I", d, ids_off)) if idsz else None
            copies = []
            c_off = ids_off + idsz
            for k in range(copycnt):
                copies.append(struct.unpack_from("<II", d, c_off + 8 * k))
            base_rows = len(self.rows)
            self._str_base.append((base_rows, nrec, rec0, strs))
            for r in range(nrec):
                rec = d[rec0 + r * self.record_size: rec0 + (r + 1) * self.record_size]
                vals = self._decode(rec, ids[r] if ids else None)
                rid = ids[r] if ids else (vals[self.id_index][0] if isinstance(vals[self.id_index], list) else vals[self.id_index])
                self.rows.append((rid, vals, si, r))
            by_id = {rid: v for rid, v, _s, _r in self.rows[base_rows:]}
            for new_id, src_id in copies:
                if src_id in by_id:
                    self.rows.append((new_id, by_id[src_id], si, -1))
        self.strings_by_section = {si: strs for si, (_b, _n, _o, strs) in enumerate(self._str_base)}

    def _decode(self, rec, rid):
        out = []
        for i, (fo, fs, add, st, c1, c2, c3) in enumerate(self.fsi):
            ebits = 32 - self.fstruct[i][0]
            if st == 0:
                n = max(1, fs // ebits) if ebits else 1
                vals = [_bits(rec, fo + k * ebits, ebits) for k in range(n)]
                out.append(vals[0] if n == 1 else vals)
            elif st == 1:
                out.append(_bits(rec, fo, fs))
            elif st == 5:
                v = _bits(rec, fo, fs)
                if v & (1 << (fs - 1)):
                    v -= 1 << fs
                out.append(v)
            elif st == 2:
                out.append(self.com[i].get(rid, c1) if rid is not None else c1)
            elif st == 3:
                out.append(self.pal[i][_bits(rec, fo, fs)])
            elif st == 4:
                idx = _bits(rec, fo, fs)
                out.append(self.pal[i][idx * c3:(idx + 1) * c3])
            else:
                out.append(None)
        return out

    def string_at(self, section, row_idx, col, value):
        """Inline string for a 32-bit offset value of (row, column) in a section."""
        _b, _n, _o, strs = self._str_base[section]
        field_pos = row_idx * self.record_size + (self.fsi[col][0] >> 3)
        pos = field_pos + value - _n * self.record_size
        if value == 0 or pos < 0 or pos >= len(strs):
            return ""
        end = strs.find(b"\0", pos)
        return strs[pos:end].decode("utf8", "replace") if end >= 0 else ""

    def string_columns(self):
        cols = []
        for c in range(self.field_count):
            if self.fsi[c][3] != 0 or self.fsi[c][1] != 32:
                continue
            ok = seen = 0
            for rid, vals, si, r in self.rows[:400]:
                v = vals[c]
                if not isinstance(v, int) or v == 0 or r < 0:
                    continue
                seen += 1
                s = self.string_at(si, r, c, v)
                if s and all(32 <= ord(ch) < 0x3000 or ch in "\n\t" for ch in s):
                    ok += 1
            if seen >= 1 and ok == seen:
                cols.append(c)
        return cols

    def table(self):
        """Rows as lists with string columns resolved; first element is the row id."""
        scols = set(self.string_columns())
        out = []
        for rid, vals, si, r in self.rows:
            row = [rid]
            for c, v in enumerate(vals):
                if c in scols and isinstance(v, int) and r >= 0:
                    row.append(self.string_at(si, r, c, v))
                else:
                    row.append(v)
            out.append(row)
        return out, scols
