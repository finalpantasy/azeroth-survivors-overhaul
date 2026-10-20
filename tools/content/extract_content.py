"""Extract the compiled content tables of Azeroth Survivors v0.103.0 (recovered layouts, not source).

Zones   : 12 x 240 B at VA 0x146b89660 (index = match in fn 0x145866530)
Enemies : per zone slice (zone+0x78), 80 B each
Bosses  : per zone slice (zone+0x88), 88 B each
Gear    : 9 classes x 304 B at VA 0x146b9ccf0; weapons slice (class+0x78), 144 B each
Usage: python extract_content.py [EXE] [OUT.json]
"""
import json, struct, sys
from pe import PE, EXE

ZONES_VA, ZONE_N, ZONE_SZ = 0x146b89660, 12, 240
GEAR_VA, CLASS_N, CLASS_SZ = 0x146b9ccf0, 9, 304
CLASSES = ['warrior', 'paladin', 'hunter', 'rogue', 'priest', 'shaman', 'mage', 'warlock', 'druid']
SLOTS = ['head', 'shoulder', 'chest', 'waist', 'legs', 'feet', 'wrist', 'hands']


class R:
    def __init__(self, p): self.p = p
    def at(self, va, fmt): return struct.unpack_from('<' + fmt, self.p.data, self.p.va2off(va))
    def s(self, va):  # &str fat pointer
        ptr, n = self.at(va, 'QQ')
        return self.p.str_at_va(ptr, n).decode('utf8') if n else ''
    def sl(self, va): return self.at(va, 'QQ')


def zone(r, i):
    b = ZONES_VA + i * ZONE_SZ
    flag, radius = r.at(b, 'If')
    z = dict(index=i, va=hex(b), key=r.s(b + 8), name=r.s(b + 24), subtitle=r.s(b + 40),
             bounded=flag, radius=radius, loadscreen=r.s(b + 152), blurb=r.s(b + 168))
    z['u184'], z['f188'] = r.at(b + 184, 'If')
    z['map'], z['area'] = r.at(b + 192, 'II')
    z['spawn'] = list(r.at(b + 200, 'fff'))
    z['facing'] = r.at(b + 212, 'f')[0]
    z['f216'], z['u220'] = r.at(b + 216, 'fI')
    z['difficulty'], z['f228'] = r.at(b + 224, 'ff')
    z['indoor'] = r.at(b + 232, 'I')[0]
    z['extra_slices'] = {hex(o): r.sl(b + o)[1] for o in (56, 72, 88, 104)}
    ep, en = r.sl(b + 120)
    z['enemies'] = [enemy(r, ep + k * 80) for k in range(en)]
    bp, bn = r.sl(b + 136)
    z['bosses'] = [boss(r, bp + k * 88) for k in range(bn)]
    return z


def enemy(r, va):
    has, spell = r.at(va, 'II'); dmg, iv = r.at(va + 8, 'ff')
    eid, disp = r.at(va + 32, 'II'); scale, u44 = r.at(va + 40, 'fI')
    return dict(va=hex(va), id=eid, name=r.s(va + 16), display=disp, scale=scale, u44=u44,
                f48_f60=list(r.at(va + 48, 'fff')), u64=r.at(va + 64, 'I')[0], f68_f76=list(r.at(va + 68, 'fff')),
                ranged=dict(on=has, spell=spell, dmg=dmg, interval=iv))


def boss(r, va):
    ap, an = r.sl(va + 16)
    mech = list(r.p.str_at_va(ap, an)) if an else []
    bid, disp = r.at(va + 48, 'II'); scale, u60 = r.at(va + 56, 'fI')
    minute, hp = r.at(va + 64, 'ff'); dmg, speed = r.at(va + 72, 'ff'); u80, final = r.at(va + 80, 'II')
    return dict(va=hex(va), id=bid, name=r.s(va), yell=r.s(va + 32), mechanics=mech, mech_va=hex(ap),
                display=disp, scale=scale, u60=u60, minute=minute, hp=hp, damage=dmg, speed=speed,
                weapon_display=u80, final=final)


def gear(r, ci):
    b = GEAR_VA + ci * CLASS_SZ
    sets = [dict(name=r.s(b + k * 24), suffix=r.at(b + k * 24 + 16, 'Q')[0]) for k in range(5)]
    disp = r.at(b + 0x88, '40I')
    for t in range(5): sets[t]['displays'] = dict(zip(SLOTS, disp[t * 8:(t + 1) * 8]))
    wp, wn = r.sl(b + 0x78)
    weapons = []
    for k in range(wn):
        w = wp + k * 144
        bp_, bn_ = r.sl(w + 88)
        weapons.append(dict(va=hex(w), head=list(r.at(w, 'II')), names=[r.s(w + 8 + j * 16) for j in range(5)],
                            bytes88=list(r.p.str_at_va(bp_, bn_)) if bn_ else [],
                            item_class_sub_inv=list(r.at(w + 104, 'IIII')), displays=list(r.at(w + 120, '5I')),
                            u140=r.at(w + 140, 'I')[0]))
    return dict(cls=CLASSES[ci], va=hex(b), sets=sets, weapons=weapons, tail=hex(r.at(b + 0x128, 'I')[0]))


def extract(path=EXE):
    p = PE(path); r = R(p)
    return dict(exe=path, zones=[zone(r, i) for i in range(ZONE_N)], gear=[gear(r, c) for c in range(CLASS_N)])


if __name__ == '__main__':
    exe = sys.argv[1] if len(sys.argv) > 1 else EXE
    out = sys.argv[2] if len(sys.argv) > 2 else None
    d = extract(exe)
    s = json.dumps(d, indent=1, ensure_ascii=False)
    if out: open(out, 'w', encoding='utf8').write(s)
    for z in d['zones']:
        print(f"{z['index']:2} {z['key']:8} map{z['map']:<4} area{z['area']:<5} diff{z['difficulty']:.2f} u184={z['u184']} f216={z['f216']} u220={z['u220']} enemies={len(z['enemies'])}")
        for b in z['bosses']:
            print(f"     boss {b['id']} {b['name'][:26]:26} disp{b['display']:<6} min{b['minute']:<5} hp{b['hp']:<8} dmg{b['damage']:<5} scale{b['scale']:.2f} mech{b['mechanics']} u60={b['u60']} wpn{b['weapon_display']} final{b['final']}")
