import sys,os
sys.path.insert(0,os.path.dirname(__file__))
from m2lib import M2
exec(open(os.path.join(os.path.dirname(__file__),'_probe3.py')).read().split('for race,sex')[0])
for race,sex,f,n in models:
    h=M2(open(repo+race+'/'+sex+'/'+f,'rb').read())
    at=h.attachments(); lk=h.attachment_lookup()
    ids=[a for a,_ in at]
    scan={}
    for i,(a,_) in enumerate(at): scan.setdefault(a,i)
    bad=[(i,lk[i],scan.get(i,0xFFFF)) for i in range(len(lk)) if lk[i]!=scan.get(i,0xFFFF)]
    print(n,'atts',len(at),'dupIDs',len(ids)-len(set(ids)),'maxid',max(ids),'lookup len',len(lk),'bad',bad[:6],len(bad))
