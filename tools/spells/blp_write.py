"""BLP2 writer (DXT1 / DXT5 / uncompressed ARGB, full mip chain) for the native engine, via etcpak + Pillow.

Layout proven by the shipped HD CLOUDS8X8 payload: BLP2 type 1, header 148 bytes + 1024-byte palette area, mip data from
offset 1172, DXT5 -> encoding 2, alpha_depth 8, alpha_encoding 7, mips down to 1x1 with 16-byte minimum blocks for DXT5.
"""
import io
import struct

import etcpak
from PIL import Image


def _mips(im, count=None):
    out = [im]
    w, h = im.size
    while (w > 1 or h > 1) and (count is None or len(out) < count):
        w, h = max(1, w // 2), max(1, h // 2)
        out.append(im.resize((w, h), Image.LANCZOS if w * h > 16 else Image.BOX))
    return out


def _compress(im, mode):
    w, h = im.size
    # etcpak wants multiples of 4; pad small mips by edge replication.
    pw, ph = max(4, (w + 3) // 4 * 4), max(4, (h + 3) // 4 * 4)
    if (pw, ph) != (w, h):
        canvas = Image.new("RGBA", (pw, ph))
        canvas.paste(im, (0, 0))
        for x in range(w, pw):
            canvas.paste(im.crop((w - 1, 0, w, h)), (x, 0))
        for y in range(h, ph):
            canvas.paste(canvas.crop((0, h - 1, pw, h)), (0, y))
        im = canvas
    raw = im.tobytes()
    return etcpak.compress_bc3(raw, pw, ph) if mode == "dxt5" else etcpak.compress_bc1(raw, pw, ph)


def encode_blp2(img, mode="dxt5", mips=True):
    """img: PIL image (any mode). mode: dxt1 | dxt5 | argb. Returns BLP2 bytes."""
    im = img.convert("RGBA")
    chain = _mips(im) if mips else [im]
    datas = []
    for m in chain:
        if mode == "argb":
            r, g, b, a = m.split()
            datas.append(Image.merge("RGBA", (b, g, r, a)).tobytes())
        else:
            datas.append(_compress(m, mode))
    enc, adepth, aenc = {"dxt1": (2, 0, 0), "dxt5": (2, 8, 7), "argb": (3, 8, 8)}[mode]
    head = b"BLP2" + struct.pack("<I4B2I", 1, enc, adepth, aenc, 1 if mips else 0, im.size[0], im.size[1])
    base = 148 + 1024
    offs, sizes, pos = [], [], base
    for d in datas:
        offs.append(pos)
        sizes.append(len(d))
        pos += len(d)
    offs += [0] * (16 - len(offs))
    sizes += [0] * (16 - len(sizes))
    return head + struct.pack("<16I", *offs) + struct.pack("<16I", *sizes) + b"\0" * 1024 + b"".join(datas)


def decode(data):
    return Image.open(io.BytesIO(data)).convert("RGBA")


if __name__ == "__main__":
    import sys
    from pathlib import Path
    src = Path(sys.argv[1]).read_bytes()
    im = decode(src)
    out = encode_blp2(im, "dxt5")
    back = decode(out)
    from PIL import ImageChops
    d = ImageChops.difference(im, back)
    print(len(src), "->", len(out), "maxdiff", max(max(x) for x in d.getextrema()))
