"""Find coherent item-display families with every slot usable (models, textures, icon present).
Usage: python set_families.py <chain-list.txt> <ItemDisplayInfo.csv> <content.json> <armor regex e.g. Plate|Mail>"""
import sys, re, json, collections
from item_display_check import load, check
files, rows = load(sys.argv[1], sys.argv[2])
used = set()
for g in json.load(open(sys.argv[3], encoding='utf8'))['gear']:
    for s in g['sets']: used |= set(s['displays'].values())
arm = sys.argv[4]
def slot(r):
    m = r['ModelNameLeft'].lower()
    if m.startswith('helm'): return 'head'
    if 'shoulder' in m: return 'shoulder'
    if r['TorsoUpperTexture'] and r['ArmUpperTexture'] is not None and '_chest_' in r['TorsoUpperTexture'].lower(): return 'chest'
    if '_belt_' in r['LegUpperTexture'].lower(): return 'waist'
    if '_pant_' in r['LegUpperTexture'].lower(): return 'legs'
    if '_boot_' in (r['FootTexture'] + r['LegLowerTexture']).lower(): return 'feet'
    if '_glove_ha' in r['HandTexture'].lower(): return 'hands'
    if r['ArmLowerTexture'] and not r['HandTexture'] and not r['TorsoUpperTexture']: return 'wrist'
fam = collections.defaultdict(lambda: collections.defaultdict(list))
for i, r in rows.items():
    if i in used: continue
    blob = ' '.join([r['ModelTextureLeft'], r['TorsoUpperTexture'], r['LegUpperTexture'], r['FootTexture'], r['HandTexture'], r['ArmLowerTexture'], r['LegLowerTexture']])
    m = re.search(r'(' + arm + r')_([A-D]_\d+[A-Za-z]*)', blob, re.I)
    s = slot(r)
    if not m or not s: continue
    if check(files, r)[0]: continue
    fam[m.group(1).lower() + '_' + m.group(2)][s].append((i, r['Icon']))
for k, v in sorted(fam.items(), key=lambda kv: -len(kv[1])):
    if len(v) >= 6: print(k, len(v), {s: v[s][:2] for s in v})
