#!/usr/bin/env python3
"""Contact sheet of one lab run: model crop of each segment's last frame + counter text. Usage: montage.py <run_dir> <out.png>"""
import json, sys
from pathlib import Path
from PIL import Image, ImageDraw
run = Path(sys.argv[1]); d = json.loads((run / "result.json").read_text())
segs = d["timeline"]["segments"]
tiles = []
for s in segs:
    im = Image.open(run / "shots" / s["frames"][-1]).convert("RGB")
    c = im.crop((400, 195, 900, 600)).resize((300, 243), Image.LANCZOS)
    t = Image.new("RGB", (300, 261), (0, 0, 0)); t.paste(c, (0, 18))
    k = s["counters"]; g = lambda n: (k.get(n) or ["?"])[0]
    ImageDraw.Draw(t).text((4, 3), f"{s['segment']} s{g('skin')} f{g('face')} hs{g('hair_style')} hc{g('hair_color')} fh{g('facial_hair')}", fill=(255, 220, 120))
    tiles.append(t)
cols = 4; rows = (len(tiles) + cols - 1) // cols
sheet = Image.new("RGB", (cols * 300, rows * 261))
for i, t in enumerate(tiles): sheet.paste(t, ((i % cols) * 300, (i // cols) * 261))
sheet.save(sys.argv[2]); print(sheet.size)
