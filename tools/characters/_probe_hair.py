import sys, subprocess, tempfile
sys.path.insert(0, 'tools/characters')
from dbclib import DBC
from pathlib import Path
from PIL import Image
BS = chr(92)
R = Path('.')
cs = DBC((R / 'analysis/characters/native-dbc/CharSections.dbc').read_bytes())
INS = str(R / 'tools/mpq-inspect.exe')
P = R / 'mod-source/characters-hd/payload'
race, sex = int(sys.argv[1]), int(sys.argv[2])
rows = [r for r in cs.rows if r[1] == race and r[2] == sex and r[3] == 3 and r[9] == 0 and r[5] == 0][:4]
out = Image.new('RGB', (256 * 4, 512), (255, 0, 255))
for i, r in enumerate(rows):
    p = cs.string(r[6])
    t = tempfile.mktemp(suffix='.blp')
    subprocess.run([INS, str(R / 'Data'), 'extract', p, t], capture_output=True)
    a = Image.open(t).convert('RGBA').resize((256, 256))
    hd = P.joinpath(*p.split(BS))
    b = Image.open(hd).convert('RGBA').resize((256, 256)) if hd.exists() else Image.new('RGBA', (256, 256), (0, 0, 0, 255))
    for k, im in enumerate((a, b)):
        bg = Image.new('RGBA', (256, 256), (255, 0, 255, 255))
        bg.alpha_composite(im)
        out.paste(bg.convert('RGB'), (i * 256, k * 256))
    print(r[4], p, hd.exists())
out.save(sys.argv[3])
