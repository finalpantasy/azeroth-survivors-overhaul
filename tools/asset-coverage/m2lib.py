"""Header-level M2 readers for the coverage tools.

Classic MD20 (version 256, the engine's native format): all arrays are (count, offset) pairs at fixed header
offsets, offsets absolute in the file. Modern MD21 chunked (Legion and later, retail/Forever): the MD21 chunk
holds an MD20 core with offsets relative to the chunk payload; skins live in separate .skin files.
Only counts and slot types are read here; this is a feasibility census, not a converter.
"""
import struct

CLASSIC = dict(name=0x08, flags=0x10, gseq=0x14, seq=0x1C, seqlookup=0x24, playable=0x2C, bones=0x34, keybones=0x3C,
               verts=0x44, views=0x4C, colors=0x54, textures=0x5C, transp=0x64, flipbooks=0x6C, texanim=0x74,
               renderflags=0x84, bonelookup=0x8C, texlookup=0x94, attach=0x104, events=0x114, lights=0x11C,
               cameras=0x124, ribbons=0x134, particles=0x13C)
# modern (v >= 264): bones/vertices etc. at different offsets
MODERN = dict(name=0x08, flags=0x10, gseq=0x14, seq=0x1C, seqlookup=0x24, bones=0x2C, keybones=0x34, verts=0x3C,
              nskins=0x44, colors=0x48, textures=0x50, transp=0x58, texanim=0x60, repl=0x68, materials=0x70,
              bonelookup=0x78, texlookup=0x80, attach=0xF0, events=0x100, lights=0x108, cameras=0x110,
              ribbons=0x120, particles=0x128)


def u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def arr(b, o, base=0):
    return u32(b, o), u32(b, o + 4) + base


def bounded(b, count, off, stride):
    return count * stride + off <= len(b)


def parse_classic(b):
    if b[:4] != b"MD20":
        raise ValueError("not MD20")
    ver = u32(b, 4)
    out = {"format": "MD20", "version": ver}
    for k, o in CLASSIC.items():
        if k in ("flags",):
            continue
        out[k] = u32(b, o)
    vc, vo = arr(b, CLASSIC["views"])
    out["views"] = vc
    if vc:
        vp = vo
        n_idx = u32(b, vp)
        n_tri = u32(b, vp + 8)
        n_sub = u32(b, vp + 24)
        n_bat = u32(b, vp + 32)
        out.update(tri_indices=n_tri, triangles=n_tri // 3, submeshes=n_sub, batches=n_bat)
    cnt, ofs = arr(b, CLASSIC["textures"])
    types = []
    for i in range(min(cnt, 64)):
        if ofs + i * 16 + 16 <= len(b):
            types.append(u32(b, ofs + i * 16))
    out["texture_types"] = types
    return out


def parse_modern(b):
    """MD21 chunked file -> dict (counts only). Returns also SFID/TXID info."""
    if b[:4] == b"MD21":
        pos = 0
        chunks = {}
        while pos + 8 <= len(b):
            tag = b[pos:pos + 4].decode("ascii", "replace")
            size = u32(b, pos + 4)
            chunks[tag] = (pos + 8, size)
            pos += 8 + size
        cs, size = chunks["MD21"]
        core = b[cs:cs + size]
    elif b[:4] == b"MD20":
        core, chunks = b, {}
    else:
        raise ValueError("not MD21/MD20")
    ver = u32(core, 4)
    out = {"format": "MD21" if b[:4] == b"MD21" else "MD20", "version": ver}
    for k, o in MODERN.items():
        if k in ("flags", "name"):
            continue
        out[k] = u32(core, o)
    # textures: 16 bytes (type, flags, name count, name offset)
    cnt, ofs = arr(core, MODERN["textures"])
    out["texture_types"] = [u32(core, ofs + i * 16) for i in range(min(cnt, 64)) if ofs + i * 16 + 16 <= len(core)]
    if "TXID" in chunks:
        s, sz = chunks["TXID"]
        out["txid"] = list(struct.unpack_from(f"<{sz // 4}I", b, s))
    if "SFID" in chunks:
        s, sz = chunks["SFID"]
        out["sfid"] = list(struct.unpack_from(f"<{sz // 4}I", b, s))
    out["chunks"] = {k: v[1] for k, v in chunks.items()}
    return out


def parse_any(b):
    if b[:4] == b"MD20" and u32(b, 4) <= 263:
        return parse_classic(b)
    return parse_modern(b)


def skin_counts(b):
    """Modern .skin (SKIN chunk-less layout): returns vertex lookups, triangle indices, submeshes, batches."""
    if b[:4] != b"SKIN":
        raise ValueError("not SKIN")
    v = arr(b, 4)
    t = arr(b, 12)
    bn = arr(b, 20)
    sub = arr(b, 28)
    bat = arr(b, 36)
    return dict(vertex_lookups=v[0], tri_indices=t[0], triangles=t[0] // 3, submeshes=sub[0], batches=bat[0])
