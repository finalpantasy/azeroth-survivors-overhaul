import sys,os
sys.path.insert(0,os.path.dirname(__file__))
from dbclib import DBC
import collections
ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__),'..','..'))
U=ROOT+'/analysis/characters/vanillahd-src/repo/DBFilesClient/'; N=ROOT+'/analysis/characters/native-dbc/'
n=DBC(open(N+'CharSections.dbc','rb').read()); u=DBC(open(U+'CharSections.dbc','rb').read())
def key(r): return tuple(r[1:6])
def tex(d,r): return tuple(d.string(x) for x in r[6:9])
nk={key(r):r for r in n.rows}; uk={}
for r in u.rows: uk.setdefault(key(r),[]).append(r)
repo=ROOT+'/analysis/characters/vanillahd-src/repo/'
idx={}
for root,_,fs in os.walk(repo+'Character'):
    for f in fs:
        rel=os.path.relpath(os.path.join(root,f),repo).replace('/',chr(92)).lower()
        idx[rel]=1
shown=collections.Counter()
for k in sorted(nk):
    if k[0]>8 or k not in uk: continue
    a=tex(n,nk[k]); b=tex(u,uk[k][0])
    if a!=b:
        cat=(k[0],k[1],k[2]); shown[cat]+=1
        if shown[cat]<=2:
            print(k,'\n  N',a,[x.lower() in idx for x in a if x],'\n  U',b,[x.lower() in idx for x in b if x],'dups',len(uk[k]))
print(sorted(shown.items()))
