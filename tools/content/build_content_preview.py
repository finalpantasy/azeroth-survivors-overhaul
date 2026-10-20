"""Stage the isolated content preview: previews/content-v0.103.0 (EXE copy + hardlinked Data).

Slice 1 (proposal, the maintainer's taste): "Westfall: The Harvest Rebellion" remixes the Elwynn slot
(key stays "elwynn" so unlocks, quick start and saves keep working), a new first boss
"Old Murk-Eye" with three mechanics composed from the existing mechanic kinds, and a new
rogue Common-tier set "Defias Renegade" that Old Murk-Eye (head) and the Elwynn-slot and
Duskwood bosses drop.

All edits are recovered-layout data edits (see analysis/content/EXPANSION.md):
  * in-place u32/f32 edits in .rdata (display ids, area, spawn, scale),
  * &str / &[Mechanic] fat pointers repointed into a new read-only section ".cpack",
  * locale keys removed (overwritten with JSON whitespace) in all 14 embedded locale tables
    so the game falls back to the repointed English strings.
Every in-place edit checks the original bytes first. The main install is only read.

The --lab flag also writes previews/content-v0.103.0/lab/AzerothSurvivors.exe: same as the
preview plus LAB-ONLY timing (Old Murk-Eye at 0:45, HP x0.5) and a 100% Common drop chance,
so one lab run can see spawn -> mechanics -> death -> drop deterministically.
Usage: python build_content_preview.py [--lab] [--slices N] [--base 0.103.0|0.103.1]
"""
import hashlib, json, os, re, struct, subprocess, sys
from pathlib import Path

ROOT = Path(r".")
SRC_DATA = ROOT / "AzerothSurvivors/Data"   # patch-2..8 are byte-identical in 0.103.1 (W5 rebase report)
BASE = 0x140000000
# Official builds. 0.103.1 moved the content tables by a uniform +0x20f0 (located by byte pattern 2026-10-19:
# zone0 spawn, drop chances, rogue displays, zone rarity all shift +0x20f0). Every edit still checks its old value.
BASES = {
    '0.103.0': dict(exe=ROOT / "AzerothSurvivors/AzerothSurvivors.exe",
                    sha="6382e9ae654295293b82d48fb07a1aa999132775bda10c6c6f80d973d2c1ddfb", shift=0),
    '0.103.1': dict(exe=ROOT / "previews/base-v0.103.1/AzerothSurvivors.exe",
                    sha="3b46bad92e7771afa61c93658fd850937834a40f3671af8b1a3e1d2aeb8b63c7", shift=0x20f0),
}
VERSION = sys.argv[sys.argv.index('--base') + 1] if '--base' in sys.argv else '0.103.0'
SRC_EXE, SRC_SHA, _SHIFT = BASES[VERSION]['exe'], BASES[VERSION]['sha'], BASES[VERSION]['shift']
OUT = ROOT / f"previews/content-v{VERSION}"

ZONE0 = 0x146b89660 + _SHIFT   # zones[0] = elwynn (240 B)
GEAR = 0x146b9ccf0 + _SHIFT    # class gear table, 9 x 304 B, rogue = index 3
CHANCE = 0x146B99D00 + _SHIFT  # f32[6] per-piece drop chance by rarity
MECH = struct.Struct('<II4ff')  # 28 B: kind, param, w2..w5, cooldown (recovered; see EXPANSION.md)


class Img:
    def __init__(self, data):
        self.d = bytearray(data)
        e = struct.unpack_from('<I', self.d, 0x3c)[0]
        self.e, self.nsec = e, struct.unpack_from('<H', self.d, e + 6)[0]
        self.opt = e + 24
        self.sh = self.opt + struct.unpack_from('<H', self.d, e + 20)[0]
        self.secs = [struct.unpack_from('<8sIIII', self.d, self.sh + 40 * i) for i in range(self.nsec)]
        self.log = []

    def off(self, va):
        rva = va - BASE
        for n, vs, v, rs, r in self.secs:
            if v <= rva < v + max(vs, rs): return r + rva - v
        raise ValueError(hex(va))

    def put(self, va, fmt, new, old, what):
        o = self.off(va); cur = struct.unpack_from('<' + fmt, self.d, o)
        if struct.pack('<' + fmt, *old) != struct.pack('<' + fmt, *cur):
            raise SystemExit(f"{what}: expected {old} at {va:#x}, found {cur}")
        struct.pack_into('<' + fmt, self.d, o, *new)
        self.log.append(dict(what=what, va=hex(va), old=list(old), new=list(new)))

    def str_at(self, va):
        p, n = struct.unpack_from('<QQ', self.d, self.off(va))
        return bytes(self.d[self.off(p):self.off(p) + n]).decode('utf8')

    def add_section(self, name, payload):
        n, vs, v, rs, r = self.secs[-1]
        va = (v + vs + 0xfff) & ~0xfff
        raw = len(self.d)
        assert raw % 0x200 == 0 and r + rs == raw, "unexpected overlay after last section"
        size = (len(payload) + 0x1ff) & ~0x1ff
        hdr = self.sh + 40 * self.nsec
        assert hdr + 40 <= struct.unpack_from('<I', self.d, self.opt + 60)[0], "no header room"
        struct.pack_into('<8sIIIIIIHHI', self.d, hdr, name, len(payload), va, size, raw, 0, 0, 0, 0, 0x40000040)
        struct.pack_into('<H', self.d, self.e + 6, self.nsec + 1)
        struct.pack_into('<I', self.d, self.opt + 56, (va + len(payload) + 0xfff) & ~0xfff)   # SizeOfImage
        self.d += payload + b'\0' * (size - len(payload))
        self.secs.append((name, len(payload), va, size, raw)); self.nsec += 1
        self.log.append(dict(what='add section ' + name.decode().strip('\0'), va=hex(BASE + va), size=len(payload)))
        return BASE + va


class Pack:
    """Builds the .cpack payload; returns VAs once the section base is known."""
    def __init__(self): self.b = bytearray(); self.items = []
    def add(self, blob):
        while len(self.b) % 8: self.b.append(0)
        o = len(self.b); self.b += blob; return o
    def s(self, text): b = text.encode('utf8'); return self.add(b), len(b)


def blank_locale_keys(img, keys):
    """Overwrite `"key": "value",` members with spaces in every embedded locale table (valid JSON whitespace)."""
    total = 0
    for k in keys:
        rx = re.compile(rb'"' + re.escape(k.encode()) + rb'": "(?:[^"\\]|\\.)*",')
        hits = [m.span() for m in rx.finditer(img.d)]
        if not hits: raise SystemExit("locale key not found: " + k)
        for a, b in hits: img.d[a:b] = b' ' * (b - a)
        total += len(hits)
        img.log.append(dict(what='locale key removed', key=k, tables=len(hits)))
    return total


def build(slices=2):
    src = SRC_EXE.read_bytes()
    if hashlib.sha256(src).hexdigest() != SRC_SHA: raise SystemExit(f"{SRC_EXE} is not v{VERSION} {SRC_SHA}")
    img = Img(src); pk = Pack()
    z = ZONE0
    ep, en = struct.unpack_from('<QQ', img.d, img.off(z + 120))
    bp, bn = struct.unpack_from('<QQ', img.d, img.off(z + 136))
    assert (en, bn) == (12, 5) and img.str_at(z + 8) == 'elwynn' and img.str_at(bp) == 'Hogger'

    # ---- strings and the new mechanics array (into .cpack) ----
    S = {}
    S['zone_name'] = pk.s("Westfall")
    S['zone_sub'] = pk.s("The Harvest Rebellion")
    S['zone_blurb'] = pk.s("The harvest golems stand silent, Riverpaw gnolls raid the fields and Old Murk-Eye "
                           "leads the coast murlocs inland. The Defias Brotherhood watches from Moonbrook. "
                           "Their leader waits for you at the end.")
    S['e600'] = pk.s("Goretusk"); S['e603'] = pk.s("Riverpaw Mystic")
    S['e606'] = pk.s("Great Goretusk"); S['e609'] = pk.s("Riverpaw Overseer")
    S['boss'] = pk.s("Old Murk-Eye")
    S['yell'] = pk.s("Mrglmrglmrgl! The tide takes the farmlands!")
    S['set'] = pk.s("Defias Renegade")
    mech = [  # kind, param, w2, w3, w4, w5, cooldown   (kinds recovered from the 60 shipped bosses;
              # shipped lists order bolt/ground first and the add summon (kind 2) last, kept here)
        (1, 116, 0, 0, 0, 0, 5.0),         # Frostbolt volley (spell 116); bolt count is a u32 in w2, set below
        (3, 122, 9.0, 1.8, 0, 0, 8.0),     # point-blank Frost Nova (spell 122), radius 9, 1.8 (Lokholar/Sapphiron style)
        (2, 8, 0, 0, 0, 0, 13.0),          # summon a pack of 8 adds (Hogger 5, Mother Fang 7)
    ]
    mb = bytearray()
    for m in mech: mb += MECH.pack(*m)
    struct.pack_into('<I', mb, 0 * 28 + 8, 6)     # 6 bolts (Marez Cowl 5, Azuregos 7)
    S['mech'] = (pk.add(bytes(mb)), len(mech))
    if slices >= 2: slice2_strings(pk, S)
    if slices >= 3: slice3_strings(pk, S)
    sec = img.add_section(b'.cpack\0\0', bytes(pk.b))
    va = lambda key: (sec + S[key][0], S[key][1])

    # ---- zone 0: Elwynn slot becomes Westfall ----
    for off, key, old in ((24, 'zone_name', 'Elwynn Forest'), (40, 'zone_sub', 'The Defias Uprising'), (168, 'zone_blurb', None)):
        cur = struct.unpack_from('<QQ', img.d, img.off(z + off))
        if old: assert img.str_at(z + off) == old
        img.put(z + off, 'QQ', va(key), cur, 'zone0 &str +%d' % off)
    img.put(z + 196, 'I', (40,), (12,), 'zone0 area Elwynn 12 -> Westfall 40')
    # spawn must sit > ~640 yd from every pruned WMO: a failed WMO stays 'pending' and the loading
    # screen never clears (lab run 2026-10-19 at -10200,1400 hung on 5 Westfall farm WMOs). See adt_placements.py.
    # Second move (lab run 3/E): at -11050,600 the boss stood stuck 7 yd from a tree in the treeline; the new point is
    # 59 yd from the nearest tree M2 and 650 yd from the nearest pruned WMO.
    img.put(z + 200, 'fff', (-11100.0, 475.0, 22.5), (-9396.0, -633.0, 68.0), 'zone0 spawn -> Westfall open field (ADT 31_52 z 22.5, pruned WMO 650 yd, tree 59 yd)')

    # ---- enemies: Westfall natives (displays verified present in the MPQ chain) ----
    for idx, eid, old_disp, new_disp, key, scale in ((0, 600, 26, 704, 'e600', None), (3, 603, 163, 204, 'e603', None),
                                                     (6, 606, 644, 703, 'e606', None), (9, 609, 163, 501, 'e609', None)):
        e = ep + idx * 80
        assert struct.unpack_from('<I', img.d, img.off(e + 32))[0] == eid
        img.put(e + 36, 'I', (new_disp,), (old_disp,), f'enemy {eid} display')
        img.put(e + 16, 'QQ', va(key), struct.unpack_from('<QQ', img.d, img.off(e + 16)), f'enemy {eid} name')

    # ---- boss 0: Hogger -> Old Murk-Eye (id 690 kept: save/bestiary/achievement paths unchanged) ----
    b = bp
    img.put(b + 0, 'QQ', va('boss'), struct.unpack_from('<QQ', img.d, img.off(b)), 'boss690 name')
    img.put(b + 16, 'QQ', (sec + S['mech'][0], S['mech'][1]), struct.unpack_from('<QQ', img.d, img.off(b + 16)), 'boss690 mechanics slice')
    img.put(b + 32, 'QQ', va('yell'), struct.unpack_from('<QQ', img.d, img.off(b + 32)), 'boss690 yell')
    img.put(b + 52, 'I', (391,), (384,), 'boss690 display Hogger 384 -> big blue murloc 391')
    img.put(b + 56, 'f', (3.2,), (3.5,), 'boss690 scale')

    # ---- gear: rogue tier 0 (Common) restyled as "Defias Renegade" ----
    rogue = GEAR + 3 * 304
    assert img.str_at(rogue) == "Footpad's"
    img.put(rogue, 'QQ', va('set'), struct.unpack_from('<QQ', img.d, img.off(rogue)), 'rogue set 0 name')
    img.put(rogue + 0x88, '8I', (8266, 9527, 9983, 12335, 9984, 9985, 12339, 14345),
            (0, 7836, 3719, 4473, 4469, 9773, 4471, 4470), 'rogue tier0 displays head..hands')

    keys = ['zone.elwynn.name', 'zone.elwynn.subtitle', 'zone.elwynn.blurb', 'enemy.600', 'enemy.603', 'enemy.606',
            'enemy.609', 'boss.690.name', 'boss.690.yell', 'set.rogue.0']
    if slices >= 2: keys += slice2_apply(img, sec, S)
    if slices >= 3: keys += slice3_apply(img, sec, S)
    blank_locale_keys(img, keys)
    return img


# ---------------- slice 2 (approved 2026-10-19): Redridge in the Duskwood slot ----------------
ZONE1 = ZONE0 + 240
# (id, old display, new display, old scale, new scale, name); displays verified usable (usable_displays.json)
S2_ENEMIES = [
    (700, 158, 487, 1.0, 1.0, "Redridge Mongrel"), (701, 137, 489, 1.0, 1.0, "Redridge Brute"),
    (702, 1091, 387, 0.6, 0.8, "Black Dragon Whelp"), (703, 203, 492, 1.0, 1.0, "Redridge Mystic"),
    (704, 1201, 385, 1.1, 1.0, "Murloc Flesheater"), (705, 202, 500, 1.0, 1.0, "Redridge Poacher"),
    (706, 388, 520, 0.35, 0.8, "Redridge Tarantula"), (707, 146, 386, 1.0, 1.0, "Murloc Tidecaller"),
    (708, 414, 500, 1.5, 1.5, "Redridge Bruiser"), (709, 523, 8249, 1.2, 0.7, "Black Dragonspawn"),
    (710, 366, 498, 1.7, 1.7, "Venomspine Tarantula"), (711, 15961, 1208, 0.6, 2.0, "Bellygrub"),
]
S2_SET = (4415, 4890, 4151, 3254, 16092, 16112, 3605, 3773)   # head..hands, item_display_check.py: all OK


def slice2_strings(pk, S):
    S['z1_name'] = pk.s("Redridge Mountains")
    S['z1_sub'] = pk.s("The Blackrock Raids")
    S['z1_blurb'] = pk.s("Blackrock orcs push down from the Burning Steppes. Redridge gnolls burn the farms, black "
                         "whelps nest in the hills, and Snarlflare circles over Three Corners.")
    for eid, *_, name in S2_ENEMIES: S[f'e{eid}'] = pk.s(name)
    S['b790'] = pk.s("Snarlflare")
    S['b790_yell'] = pk.s("The brood hungers! Burn, little ones, burn!")
    S['set_w'] = pk.s("Stonewatch")
    mech = [(1, 133, 0, 0, 0, 0, 5.0),            # Fireball volley (spell 133, as Jeklik/Thaurissan)
            (0, 5740, 0, 8.0, 1.6, 2.5, 8.0),     # Rain of Fire ground AoE (Doomguard Commander values)
            (2, 5, 0, 0, 0, 0, 14.0)]             # 5 adds (Naraxis's count)
    mb = bytearray()
    for m in mech: mb += MECH.pack(*m)
    struct.pack_into('<I', mb, 0 * 28 + 8, 4)      # 4 bolts
    struct.pack_into('<I', mb, 1 * 28 + 8, 2120)   # impact visual 2120
    S['b790_mech'] = (pk.add(bytes(mb)), len(mech))


def slice2_apply(img, sec, S):
    va = lambda key: (sec + S[key][0], S[key][1])
    z = ZONE1
    assert img.str_at(z + 8) == 'duskwood'
    for off, key, old in ((24, 'z1_name', 'Duskwood'), (40, 'z1_sub', 'Night of the Nightbane'), (168, 'z1_blurb', None)):
        if old: assert img.str_at(z + off) == old
        img.put(z + off, 'QQ', va(key), struct.unpack_from('<QQ', img.d, img.off(z + off)), 'zone1 &str +%d' % off)
    # Shadowfang Keep art does not fit Redridge: reuse zone 0's generic Eastern Kingdoms screen
    img.put(z + 152, 'QQ', struct.unpack_from('<QQ', img.d, img.off(ZONE0 + 152)),
            struct.unpack_from('<QQ', img.d, img.off(z + 152)), 'zone1 loadscreen -> loadscreeneasternkingdom')
    img.put(z + 196, 'I', (44,), (10,), 'zone1 area Duskwood 10 -> Redridge 44')
    # spawn_finder.py: Three Corners, 706 yd from the nearest pruned WMO, 12.6 yd height range over 80 yd
    img.put(z + 200, 'fff', (-9866.0, -1716.0, 23.2), (-10540.0, 180.0, 30.3), 'zone1 spawn -> Redridge, Three Corners')
    ep, en = struct.unpack_from('<QQ', img.d, img.off(z + 120))
    bp, bn = struct.unpack_from('<QQ', img.d, img.off(z + 136))
    assert (en, bn) == (12, 5)
    for i, (eid, od, nd, osc, nsc, _) in enumerate(S2_ENEMIES):
        e = ep + i * 80
        assert struct.unpack_from('<I', img.d, img.off(e + 32))[0] == eid
        img.put(e + 36, 'I', (nd,), (od,), f'enemy {eid} display')
        if osc != nsc: img.put(e + 40, 'f', (nsc,), (osc,), f'enemy {eid} scale')
        img.put(e + 16, 'QQ', va(f'e{eid}'), struct.unpack_from('<QQ', img.d, img.off(e + 16)), f'enemy {eid} name')
    b = bp
    assert img.str_at(b) == 'Naraxis'
    img.put(b + 0, 'QQ', va('b790'), struct.unpack_from('<QQ', img.d, img.off(b)), 'boss790 name')
    img.put(b + 16, 'QQ', (sec + S['b790_mech'][0], S['b790_mech'][1]), struct.unpack_from('<QQ', img.d, img.off(b + 16)), 'boss790 mechanics slice')
    img.put(b + 32, 'QQ', va('b790_yell'), struct.unpack_from('<QQ', img.d, img.off(b + 32)), 'boss790 yell')
    img.put(b + 52, 'If', (399, 3.0), (1091, 2.8), 'boss790 display spider 1091 -> black whelp 399, scale 3.0')
    img.put(b + 60, 'I', (2,), (1,), 'boss790 creature type beast -> dragonkin')
    warrior = GEAR
    assert img.str_at(warrior) == "Recruit's"
    img.put(warrior, 'QQ', va('set_w'), struct.unpack_from('<QQ', img.d, img.off(warrior)), 'warrior set 0 name')
    img.put(warrior + 0x88, '8I', S2_SET, (0, 26164, 16183, 3418, 3414, 25828, 4448, 25831), 'warrior tier0 displays head..hands')
    return (['zone.duskwood.name', 'zone.duskwood.subtitle', 'zone.duskwood.blurb', 'boss.790.name', 'boss.790.yell',
             'set.warrior.0'] + [f'enemy.{e[0]}' for e in S2_ENEMIES])


# ---------------- slice 3 (approved 2026-10-19 once W8's golem passed in-engine): Foe Reaper 4000 ----------------
# Westfall boss 1 (Mother Fang, minute 6, drops the shoulder slot) becomes the harvest golem. The model is not in the
# shipped MPQs: W8 converted it (analysis/skinned-converter/out/golemharvest-v1/payload, Creature\GolemHarvest) and
# stage() packs it into the preview's Data/patch-9.MPQ. Blizzard-derived: LOCAL ONLY, never share the preview Data.
GOLEM_PAYLOAD = ROOT / "AzerothSurvivors/analysis/skinned-converter/out/golemharvest-v1/payload"


def slice3_strings(pk, S):
    S['b691'] = pk.s("Foe Reaper 4000")
    S['b691_yell'] = pk.s("Harvest protocol engaged. Removing pests from the field.")
    mech = [(4, 100, 0, 0, 0, 0, 8.0),            # charge (Hogger/Mor'Ladim)
            (3, 1680, 8.0, 1.6, 0, 0, 7.0),       # Whirlwind PBAoE (spell 1680, Mor'Ladim values)
            (2, 6, 0, 0, 0, 0, 14.0)]             # 6 adds
    mb = bytearray()
    for m in mech: mb += MECH.pack(*m)
    S['b691_mech'] = (pk.add(bytes(mb)), len(mech))


def slice3_apply(img, sec, S):
    va = lambda key: (sec + S[key][0], S[key][1])
    b = struct.unpack_from('<Q', img.d, img.off(ZONE0 + 136))[0] + 88
    assert img.str_at(b) == 'Mother Fang'
    img.put(b + 0, 'QQ', va('b691'), struct.unpack_from('<QQ', img.d, img.off(b)), 'boss691 name')
    img.put(b + 16, 'QQ', (sec + S['b691_mech'][0], S['b691_mech'][1]), struct.unpack_from('<QQ', img.d, img.off(b + 16)), 'boss691 mechanics slice')
    img.put(b + 32, 'QQ', va('b691_yell'), struct.unpack_from('<QQ', img.d, img.off(b + 32)), 'boss691 yell')
    img.put(b + 52, 'If', (548, 1.8), (7536, 0.83), 'boss691 display spider 7536 -> harvest golem 548 (W8 conversion), scale 1.8')
    img.put(b + 60, 'I', (9,), (1,), 'boss691 creature type beast -> mechanical')
    return ['boss.691.name', 'boss.691.yell']


def pack_golem(folder):
    """Data/patch-9.MPQ = the golem payload only (the shipped chain ends at patch-8; the engine also reads patch-9)."""
    out = folder / "Data" / "patch-9.MPQ"
    if out.exists(): out.unlink()
    subprocess.run([sys.executable, str(ROOT / "AzerothSurvivors/tools/pack-mpq.py"), str(GOLEM_PAYLOAD), str(out)],
                   check=True, capture_output=True)
    return out


def lab_patches(img, slices=2):
    b = struct.unpack_from('<Q', img.d, img.off(ZONE0 + 136))[0]
    img.put(b + 64, 'ff', (0.5, 300.0), (3.0, 3600.0), 'LAB ONLY boss690 at 0:30, HP 300 (autoplay must reach the kill and drop)')
    if slices >= 2:
        b = struct.unpack_from('<Q', img.d, img.off(ZONE1 + 136))[0]
        img.put(b + 64, 'ff', (0.5, 300.0), (3.0, 3600.0), 'LAB ONLY boss790 at 0:30, HP 300')
    if slices >= 3:
        b = struct.unpack_from('<Q', img.d, img.off(ZONE0 + 136))[0] + 88
        img.put(b + 64, 'ff', (1.0, 300.0), (6.0, 9000.0), 'LAB ONLY boss691 at 1:00, HP 300')
    img.put(CHANCE, 'f', (1.0,), (0.6,), 'LAB ONLY Common drop chance 60% -> 100%')


def sha(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for c in iter(lambda: f.read(1 << 20), b''): h.update(c)
    return h.hexdigest()


def stage(img, folder, data_link=True):
    folder.mkdir(parents=True, exist_ok=True)
    exe = folder / "AzerothSurvivors.exe"
    exe.write_bytes(bytes(img.d))
    if data_link:
        (folder / "Data").mkdir(exist_ok=True)
        for f in sorted(SRC_DATA.iterdir()):
            dst = folder / "Data" / f.name
            if dst.exists() and os.path.samefile(f, dst): continue
            if dst.exists(): dst.unlink()
            os.link(f, dst)
    return exe


if __name__ == '__main__':
    slices = int(sys.argv[sys.argv.index('--slices') + 1]) if '--slices' in sys.argv else 3
    img = build(slices)
    exe = stage(img, OUT)
    golem = pack_golem(OUT) if slices >= 3 else None
    if slices < 3 and (OUT / "Data" / "patch-9.MPQ").exists(): (OUT / "Data" / "patch-9.MPQ").unlink()
    manifest = dict(source=str(SRC_EXE), source_sha256=SRC_SHA, exe=str(exe), exe_sha256=sha(exe),
                    data="hardlinks of " + str(SRC_DATA), edits=img.log)
    if '--lab' in sys.argv:
        n = len(img.log); lab_patches(img, slices)
        lexe = stage(img, OUT / "lab", data_link=False)
        manifest['lab_exe'] = str(lexe); manifest['lab_exe_sha256'] = sha(lexe); manifest['lab_edits'] = img.log[n:]
    manifest['slices'] = slices; manifest['base_version'] = VERSION
    if golem: manifest['patch9'] = dict(path=str(golem), sha256=sha(golem), source=str(GOLEM_PAYLOAD), note='Blizzard-derived, local only')
    (OUT / "content-manifest.json").write_text(json.dumps(manifest, indent=1), encoding='utf8')
    for e in img.log: print(e)
    print('staged', exe, manifest['exe_sha256'])
