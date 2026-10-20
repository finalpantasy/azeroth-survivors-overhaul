import sys,os,collections
sys.path.insert(0,os.path.dirname(__file__))
from m2lib import M2
exec(open(os.path.join(os.path.dirname(__file__),'_probe3.py')).read().split('for race,sex')[0])
for race,sex,f,n in models:
    h=M2(open(repo+race+'/'+sex+'/'+f,'rb').read()); o=M2(open(nat+n+'.m2','rb').read())
    print(n,'HD',h.textures(),'\n    native',o.textures())
