import sys,os
sys.path.insert(0,os.path.dirname(__file__))
from dbclib import DBC
ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__),'..','..'))
U=ROOT+'/analysis/characters/vanillahd-src/repo/DBFilesClient/'; N=ROOT+'/analysis/characters/native-dbc/'
for t,kn in (('CharHairGeosets',4),('CharacterFacialHairStyles',3)):
    n=DBC(open(N+t+'.dbc','rb').read()); u=DBC(open(U+t+'.dbc','rb').read())
    print(t,'native hdr row',n.rows[0],'up',u.rows[0])
    nk={tuple(r[:kn]) if t=='CharacterFacialHairStyles' else tuple(r[1:4]):r for r in n.rows}
    uk={tuple(r[:kn]) if t=='CharacterFacialHairStyles' else tuple(r[1:4]):r for r in u.rows}
    for race in range(1,9):
        for sex in (0,1):
            ks=[k for k in nk if k[0]==race and k[1]==sex]
            diff=[(k,nk[k],uk.get(k)) for k in ks if uk.get(k) is None or nk[k][-3:]!=uk[k][-3:] and t=='CharacterFacialHairStyles' or (t=='CharHairGeosets' and nk[k][4:]!=uk[k][4:])]
            extra=[k for k in uk if k[0]==race and k[1]==sex and k not in nk]
            print(race,sex,'native',len(ks),'diff',len(diff),'extra_up',len(extra))
            for d in diff[:3]: print('   ',d)
