import sys,os,collections
sys.path.insert(0,os.path.dirname(__file__))
from m2lib import M2
ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__),'..','..'))
repo=ROOT+'/analysis/characters/vanillahd-src/repo/Character/'
nat=ROOT+'/analysis/characters/native-m2/'
models=[('Human','Male','humanmale.m2','HumanMale'),('Human','Female','HumanFemale.m2','HumanFemale'),('Orc','Male','orcmale.m2','OrcMale'),('Orc','Female','OrcFemale.m2','OrcFemale'),('Dwarf','Male','DwarfMale.m2','DwarfMale'),('Dwarf','Female','DwarfFemale.m2','DwarfFemale'),('Nightelf','Male','NightElfMale.m2','NightElfMale'),('Nightelf','Female','NightElfFemale.m2','NightElfFemale'),('Scourge','Male','SCOURGEMale.m2','ScourgeMale'),('Scourge','Female','ScourgeFemale.m2','ScourgeFemale'),('Tauren','Male','TaurenMale.m2','TaurenMale'),('Tauren','Female','TaurenFemale.m2','TaurenFemale'),('Gnome','Male','GnomeMale.m2','GnomeMale'),('Gnome','Female','GnomeFemale.m2','GnomeFemale'),('Troll','Male','TrollMale.m2','TrollMale'),('Troll','Female','TrollFemale.m2','TrollFemale')]
for race,sex,f,n in models:
    h=M2(open(repo+race+'/'+sex+'/'+f,'rb').read()); o=M2(open(nat+n+'.m2','rb').read())
    hs=h.stats(); os_=o.stats()
    hid=sorted(set(s['id'] for s in h.submeshes())); oid=sorted(set(s['id'] for s in o.submeshes()))
    print(n,'HD v/t/b/bones/seq',hs['vertices'],hs['triangles_skin0'],hs['batches_skin0'],hs['bones'],hs['sequences'],'used',h.used_vertex_extent(),'| native',os_['vertices'],os_['triangles_skin0'],os_['batches_skin0'],os_['bones'],os_['sequences'],
          '| att',hs['attachments'],hs['attachment_lookup'],max(a for a,_ in h.attachments()))
    print('   only HD ids',[i for i in hid if i not in oid],' only native ids',[i for i in oid if i not in hid])
