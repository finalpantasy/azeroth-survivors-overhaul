import sys, subprocess, tempfile
sys.path.insert(0, 'tools/characters')
from dbclib import DBC
from PIL import Image
BS = chr(92)
R = './'
x = DBC(open(R + 'analysis/characters/native-dbc/CreatureDisplayInfoExtra.dbc', 'rb').read())
INS = R + 'tools/mpq-inspect.exe'
rows = [r for r in x.rows if r[1] == 1 and r[2] == 0 and r[7] >= 3 and r[18]][:3]
out = Image.new('RGB', (3 * 256, 256))
for i, r in enumerate(rows):
    name = x.string(r[18])
    print(r[:9], name)
    t = tempfile.mktemp(suffix='.blp')
    res = subprocess.run([INS, R + 'Data', 'extract', 'Textures' + BS + 'BakedNpcTextures' + BS + name, t], capture_output=True, text=True)
    print(res.stdout[-120:], res.stderr[-120:])
    try:
        out.paste(Image.open(t).convert('RGB').resize((256, 256)), (i * 256, 0))
    except Exception as e:
        print('fail', e)
out.save(R + 'analysis/characters/lab/bake-sample.png')
