"""Contact sheet of the combat frames of a lab run: sheet.py <run_dir> <out.png> [first] [count]  (centre crops at full res)."""
import glob, sys
from PIL import Image
run, out = sys.argv[1], sys.argv[2]
fs = sorted(glob.glob(run + "/shots/*.png"))
first = int(sys.argv[3]) if len(sys.argv) > 3 else 0
count = int(sys.argv[4]) if len(sys.argv) > 4 else 9
sel = fs[first:]
step = max(1, len(sel) // count) if count < 100 and len(sel) > 40 else 1
sel = sel[::step][:count]
cw, ch = 640, 400
rows = (len(sel) + 2) // 3
sheet = Image.new("RGB", (cw * 3, ch * rows))
for i, f in enumerate(sel):
    im = Image.open(f).convert("RGB")
    w, h = im.size
    crop = im.crop((w // 2 - cw // 2, h // 2 - ch // 2 - 60, w // 2 + cw // 2, h // 2 + ch // 2 - 60))
    sheet.paste(crop, ((i % 3) * cw, (i // 3) * ch))
sheet.save(out)
print(len(fs), "frames; sheet of", len(sel))
