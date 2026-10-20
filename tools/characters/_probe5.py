import sys,os,collections
sys.path.insert(0,os.path.dirname(__file__))
from dbclib import DBC
ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__),'..','..'))
U=ROOT+'/analysis/characters/vanillahd-src/repo/DBFilesClient/'; N=ROOT+'/analysis/characters/native-dbc/'
repo=ROOT+'/analysis/characters/vanillahd-src/repo/'
idx={}
for root,_,fs in os.walk(repo+'Character'):
    for f in fs:
        rel=os.path.relpath(os.path.join(root,f),repo).replace('/',chr(92)).lower()
        idx[rel]=1
n=DBC(open(N+'CharSections.dbc','rb').read()); u=DBC(open(U+'CharSections.dbc','rb').read())
def key(r): return tuple(r[1:6])+(r[9],)
def tex(d,r): return tuple(d.string(x) for x in r[6:9])
uk={key(r):r for r in u.rows}
stat=collections.defaultdict(collections.Counter)
miss=collections.defaultdict(list)
for r in n.rows:
    if r[1]>8: continue
    k=key(r); nt=tex(n,r)
    cat=(r[1],r[2],r[3])
    if k not in uk: stat[cat]['no_up_row']+=1; continue
    ut=tex(u,uk[k])
    if ut==nt:
        ok=all((x=='' or x.lower() in idx) for x in nt)
        stat[cat]['same_all_in_tree' if ok else ('same_notree' )]+=1
        if not ok: miss[cat].append([x for x in nt if x and x.lower() not in idx])
        continue
    ok=all((x=='' or x.lower() in idx) for x in ut)
    stat[cat]['diff_up_ok' if ok else 'diff_up_missing']+=1
    if not ok: miss[cat].append([x for x in ut if x and x.lower() not in idx])
for c in sorted(stat): print(c,dict(stat[c]))
print()
for c in sorted(miss): print(c,len(miss[c]),miss[c][:2])
