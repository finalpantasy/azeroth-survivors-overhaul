import sys,os,collections
sys.path.insert(0,os.path.dirname(__file__))
from dbclib import DBC
from m2lib import M2
exec(open(os.path.join(os.path.dirname(__file__),'_probe3.py')).read().split('for race,sex')[0])
N=ROOT+'/analysis/characters/native-dbc/'; U=ROOT+'/analysis/characters/vanillahd-src/repo/DBFilesClient/'
nat_hg=DBC(open(N+'CharHairGeosets.dbc','rb').read()); nat_fh=DBC(open(N+'CharacterFacialHairStyles.dbc','rb').read())
up_hg=DBC(open(U+'CharHairGeosets.dbc','rb').read()); up_fh=DBC(open(U+'CharacterFacialHairStyles.dbc','rb').read())
rid={'Human':1,'Orc':2,'Dwarf':3,'Nightelf':4,'Scourge':5,'Tauren':6,'Gnome':7,'Troll':8}
def need_hair(hg,race,sex): return {(r[3]):r[4] for r in hg.rows if r[1]==race and r[2]==sex}
def need_fh(fh,race,sex): return {r[2]:(r[6],r[7],r[8]) for r in fh.rows if r[0]==race and r[1]==sex}
for race,sex,f,n in models:
    h=M2(open(repo+race+'/'+sex+'/'+f,'rb').read()); ids=set(s['id'] for s in h.submeshes())
    R=rid[race]; S=0 if sex=='Male' else 1
    out=[]
    for tag,hg,fh in (('native',nat_hg,nat_fh),('upstream',up_hg,up_fh)):
        bad_h=[(v,g) for v,g in need_hair(hg,R,S).items() if g not in (0,1) and g not in ids and v<100]
        bad_f=[]
        for v,(a,b,c) in need_fh(fh,R,S).items():
            for grp,val in ((100,a),(300,b),(200,c)):
                if val>1 and grp+val not in ids and val<100: bad_f.append((v,grp+val))
        out.append((tag,bad_h,bad_f))
    print(n,out)
