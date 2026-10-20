import sys,os,collections
sys.path.insert(0,os.path.dirname(__file__))
from dbclib import DBC
from m2lib import M2
ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__),'..','..'))
N=ROOT+'/analysis/characters/native-dbc/'
hg=DBC(open(N+'CharHairGeosets.dbc','rb').read()); fh=DBC(open(N+'CharacterFacialHairStyles.dbc','rb').read())
for name,p in (('HD','analysis/characters/vanillahd-src/repo/Character/Human/Male/humanmale.m2'),('nat','analysis/characters/native-m2/HumanMale.m2')):
    m=M2(open(ROOT+'/'+p,'rb').read())
    print(name,sorted(s['id'] for s in m.submeshes()))
print('hair rows human male',[(r[3],r[4],r[5]) for r in hg.rows if r[1]==1 and r[2]==0])
print('facial rows human male',[(r[2],r[6],r[7],r[8]) for r in fh.rows if r[0]==1 and r[1]==0])
