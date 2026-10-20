"""Small, auditable edits on classic MD20 v256 effect models (same approach as the reviewed spell-hd-phase2 build):

* rename_textures: append NUL-terminated alias strings at the end of the file and repoint the texture records
  (offset/length only; texture index order is unchanged).
* set_seq_clamp: copy the native clamp bit (flags & 1) onto candidate sequences with the same animation id and ordinal.
"""
import struct

TEX_OFS = 0x5C
SEQ_OFS = 0x1C
TEX_REC = 16
SEQ_REC = 64


def textures(b):
    n, o = struct.unpack_from("<II", b, TEX_OFS)
    out = []
    for i in range(n):
        t, fl, ln, no = struct.unpack_from("<IIII", b, o + i * TEX_REC)
        name = b[no:no + ln].rstrip(b"\0").decode("ascii", "replace") if t == 0 and ln else ""
        out.append({"i": i, "type": t, "flags": fl, "name": name, "rec": o + i * TEX_REC})
    return out


def sequences(b):
    n, o = struct.unpack_from("<II", b, SEQ_OFS)
    out = []
    for i in range(n):
        r = o + i * SEQ_REC
        aid, sub = struct.unpack_from("<HH", b, r)
        start, end, spd, fl = struct.unpack_from("<IIII", b, r + 4)
        out.append({"i": i, "id": aid, "sub": sub, "start": start, "end": end, "flags": fl, "rec": r})
    return out


def rename_textures(b, mapping):
    """mapping: {texture index: new path (backslashes)}. Returns new bytes."""
    b = bytearray(b)
    for idx, new in mapping.items():
        rec = textures(bytes(b))[idx]
        raw = new.encode("ascii") + b"\0"
        off = len(b)
        b += raw
        struct.pack_into("<II", b, rec["rec"] + 8, len(raw), off)
    return bytes(b)


def set_seq_clamp(b, native):
    """Copy the native low flag bit (clamp) per matching (id, ordinal-within-id) sequence."""
    b = bytearray(b)
    cs, ns = sequences(bytes(b)), sequences(native)
    ordn = {}
    for s in ns:
        ordn.setdefault(s["id"], []).append(s)
    seen = {}
    changes = []
    for s in cs:
        k = seen.get(s["id"], 0)
        seen[s["id"]] = k + 1
        lst = ordn.get(s["id"])
        if not lst or k >= len(lst):
            continue
        nbit = lst[k]["flags"] & 1
        if (s["flags"] & 1) != nbit:
            new = (s["flags"] & ~1) | nbit
            struct.pack_into("<I", b, s["rec"] + 16, new)
            changes.append((s["i"], s["flags"], new))
    return bytes(b), changes
