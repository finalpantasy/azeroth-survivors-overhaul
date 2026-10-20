"""Check that an ItemDisplayInfo row's models, textures and icon ship in the MPQ chain.
Inputs: chain listing (mpq-inspect list) and ItemDisplayInfo.csv (mpq-inspect dbc).
Usage: python item_display_check.py <chain-list.txt> <ItemDisplayInfo.csv> <id> [<id>...]
       python item_display_check.py <chain-list.txt> <ItemDisplayInfo.csv> --grep <regex>
"""
import csv, re, sys

BS = chr(92)
REGION = dict(ArmUpperTexture='ArmUpperTexture', ArmLowerTexture='ArmLowerTexture', HandTexture='HandTexture',
              TorsoUpperTexture='TorsoUpperTexture', TorsoLowerTexture='TorsoLowerTexture',
              LegUpperTexture='LegUpperTexture', LegLowerTexture='LegLowerTexture', FootTexture='FootTexture')


def load(listing, idi_csv):
    files = set()
    for l in open(listing, encoding='utf8', errors='replace'):
        p = l.split(None, 1)
        if len(p) == 2: files.add(p[1].strip().lower())
    rows = {int(r['ID']): r for r in csv.DictReader(open(idi_csv, encoding='utf8'))}
    return files, rows


def check(files, r):
    miss, have = [], []
    def need(path):
        (have if path.lower() in files else miss).append(path)
    for side in ('Left', 'Right'):
        m = r['ModelName' + side]
        if not m: continue
        folder = 'Head' if m.lower().startswith('helm') else 'Shoulder' if 'shoulder' in m.lower() else None
        if folder:
            base = 'Item' + BS + 'ObjectComponents' + BS + folder + BS
            stem = m[:-4]
            if folder == 'Head':  # helms ship per race/sex: accept any variant
                if not any(f.startswith((base + stem + '_').lower()) and f.endswith('.m2') for f in files): miss.append(base + stem + '_*.m2')
                else: have.append(base + stem + '_*.m2')
            else: need(base + stem + '.m2')
            t = r['ModelTexture' + side]
            if t: need(base + t + '.blp')
    for col, reg in REGION.items():
        t = r[col]
        if not t: continue
        base = ('Item' + BS + 'TextureComponents' + BS + reg + BS + t).lower()
        ok = any((base + s + '.blp') in files for s in ('_u', '_m', '_f', ''))
        (have if ok else miss).append('TC/' + reg + '/' + t)
    if r['Icon']: need('Interface' + BS + 'Icons' + BS + r['Icon'] + '.blp')
    else: miss.append('(no icon)')
    return miss, have


if __name__ == '__main__':
    files, rows = load(sys.argv[1], sys.argv[2])
    if sys.argv[3] == '--grep':
        rx = re.compile(sys.argv[4], re.I)
        ids = [i for i, r in rows.items() if rx.search(','.join(r.values()))]
    else:
        ids = [int(x) for x in sys.argv[3:]]
    for i in ids:
        r = rows.get(i)
        if not r: print(i, 'NO ROW'); continue
        miss, have = check(files, r)
        print(i, 'OK ' if not miss else 'MISS', r['Icon'] or '-', r['ModelNameLeft'] or '-',
              [r[c] for c in REGION if r[c]][:3], ('missing: ' + '; '.join(miss)) if miss else '')
