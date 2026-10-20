"""Small MPQ v1 reader (stdlib only) and a wrapper around the first-party packer.

Reads the layouts this project cares about: v1 header, encrypted hash/block
tables, zlib/bzip2/uncompressed sectors, single-unit files. Anything else raises
``UnsupportedMpq`` so callers fail closed instead of guessing.
"""
from __future__ import annotations

import bz2
import struct
import subprocess
import sys
import zlib
from pathlib import Path
from typing import Any, Iterator

from .util import InstallerError

MPQ_MAGIC = b"MPQ\x1a"
FILE_EXISTS = 0x80000000
FILE_COMPRESS = 0x200
FILE_IMPLODE = 0x100
FILE_ENCRYPTED = 0x10000
FILE_SINGLE_UNIT = 0x1000000
FILE_DELETE_MARKER = 0x2000000
FILE_SECTOR_CRC = 0x4000000
FILE_PATCH = 0x100000
HASH_EMPTY = 0xFFFFFFFF
HASH_DELETED = 0xFFFFFFFE
U32 = 0xFFFFFFFF


class UnsupportedMpq(InstallerError):
    pass


def _crypt_table() -> list[int]:
    table = [0] * 0x500
    seed = 0x00100001
    for i1 in range(0x100):
        for i2 in range(i1, 0x500, 0x100):
            seed = (seed * 125 + 3) % 0x2AAAAB
            high = (seed & 0xFFFF) << 16
            seed = (seed * 125 + 3) % 0x2AAAAB
            table[i2] = high | (seed & 0xFFFF)
    return table


_CRYPT = _crypt_table()


def mpq_hash(name: str, kind: int) -> int:
    seed1, seed2 = 0x7FED7FED, 0xEEEEEEEE
    for byte in name.replace("/", "\\").upper().encode("ascii", "replace"):
        seed1 = (_CRYPT[(kind << 8) + byte] ^ (seed1 + seed2)) & U32
        seed2 = (byte + seed1 + seed2 + ((seed2 << 5) & U32) + 3) & U32
    return seed1


def _decrypt(data: bytes, key: int) -> bytes:
    count = len(data) // 4
    values = list(struct.unpack("<%dI" % count, data[: count * 4]))
    seed = 0xEEEEEEEE
    for i in range(count):
        seed = (seed + _CRYPT[0x400 + (key & 0xFF)]) & U32
        value = values[i] ^ ((key + seed) & U32)
        key = ((((~key) << 21) + 0x11111111) | (key >> 11)) & U32
        seed = (value + seed + ((seed << 5) & U32) + 3) & U32
        values[i] = value
    return struct.pack("<%dI" % count, *values) + data[count * 4:]


class MpqArchive:
    """Read-only view of one MPQ file."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._fh = open(self.path, "rb")
        try:
            self._load()
        except Exception:
            self._fh.close()
            raise

    def close(self) -> None:
        self._fh.close()

    def __enter__(self) -> "MpqArchive":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _load(self) -> None:
        head = self._fh.read(32)
        if len(head) < 32 or head[:4] != MPQ_MAGIC:
            raise UnsupportedMpq(f"Not an MPQ archive: {self.path.name}", "Is the file truncated or not an MPQ?")
        (_, header_size, archive_size, version, shift, hash_off, block_off,
         hash_count, block_count) = struct.unpack("<4sIIHHIIII", head)
        if version != 0:
            raise UnsupportedMpq(f"{self.path.name}: MPQ format version {version} is not supported (v1 only)")
        size = self.path.stat().st_size
        if hash_off + hash_count * 16 > size or block_off + block_count * 16 > size:
            raise UnsupportedMpq(f"{self.path.name}: hash/block tables extend past the end of the file")
        self.sector_size = 512 << shift
        self._fh.seek(hash_off)
        hashes = _decrypt(self._fh.read(hash_count * 16), mpq_hash("(hash table)", 3))
        self._fh.seek(block_off)
        blocks = _decrypt(self._fh.read(block_count * 16), mpq_hash("(block table)", 3))
        self.hashes = [struct.unpack_from("<IIHBBI", hashes, i * 16) for i in range(hash_count)]
        self.blocks = [struct.unpack_from("<IIII", blocks, i * 16) for i in range(block_count)]
        self.hash_count = hash_count

    def _find(self, name: str) -> tuple[int, int, int, int] | None:
        a, b = mpq_hash(name, 1), mpq_hash(name, 2)
        start = mpq_hash(name, 0) & (self.hash_count - 1)
        for step in range(self.hash_count):
            ha, hb, _loc, _plat, _res, block = self.hashes[(start + step) % self.hash_count]
            if block == HASH_EMPTY:
                return None
            if ha == a and hb == b and block != HASH_DELETED and block < len(self.blocks):
                return self.blocks[block]
        return None

    def has(self, name: str) -> bool:
        block = self._find(name)
        return block is not None and bool(block[3] & FILE_EXISTS) and not block[3] & FILE_DELETE_MARKER

    def read(self, name: str) -> bytes:
        block = self._find(name)
        if block is None or not block[3] & FILE_EXISTS or block[3] & FILE_DELETE_MARKER:
            raise KeyError(name)
        offset, csize, fsize, flags = block
        if flags & (FILE_ENCRYPTED | FILE_PATCH):
            raise UnsupportedMpq(f"{self.path.name}: {name} is encrypted or a patch file (unsupported)")
        self._fh.seek(offset)
        raw = self._fh.read(csize)
        if len(raw) != csize:
            raise UnsupportedMpq(f"{self.path.name}: {name} data is truncated")
        if not flags & (FILE_COMPRESS | FILE_IMPLODE):
            return raw[:fsize]
        if flags & FILE_IMPLODE:
            raise UnsupportedMpq(f"{self.path.name}: {name} uses PKWARE implode (unsupported)")
        if flags & FILE_SINGLE_UNIT:
            return self._decompress(raw, fsize, name)
        sectors = (fsize + self.sector_size - 1) // self.sector_size
        table_len = sectors + 1 + (1 if flags & FILE_SECTOR_CRC else 0)
        offsets = struct.unpack_from("<%dI" % table_len, raw, 0)
        out = bytearray()
        for i in range(sectors):
            chunk = raw[offsets[i]:offsets[i + 1]]
            expect = min(self.sector_size, fsize - i * self.sector_size)
            out += chunk if len(chunk) == expect else self._decompress(chunk, expect, name)
        if len(out) != fsize:
            raise UnsupportedMpq(f"{self.path.name}: {name} decompressed to the wrong size")
        return bytes(out)

    def _decompress(self, chunk: bytes, expect: int, name: str) -> bytes:
        if len(chunk) == expect:
            return chunk
        mask = chunk[0]
        body = chunk[1:]
        if mask == 0x02:
            data = zlib.decompress(body)
        elif mask == 0x10:
            data = bz2.decompress(body)
        else:
            raise UnsupportedMpq(f"{self.path.name}: {name} uses compression mask 0x{mask:02x} (unsupported)")
        if len(data) != expect:
            raise UnsupportedMpq(f"{self.path.name}: {name} sector size mismatch")
        return data

    def names(self) -> list[str]:
        """File names from the internal (listfile); raises when it is absent."""
        if not self.has("(listfile)"):
            raise UnsupportedMpq(f"{self.path.name} has no (listfile); its contents cannot be enumerated")
        text = self.read("(listfile)").decode("ascii", "replace")
        return [line.strip() for line in text.replace("\r", "\n").split("\n") if line.strip()]


class ChainReader:
    """Resolve names through several archives; later archives in ``paths`` win."""

    def __init__(self, paths: list[Path]) -> None:
        self.archives = [MpqArchive(p) for p in paths]

    def close(self) -> None:
        for a in self.archives:
            a.close()

    def read(self, name: str) -> tuple[bytes, Path]:
        for archive in reversed(self.archives):
            if archive.has(name):
                return archive.read(name), archive.path
        raise KeyError(name)


def official_chain(data_dir: Path, exclude: list[str]) -> list[Path]:
    """patch.MPQ then patch-N.MPQ in numeric order, minus installer-owned slots."""
    skip = {e.casefold() for e in exclude}
    found: list[tuple[int, Path]] = []
    for p in data_dir.iterdir():
        low = p.name.casefold()
        if low in skip or not low.endswith(".mpq") or not low.startswith("patch"):
            continue
        stem = low[len("patch"):-len(".mpq")]
        if stem == "":
            found.append((-1, p))
        elif stem.startswith("-") and stem[1:].isdigit():
            found.append((int(stem[1:]), p))
    return [p for _, p in sorted(found)]


def pack_tree(packer: Path, tree: Path, output: Path, timeout: int = 300) -> str:
    """Run the first-party deterministic packer; ``output`` must not exist."""
    try:
        done = subprocess.run([sys.executable, str(packer), str(tree), str(output)],
                              capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise InstallerError("pack-mpq.py timed out", "Check disk space and antivirus interference.", "pack-timeout") from None
    if done.returncode != 0:
        raise InstallerError("pack-mpq.py failed: " + (done.stderr or done.stdout).strip()[-800:], code="pack-failed")
    return done.stdout.strip()


def read_all(path: Path) -> dict[str, bytes]:
    """Every file of an archive keyed by its internal path (needs a (listfile))."""
    with MpqArchive(path) as archive:
        return {name: archive.read(name) for name in archive.names()}


def iter_names(path: Path) -> Iterator[str]:
    with MpqArchive(path) as archive:
        yield from archive.names()
