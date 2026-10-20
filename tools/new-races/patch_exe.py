#!/usr/bin/env python3
"""Add a 9th playable race (Goblin, ChrRaces id 9) to the closed v0.103.0 EXE. Recovered layout, not source.

What the official EXE hard-codes (all found by static analysis, see analysis/new-races/FEASIBILITY.md):
  * RACES: [RaceDef; 8] at VA 0x146b8a820, stride 0x58:
      +0x00 name &str, +0x10 token &str (e.g. "SCOURGE"), +0x20 passives &[(u32 kind, f32 value)],
      +0x30 racial ability key &str, +0x40 tooltip lines &[&str], +0x50 id u64.
  * race lookup `add al,0xfe; cmp al,6; ja default; table[id-2]` (1 function + 7 inlined sites, 3 table copies),
    default = &RACES[0] (Human).
  * 3 loops over RACES with a byte bound `cmp rax, 0x2c0` (8 * 0x58).
  * 3 copies of the class -> allowed-races lists (Armory, roster sanitizer, mods), each a lengths table plus a
    pointer or rel32 table indexed by class-1.
This script appends a `.racex` section holding RACES9 (the 8 originals + Goblin), an 8-entry lookup table, new
class lists, and a rebuilt base-relocation directory (old blocks + blocks for the new absolute pointers); it then
repoints every site. Each site's original bytes are asserted before writing.

  python patch_exe.py <official exe> <out exe> [--report out.json]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

import capstone

OFFICIAL_SHA = "6382e9ae654295293b82d48fb07a1aa999132775bda10c6c6f80d973d2c1ddfb"
BASE = 0x140000000

RACES = 0x146B8A820
RACE_STRIDE = 0x58
# race lookup sites: (cmp-imm addresses holding `6`, table lea, default lea)
LOOKUP_SITES = [
    ([0x145866863], 0x14586686B, 0x145866877),  # race_info() function
    ([0x14588D089], 0x14588D090, 0x14588D09D),
    ([0x14590C466, 0x14590C483], 0x14590C48A, 0x14590C46A),
    ([0x14590DDA9], 0x14590DDB0, 0x14590DDBD),
    ([0x14591BE4D, 0x14591BE6C], 0x14591BE73, 0x14591BE51),
    ([0x145922A4F], 0x145922A5D, 0x145922A6A),
    ([0x14593DF3E], 0x14593DF45, 0x14593DF52),
]
OLD_TABLES = {0x146B92FF8, 0x146B97788, 0x14718D478}
# loops over RACES: (base lea, bound cmp)
ITER_SITES = [(0x145898EB0, 0x145898EA4), (0x1458EBF39, 0x1458EBF4D), (0x1458EC430, 0x1458EC622)]

# class -> races lists. 11 classes (index class-1); 6 and 10 unused (point at [1]).
OLD_LISTS = [[1, 2, 3, 4, 5, 6, 7, 8], [1, 3], [3, 4, 2, 6, 8], [1, 3, 4, 7, 2, 5, 8], [1, 3, 4, 5, 8], [1],
             [2, 6, 8], [1, 7, 5, 8], [1, 7, 2, 5], [1], [4, 6]]
# copies: armory (lens u64 @A, rel32 @B relative to B), chars (lens u64, ptr u64), mods (rel32 @B, lens u64)
ARMORY_LENS, ARMORY_REL = 0x145D6C908, 0x145D6C960
CHARS_LENS, CHARS_PTRS = 0x146B96C48, 0x146B96CA0
MODS_REL, MODS_LENS = 0x146B97810, 0x146B97840
# armory.rs faction rows: each row packs its 4 race ids in a u32 (Alliance 07040301, Horde 08060502) that the
# button loop stores at [rbp+0x348] and walks with `cmp rax,4; je done` at ARMORY_LOOP. [rbp+0x34c] is unused padding.
ARMORY_LOOP, ARMORY_LOOP_BYTES = 0x14016CEF0, bytes.fromhex("4883f8040f84660a0000")
ARMORY_BODY, ARMORY_DONE = 0x14016CEFA, 0x14016D960
HORDE_ROW = 0x08060502
# benilla-formats characters/customization.rs load_with: `for race in PLAYABLE_RACES` ([1..8] stored as a u64 at
# [rbp+0x40], walked with `cmp rax, 8`) fills the per-(race, sex) appearance ranges the Armory counters read.
RANGES_INIT, RANGES_INIT_BYTES = 0x145A31528, bytes.fromhex("48c7453808000000")  # mov qword [rbp+0x38], 8
RANGES_AFTER = 0x145A31530
RANGES_CMP, RANGES_CMP_BYTES = 0x145A3154E, bytes.fromhex("4883f808")  # cmp rax, 8


def new_race_lists(extra: dict[int, list[int]]) -> list[list[int]]:
    out = [list(x) for x in OLD_LISTS]
    for race, classes in extra.items():
        for c in classes:
            if race not in out[c - 1]:
                out[c - 1].append(race)
    return out


class PE:
    def __init__(self, data: bytearray):
        self.d = data
        self.lfanew = struct.unpack_from("<I", data, 0x3C)[0]
        self.fh = self.lfanew + 4
        self.nsec = struct.unpack_from("<H", data, self.fh + 2)[0]
        self.optsz = struct.unpack_from("<H", data, self.fh + 16)[0]
        self.oh = self.fh + 20
        self.sh = self.oh + self.optsz
        self.secs = []
        for i in range(self.nsec):
            o = self.sh + 40 * i
            name = bytes(data[o:o + 8]).rstrip(b"\0").decode()
            vs, va, rs, ro = struct.unpack_from("<IIII", data, o + 8)
            self.secs.append((name, va, vs, ro, rs))

    def off(self, va: int) -> int:
        rva = va - BASE
        for name, sva, vs, ro, rs in self.secs:
            if sva <= rva < sva + max(vs, rs) and rva - sva < rs:
                return ro + rva - sva
        raise ValueError(hex(va))

    def u64(self, va):
        return struct.unpack_from("<Q", self.d, self.off(va))[0]


md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)


def rip_target(pe: PE, va: int):
    o = pe.off(va)
    ins = next(md.disasm(bytes(pe.d[o:o + 16]), va))
    if ins.mnemonic != "lea" or "rip" not in ins.op_str:
        raise ValueError(f"{va:#x}: expected lea [rip], got {ins.mnemonic} {ins.op_str}")
    disp_off = ins.size - 4
    disp = struct.unpack_from("<i", pe.d, o + disp_off)[0]
    return ins, o + disp_off, va + ins.size + disp


def set_lea(pe: PE, va: int, expect_old: int | set, new: int, log: list):
    ins, doff, old = rip_target(pe, va)
    ok = old in expect_old if isinstance(expect_old, set) else old == expect_old
    if not ok:
        raise ValueError(f"{va:#x}: lea target {old:#x} not expected")
    struct.pack_into("<i", pe.d, doff, new - (va + ins.size))
    log.append({"site": hex(va), "kind": "lea", "old": hex(old), "new": hex(new)})


def set_cmp6(pe: PE, va: int, log: list, new_max: int):
    o = pe.off(va)
    b = bytes(pe.d[o:o + 3])
    if b[:2] == b"\x3c\x06":  # cmp al, 6
        pe.d[o + 1] = new_max
    elif b == b"\x80\xf9\x06":  # cmp cl, 6
        pe.d[o + 2] = new_max
    else:
        raise ValueError(f"{va:#x}: unexpected cmp bytes {b.hex()}")
    log.append({"site": hex(va), "kind": "cmp_imm", "old": 6, "new": new_max})


def set_bound(pe: PE, va: int, new: int, log: list):
    o = pe.off(va)
    if bytes(pe.d[o:o + 6]) != b"\x48\x3d\xc0\x02\x00\x00":
        raise ValueError(f"{va:#x}: unexpected bound bytes {bytes(pe.d[o:o+6]).hex()}")
    struct.pack_into("<I", pe.d, o + 2, new)
    log.append({"site": hex(va), "kind": "bound", "old": 0x2C0, "new": new})


class Blob:
    """The new section's contents, with a list of offsets holding absolute VAs (need DIR64 relocations)."""

    def __init__(self, va: int):
        self.va = va
        self.b = bytearray()
        self.abs = []

    def align(self, n):
        while len(self.b) % n:
            self.b += b"\0"

    def here(self):
        return self.va + len(self.b)

    def put(self, data: bytes, align=1):
        self.align(align)
        at = self.here()
        self.b += data
        return at

    def ptr(self, target: int):
        self.align(8)
        self.abs.append(len(self.b))
        self.b += struct.pack("<Q", target)

    def slice(self, target: int, n: int):
        self.ptr(target)
        self.b += struct.pack("<Q", n)


def build_racedef(blob: Blob, name_va, name_n, tok_va, tok_n, pas_va, pas_n, key_va, key_n, lines_va, lines_n, rid):
    blob.align(8)
    at = blob.here()
    blob.slice(name_va, name_n)
    blob.slice(tok_va, tok_n)
    blob.slice(pas_va, pas_n)
    blob.slice(key_va, key_n)
    blob.slice(lines_va, lines_n)
    blob.b += struct.pack("<Q", rid)
    assert blob.here() - at == RACE_STRIDE
    return at


def reloc_blocks(page_offsets: dict[int, list[int]]) -> bytes:
    out = bytearray()
    for page in sorted(page_offsets):
        ents = sorted(page_offsets[page])
        words = [(10 << 12) | (x & 0xFFF) for x in ents]
        if len(words) % 2:
            words.append(0)
        out += struct.pack("<II", page, 8 + 2 * len(words))
        out += struct.pack(f"<{len(words)}H", *words)
    return bytes(out)


GOBLIN = {
    "id": 9,
    "name": "Goblin",
    "token": "GOBLIN",
    # passive kinds (recovered): 0 max health, 1 damage, 2 cooldowns, 3 move speed, 4 crit, 5 damage reduction,
    # 6 health/sec, 7 pickup range, 8 experience, 9 gold, 10 luck, 11 heal per kill
    "passives": [(2, 0.05), (9, 0.20)],
    "racial": "berserking",  # reuses Troll's Berserking ability record (new ability records = table extension)
    "lines": [
        "Berserking: every 35 sec, cooldowns recover 30% faster for 10 sec",
        "Time Is Money: -5% cooldowns",
        "Best Deals Anywhere: +20% gold",
    ],
    "classes": [1, 3, 4, 5, 7, 8, 9],  # Warrior Hunter Rogue Priest Shaman Mage Warlock (Cataclysm set minus DK)
}


def patch(data: bytes, race: dict) -> tuple[bytes, dict]:
    if hashlib.sha256(data).hexdigest() != OFFICIAL_SHA:
        raise SystemExit("input is not the official v0.103.0 EXE")
    d = bytearray(data)
    pe = PE(d)
    log: list = []
    last = pe.secs[-1]
    sect_align = struct.unpack_from("<I", d, pe.oh + 32)[0]
    file_align = struct.unpack_from("<I", d, pe.oh + 36)[0]
    size_image = struct.unpack_from("<I", d, pe.oh + 56)[0]
    new_rva = size_image
    assert new_rva % sect_align == 0 and new_rva >= last[1] + last[2]
    blob = Blob(BASE + new_rva)

    # --- RACES9: copy the 8 shipped RaceDefs verbatim (their pointers keep pointing at .rdata strings)
    defs = []
    for i in range(8):
        src = RACES + i * RACE_STRIDE
        f = struct.unpack_from("<11Q", d, pe.off(src))
        assert f[10] == i + 1, f"RaceDef {i} id {f[10]}"
        defs.append(f)
    # racial key: reuse the shipped key string of the race that owns it
    key_src = next(f for f in defs if bytes(d[pe.off(f[6]):pe.off(f[6]) + f[7]]).decode() == race["racial"])
    # new strings
    s_name = blob.put(race["name"].encode())
    s_tok = blob.put(race["token"].encode())
    line_vas = [(blob.put(t.encode()), len(t.encode())) for t in race["lines"]]
    s_pas = blob.put(b"".join(struct.pack("<If", k, v) for k, v in race["passives"]), align=4)
    blob.align(8)
    lines_arr = blob.here()
    for va, n in line_vas:
        blob.slice(va, n)
    blob.align(16)
    races9 = blob.here()
    for f in defs:
        build_racedef(blob, f[0], f[1], f[2], f[3], f[4], f[5], f[6], f[7], f[8], f[9], f[10])
    build_racedef(blob, s_name, len(race["name"]), s_tok, len(race["token"]), s_pas, len(race["passives"]),
                  key_src[6], key_src[7], lines_arr, len(line_vas), race["id"])
    n_races = 9
    # lookup table for ids 2..9
    blob.align(8)
    table = blob.here()
    for i in range(1, n_races):
        blob.ptr(races9 + i * RACE_STRIDE)

    # --- armory: 5th button on the Horde row (code cave; the loop head jumps here, then back to body or exit)
    blob.align(16)
    cave = blob.here()
    code = bytearray()
    code += bytes.fromhex("4883f804")  # cmp rax, 4
    code += bytes.fromhex("7215")  # jb cont
    code += bytes.fromhex("7518")  # jne done
    code += bytes.fromhex("81bd48030000") + struct.pack("<I", HORDE_ROW)  # cmp dword [rbp+0x348], Horde row
    code += bytes.fromhex("750c")  # jne done
    code += bytes.fromhex("c6854c030000") + bytes([race["id"]])  # mov byte [rbp+0x34c], race id
    cont = len(code)
    code += b"\xe9" + struct.pack("<i", ARMORY_BODY - (cave + cont + 5))
    done = len(code)
    code += b"\xe9" + struct.pack("<i", ARMORY_DONE - (cave + done + 5))
    assert code[5] == cont - 6 and code[7] == done - 8 and code[0x13] == done - 0x14, (cont, done)
    blob.put(bytes(code))
    lo = pe.off(ARMORY_LOOP)
    assert bytes(d[lo:lo + 10]) == ARMORY_LOOP_BYTES
    d[lo:lo + 10] = b"\xe9" + struct.pack("<i", cave - (ARMORY_LOOP + 5)) + b"\x90" * 5
    log.append({"kind": "armory_loop_cave", "site": hex(ARMORY_LOOP), "cave": hex(cave), "code": code.hex()})

    # --- appearance ranges: 9th race byte at [rbp+0x48] (unused by the loader) and loop bound 8 -> 9
    blob.align(16)
    cave2 = blob.here()
    code2 = bytearray(bytes.fromhex("48c7453809000000"))  # mov qword [rbp+0x38], 9
    code2 += bytes.fromhex("c64548") + bytes([race["id"]])  # mov byte [rbp+0x48], race id
    code2 += b"\xe9" + struct.pack("<i", RANGES_AFTER - (cave2 + len(code2) + 5))
    blob.put(bytes(code2))
    lo = pe.off(RANGES_INIT)
    assert bytes(d[lo:lo + 8]) == RANGES_INIT_BYTES
    d[lo:lo + 8] = b"\xe9" + struct.pack("<i", cave2 - (RANGES_INIT + 5)) + b"\x90" * 3
    lo = pe.off(RANGES_CMP)
    assert bytes(d[lo:lo + 4]) == RANGES_CMP_BYTES
    d[lo + 3] = 9
    log.append({"kind": "ranges_loop_cave", "site": hex(RANGES_INIT), "cave": hex(cave2), "code": code2.hex(),
                "bound": hex(RANGES_CMP)})

    # --- class lists
    lists = new_race_lists({race["id"]: race["classes"]})
    list_vas = []
    for lst in lists:
        list_vas.append(blob.put(bytes(lst)))

    # --- patch lookup sites
    for cmps, tlea, dlea in LOOKUP_SITES:
        for c in cmps:
            set_cmp6(pe, c, log, 6 + (n_races - 8))
        set_lea(pe, tlea, OLD_TABLES, table, log)
        set_lea(pe, dlea, RACES, races9, log)
    for blea, bound in ITER_SITES:
        set_lea(pe, blea, RACES, races9, log)
        set_bound(pe, bound, n_races * RACE_STRIDE, log)

    # --- class list copies
    for ci, lst in enumerate(lists):
        # armory: u64 lens + rel32 from ARMORY_REL
        lo = pe.off(ARMORY_LENS + 8 * ci)
        assert struct.unpack_from("<Q", d, lo)[0] == len(OLD_LISTS[ci])
        struct.pack_into("<Q", d, lo, len(lst))
        struct.pack_into("<i", d, pe.off(ARMORY_REL + 4 * ci), list_vas[ci] - ARMORY_REL)
        # chars: u64 lens + u64 ptrs (existing relocation slots)
        lo = pe.off(CHARS_LENS + 8 * ci)
        assert struct.unpack_from("<Q", d, lo)[0] == len(OLD_LISTS[ci])
        struct.pack_into("<Q", d, lo, len(lst))
        struct.pack_into("<Q", d, pe.off(CHARS_PTRS + 8 * ci), list_vas[ci])
        # mods: rel32 from MODS_REL + u64 lens
        lo = pe.off(MODS_LENS + 8 * ci)
        assert struct.unpack_from("<Q", d, lo)[0] == len(OLD_LISTS[ci])
        struct.pack_into("<Q", d, lo, len(lst))
        struct.pack_into("<i", d, pe.off(MODS_REL + 4 * ci), list_vas[ci] - MODS_REL)
    log.append({"kind": "class_lists", "lists": lists})

    # --- relocation directory: old blocks + new DIR64 entries for blob.abs
    reloc_rva, reloc_size = struct.unpack_from("<II", d, pe.oh + 112 + 5 * 8)
    old_reloc = bytes(d[pe.off(BASE + reloc_rva):pe.off(BASE + reloc_rva) + reloc_size])
    pages: dict[int, list[int]] = {}
    for off in blob.abs:
        rva = new_rva + off
        pages.setdefault(rva & ~0xFFF, []).append(rva & 0xFFF)
    blob.align(16)
    new_reloc_rva = new_rva + len(blob.b)
    new_reloc = old_reloc + reloc_blocks(pages)
    blob.b += new_reloc

    # --- append section
    vsize = len(blob.b)
    rsize = (vsize + file_align - 1) // file_align * file_align
    raw_off = (len(d) + file_align - 1) // file_align * file_align
    d += b"\0" * (raw_off - len(d))
    d += bytes(blob.b) + b"\0" * (rsize - vsize)
    sh = pe.sh + 40 * pe.nsec
    assert sh + 40 <= struct.unpack_from("<I", d, pe.oh + 60)[0], "no room for a section header"
    assert bytes(d[sh:sh + 40]) == b"\0" * 40
    struct.pack_into("<8sIIIIIIHHI", d, sh, b".racex", vsize, new_rva, rsize, raw_off, 0, 0, 0, 0, 0x60000060)  # code + data, read + execute
    struct.pack_into("<H", d, pe.fh + 2, pe.nsec + 1)
    struct.pack_into("<I", d, pe.oh + 56, (new_rva + vsize + sect_align - 1) // sect_align * sect_align)
    struct.pack_into("<II", d, pe.oh + 112 + 5 * 8, new_reloc_rva, len(new_reloc))
    struct.pack_into("<I", d, pe.oh + 64, 0)  # checksum (not checked for user-mode EXEs)

    report = {
        "input_sha256": OFFICIAL_SHA, "output_sha256": hashlib.sha256(d).hexdigest(),
        "section": {"name": ".racex", "rva": hex(new_rva), "vsize": vsize, "raw_off": hex(raw_off)},
        "races9_va": hex(races9), "lookup_table_va": hex(table),
        "new_absolute_pointers": len(blob.abs), "reloc": {"old_size": reloc_size, "new_size": len(new_reloc),
                                                          "new_rva": hex(new_reloc_rva)},
        "race": {k: v for k, v in race.items()}, "sites": log,
    }
    return bytes(d), report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("exe")
    ap.add_argument("out")
    ap.add_argument("--report")
    a = ap.parse_args()
    out = Path(a.out)
    if out.exists():
        raise SystemExit(f"{out} exists; refusing to overwrite")
    data, rep = patch(Path(a.exe).read_bytes(), GOBLIN)
    out.write_bytes(data)
    if a.report:
        Path(a.report).write_text(json.dumps(rep, indent=2) + "\n")
    print(json.dumps({k: rep[k] for k in ("output_sha256", "section", "races9_va", "new_absolute_pointers")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
