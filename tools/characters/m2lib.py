#!/usr/bin/env python3
"""Minimal vanilla (MD20 v256) character M2 reader used by the per-race character pipeline.

Layouts follow analysis/human-trim-feasibility/README.md and analysis/modern-converter-audit
(48-byte vertices, 108-byte bones, 44-byte embedded view headers, 32-byte submeshes,
24-byte texture units, 16-byte textures, 48-byte attachments). Read-only; never
rewrites a file except through the explicit helpers below.
"""
from __future__ import annotations

import struct

HEADER_OFFSETS = {
    "global_sequences": 0x14,
    "animations": 0x1C,
    "animation_lookup": 0x24,
    "playable_animation_lookup": 0x2C,
    "bones": 0x34,
    "key_bone_lookup": 0x3C,
    "vertices": 0x44,
    "views": 0x4C,
    "colors": 0x54,
    "textures": 0x5C,
    "transparency": 0x64,
    "texture_anims": 0x6C,
    "tex_replace": 0x74,
    "render_flags": 0x7C,
    "bone_lookup": 0x84,
    "texture_lookup": 0x8C,
    "tex_unit_lookup": 0x94,
    "transparency_lookup": 0x9C,
    "texture_anim_lookup": 0xA4,
}
# after bounding data (0xAC..0xDB): bounding triangles/vertices/normals, collision
HEADER_OFFSETS_TAIL = {
    "attachments": 0x104,
    "attachment_lookup": 0x10C,
    "events": 0x114,
    "lights": 0x11C,
    "cameras": 0x124,
    "camera_lookup": 0x12C,
    "ribbons": 0x134,
    "particles": 0x13C,
}


class M2:
    def __init__(self, data: bytes):
        if data[:4] != b"MD20":
            raise ValueError("not an MD20 model")
        self.data = data
        self.version = struct.unpack_from("<I", data, 4)[0]
        if self.version != 256:
            raise ValueError(f"unsupported M2 version {self.version}")

    def arr(self, off: int) -> tuple[int, int]:
        return struct.unpack_from("<II", self.data, off)

    def count(self, name: str) -> int:
        table = {**HEADER_OFFSETS, **HEADER_OFFSETS_TAIL}
        return self.arr(table[name])[0]

    def name(self) -> str:
        n, o = self.arr(0x08)
        return self.data[o : o + n].split(b"\0")[0].decode("ascii", "replace")

    def textures(self) -> list[tuple[int, int, str]]:
        n, o = self.arr(HEADER_OFFSETS["textures"])
        out = []
        for i in range(n):
            t, f, ln, lo = struct.unpack_from("<IIII", self.data, o + i * 16)
            s = self.data[lo : lo + ln].split(b"\0")[0].decode("ascii", "replace") if ln else ""
            out.append((t, f, s))
        return out

    def view(self, index: int = 0) -> dict:
        n, o = self.arr(HEADER_OFFSETS["views"])
        if index >= n:
            raise IndexError
        base = o + index * 44
        ni, oi, nt, ot, np_, op, nsub, osub, ntu, otu, lod = struct.unpack_from("<IIIIIIIIIII", self.data, base)
        return dict(
            n_indices=ni, ofs_indices=oi, n_tri_indices=nt, ofs_tri=ot, n_submeshes=nsub, ofs_sub=osub,
            n_texunits=ntu, ofs_texunits=otu, lod=lod,
        )

    def submeshes(self, index: int = 0) -> list[dict]:
        v = self.view(index)
        out = []
        for i in range(v["n_submeshes"]):
            rec = struct.unpack_from("<HHHHHHHH3f", self.data, v["ofs_sub"] + i * 32)
            sid, lvl, vs, vc, ts, tc, bc, bs = rec[:8]
            out.append(dict(id=sid, vstart=vs, vcount=vc, tstart=ts, tcount=tc))
        return out

    def stats(self) -> dict:
        v0 = self.view(0)
        return dict(
            version=self.version,
            vertices=self.count("vertices"),
            bones=self.count("bones"),
            sequences=self.count("animations"),
            views=self.count("views"),
            triangles_skin0=v0["n_tri_indices"] // 3,
            batches_skin0=v0["n_texunits"],
            submeshes_skin0=v0["n_submeshes"],
            attachments=self.count("attachments"),
            attachment_lookup=self.count("attachment_lookup"),
            textures=self.count("textures"),
            file_bytes=len(self.data),
        )

    def used_vertex_extent(self) -> int:
        """Highest vertex index referenced by any view's triangle list, plus one (0 if none)."""
        n, o = self.arr(HEADER_OFFSETS["views"])
        top = -1
        for i in range(n):
            v = self.view(i)
            idx = struct.unpack_from(f"<{v['n_indices']}H", self.data, v["ofs_indices"])
            tri = struct.unpack_from(f"<{v['n_tri_indices']}H", self.data, v["ofs_tri"])
            m = max(idx[t] for t in tri) if tri else -1
            top = max(top, m)
        return top + 1

    def attachments(self) -> list[tuple[int, int]]:
        n, o = self.arr(HEADER_OFFSETS_TAIL["attachments"])
        out = []
        for i in range(n):
            aid, bone = struct.unpack_from("<II", self.data, o + i * 48)
            out.append((aid, bone))
        return out

    def attachment_lookup(self) -> list[int]:
        n, o = self.arr(HEADER_OFFSETS_TAIL["attachment_lookup"])
        return list(struct.unpack_from(f"<{n}H", self.data, o))


def trim_vertex_count(data: bytes, new_count: int) -> bytes:
    """Count-only edit of header offset 0x44 (same approach as tools/human-vertex-trim)."""
    b = bytearray(data)
    old = struct.unpack_from("<I", b, 0x44)[0]
    if not (0 < new_count <= old):
        raise ValueError("new vertex count out of range")
    struct.pack_into("<I", b, 0x44, new_count)
    return bytes(b)
