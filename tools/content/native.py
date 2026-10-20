"""Map an Azeroth Survivors EXE copy as a private image and call pure production functions.

Pattern copied from analysis/dps-math/native_combat.py (LoadLibraryEx DONT_RESOLVE + manual
import binding). No entry point, renderer or window runs. BelowNormal priority.
Usage: from native import load; n = load(path_to_exe_copy); n.drops(class_id, zone, boss)
"""
import ctypes as C
from ctypes import wintypes as W
import struct
import pefile

K = C.WinDLL('kernel32', use_last_error=True)
K.GetCurrentProcess.restype = W.HANDLE
K.SetPriorityClass.argtypes = [W.HANDLE, W.DWORD]
K.SetPriorityClass(K.GetCurrentProcess(), 0x4000)
K.LoadLibraryExW.argtypes = [W.LPCWSTR, W.HANDLE, W.DWORD]; K.LoadLibraryExW.restype = W.HMODULE
K.GetProcAddress.argtypes = [W.HMODULE, C.c_void_p]; K.GetProcAddress.restype = C.c_void_p
K.VirtualProtect.argtypes = [C.c_void_p, C.c_size_t, W.DWORD, C.POINTER(W.DWORD)]; K.VirtualProtect.restype = W.BOOL

CLASS_IDS = dict(warrior=1, paladin=2, hunter=3, rogue=4, priest=5, shaman=7, mage=8, warlock=9, druid=11)
RARITY = ['Common', 'Uncommon', 'Rare', 'Epic', 'Legendary', 'PvP']


class Native:
    def __init__(self, exe):
        self.base = K.LoadLibraryExW(str(exe), None, 1)
        if not self.base: raise OSError(C.get_last_error())
        pe = pefile.PE(str(exe), fast_load=True); pe.parse_data_directories(directories=[1])
        for e in pe.DIRECTORY_ENTRY_IMPORT:
            mod = K.LoadLibraryExW(e.dll.decode(), None, 0x800)
            for imp in e.imports:
                name = C.create_string_buffer(imp.name) if imp.name else None
                ptr = K.GetProcAddress(mod, C.cast(name, C.c_void_p) if name else C.c_void_p(imp.ordinal))
                at = self.base + imp.address - pe.OPTIONAL_HEADER.ImageBase; old = W.DWORD()
                K.VirtualProtect(at, 8, 4, C.byref(old)); C.c_void_p.from_address(at).value = ptr
                K.VirtualProtect(at, 8, old.value, C.byref(W.DWORD()))
        self._drops = self.fn(0x14589cd20, None, C.c_void_p, C.c_ubyte, C.c_size_t, C.c_size_t)

    def va(self, x): return self.base + x - 0x140000000
    def fn(self, x, res, *args): return C.CFUNCTYPE(res, *args)(self.va(x))

    def drops(self, class_id, zone, boss):
        """Production boss_drop_table(class, zone_idx, boss_idx) -> [(item_id, chance)] (recovered name)."""
        out = (C.c_uint64 * 3)()
        self._drops(C.addressof(out), class_id, zone, boss)
        cap, ptr, n = out
        return [struct.unpack('<If', C.string_at(ptr + 8 * i, 8)) for i in range(n)]


def decode_item(i):
    """Recovered encoding: 100000 + class*1000 + slot*100 + rarity*10 (slot 0-7 armor, 8+ weapons)."""
    r = i - 100000
    return dict(cls=r // 1000, slot=(r % 1000) // 100, rarity=(r % 100) // 10, rest=r % 10)


def load(exe): return Native(exe)
