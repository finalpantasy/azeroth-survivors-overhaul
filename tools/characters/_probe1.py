import sys,os
sys.path.insert(0,os.path.dirname(__file__))
from dbclib import DBC
ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__),'..','..'))
U=ROOT+'/analysis/characters/vanillahd-src/repo/DBFilesClient/'; N=ROOT+'/analysis/characters/native-dbc/'
n=DBC(open(N+'CharSections.dbc','rb').read()); u=DBC(open(U+'CharSections.dbc','rb').read())
for name,d in (('N',n),('U',u)):
    print(name)
    for r in d.rows:
        if r[1]==1 and r[2]==0 and r[3]==0:
            print(r[:6],[d.string(x) for x in r[6:9]],r[9])
