import sys
from collections import Counter
sys.path.insert(0,'.')
from dbclib import DBC
ND='../../analysis/characters/native-dbc/'
cs=DBC(open(ND+'CharSections.dbc','rb').read())
hdcs=DBC(open('../../mod-source/characters-hd/payload/DBFilesClient/CharSections.dbc','rb').read())
ext=DBC(open(ND+'CreatureDisplayInfoExtra.dbc','rb').read())
cdi=DBC(open(ND+'CreatureDisplayInfo.dbc','rb').read())
print('cs rows',len(cs.rows),'hd rows',len(hdcs.rows), 'flags',Counter(r[9] for r in cs.rows))
changed=[(a,b) for a,b in zip(cs.rows,hdcs.rows) if a!=b]
print('changed rows',len(changed),'by flags',Counter(a[9] for a,b in changed),'by type',Counter(a[3] for a,b in changed))
idx={}
for r in cs.rows: idx.setdefault((r[1],r[2],r[3],r[4],r[5]),[]).append(r)
used=Counter(r[3] for r in cdi.rows if r[3])
print('cdi displays with extra',sum(used.values()),'distinct extras',len(used))
stat=Counter()
for e in ext.rows:
    race,sex,skin,face,hs,hc,fh=e[1],e[2],e[3],e[4],e[5],e[6],e[7]
    if (race,sex) not in {(r[1],r[2]) for r in cs.rows if r[3]==0}: stat['non-player race/sex']+=1; continue
    n=1 if e[0] in used else 0
    keys=[('skin',(race,sex,0,0,skin)),('face',(race,sex,1,face,skin)),('uw',(race,sex,4,0,skin))]
    if fh: keys.append(('fh',(race,sex,2,fh,hc)))
    for nm,k in keys:
        rs=idx.get(k)
        if not rs: stat[nm+' nomatch']+=1
        else:
            fl=tuple(sorted(set(r[9] for r in rs)))
            stat[nm+' flags'+str(fl)]+=1
            if n: stat[nm+' USED flags'+str(fl)]+=1
for k,v in sorted(stat.items()): print(k,v)
