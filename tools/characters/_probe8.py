import sys,os,collections
sys.path.insert(0,os.path.dirname(__file__))
from dbclib import DBC
from m2lib import M2
exec(open(os.path.join(os.path.dirname(__file__),'_probe3.py')).read().split('for race,sex')[0])
N=ROOT+'/analysis/characters/native-dbc/'; U=ROOT+'/analysis/characters/vanillahd-src/repo/DBFilesClient/'
nat_fh=DBC(open(N+'CharacterFacialHairStyles.dbc','rb').read());up_fh=DBC(open(U+'CharacterFacialHairStyles.dbc','rb').read())
for (race,sex,f,n,R,S) in (('Dwarf','Male','DwarfMale.m2','DwarfMale',3,0),('Nightelf','Male','NightElfMale.m2','NightElfMale',4,0),('Tauren','Male','TaurenMale.m2','TaurenMale',6,0),('Scourge','Female','ScourgeFemale.m2','ScourgeFemale',5,1)):
    h=M2(open(repo+race+'/'+sex+'/'+f,'rb').read()); o=M2(open(nat+n+'.m2','rb').read())
    print(n,'HD ids',sorted(set(s['id'] for s in h.submeshes())))
    print('   native ids',sorted(set(s['id'] for s in o.submeshes())))
    print('   native fh',[(r[2],r[6],r[7],r[8]) for r in nat_fh.rows if r[0]==R and r[1]==S])
    print('   up fh',[(r[2],r[6],r[7],r[8]) for r in up_fh.rows if r[0]==R and r[1]==S])
