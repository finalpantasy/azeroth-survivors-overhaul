import sys, os
sys.path.insert(0,'.')
from dbclib import DBC
BS=chr(92)
ND='../../analysis/characters/native-dbc/'
ext=DBC(open(ND+'CreatureDisplayInfoExtra.dbc','rb').read())
cdi=DBC(open(ND+'CreatureDisplayInfo.dbc','rb').read())
names={l.strip().split(BS)[-1].lower() for l in open(sys.argv[1])}
print(len(names), list(names)[:3])
rows=[e for e in ext.rows if e[18] and ext.string(e[18]).lower() in names]
print(len(rows))
used={r[3]:r[0] for r in cdi.rows if r[3]}
for e in rows: print(e[0],e[1],e[2],ext.string(e[18]), 'cdi', used.get(e[0],''))
