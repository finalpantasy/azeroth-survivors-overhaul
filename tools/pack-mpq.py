#!/usr/bin/env python3
"""Build a deterministic MPQ v1 archive from a loose asset tree.

Files use standard sector tables. Each sector is stored raw when zlib would not
shrink it, or with the MPQ zlib mask when compression is smaller. Hash and block
tables are encrypted. This writer targets local mod archives and readback
validation with mpq-inspect; it does not add signatures or modify an archive.
"""

from __future__ import annotations

import argparse
import os
import struct
import sys
import zlib
from pathlib import Path


MPQ_MAGIC = b"MPQ\x1a"
MPQ_HEADER_SIZE = 32
MPQ_FORMAT_VERSION = 0
MPQ_SECTOR_SHIFT = 3
MPQ_FILE_EXISTS = 0x80000000
MPQ_FILE_COMPRESS = 0x00000200
MPQ_HASH_EMPTY = 0xFFFFFFFF
MPQ_HASH_DELETED = 0xFFFFFFFE
UINT32_MASK = 0xFFFFFFFF


def make_crypt_table() -> list[int]:
    table = [0] * 0x500
    seed = 0x00100001
    for index1 in range(0x100):
        for index2 in range(index1, 0x500, 0x100):
            seed = (seed * 125 + 3) % 0x2AAAAB
            high = (seed & 0xFFFF) << 16
            seed = (seed * 125 + 3) % 0x2AAAAB
            table[index2] = high | (seed & 0xFFFF)
    return table


CRYPT_TABLE = make_crypt_table()


def mpq_hash(name: str, hash_type: int) -> int:
    try:
        encoded = name.replace("/", "\\").upper().encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError(f"MPQ paths must use ASCII characters: {name!r}") from exc

    seed1 = 0x7FED7FED
    seed2 = 0xEEEEEEEE
    for byte in encoded:
        seed1 = (CRYPT_TABLE[(hash_type << 8) + byte] ^ (seed1 + seed2)) & UINT32_MASK
        seed2 = (byte + seed1 + seed2 + ((seed2 << 5) & UINT32_MASK) + 3) & UINT32_MASK
    return seed1


def encrypt_table(data: bytes, key: int) -> bytes:
    if len(data) % 4:
        raise ValueError("MPQ table byte length must be divisible by four")

    encrypted = bytearray(len(data))
    seed1 = key
    seed2 = 0xEEEEEEEE
    for offset in range(0, len(data), 4):
        seed2 = (seed2 + CRYPT_TABLE[0x400 + (seed1 & 0xFF)]) & UINT32_MASK
        plain = struct.unpack_from("<I", data, offset)[0]
        cipher = plain ^ ((seed1 + seed2) & UINT32_MASK)
        struct.pack_into("<I", encrypted, offset, cipher)
        seed1 = (((~seed1 << 21) + 0x11111111) | (seed1 >> 11)) & UINT32_MASK
        seed2 = (plain + seed2 + ((seed2 << 5) & UINT32_MASK) + 3) & UINT32_MASK
    return bytes(encrypted)


def internal_name(relative_path: Path) -> str:
    name = relative_path.as_posix().replace("/", "\\")
    if not name or name.startswith("\\") or ":" in name or "\x00" in name:
        raise ValueError(f"Unsafe internal MPQ path: {name!r}")
    if any(part in ("", ".", "..") for part in name.split("\\")):
        raise ValueError(f"Unsafe internal MPQ path: {name!r}")
    name.encode("ascii")
    return name


def collect_files(source_root: Path) -> list[tuple[str, Path, os.stat_result]]:
    root = source_root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"Source path is not a directory: {root}")

    entries: list[tuple[str, Path, os.stat_result]] = []
    seen: set[str] = set()
    for current, directories, filenames in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        for directory in directories:
            if (current_path / directory).is_symlink():
                raise ValueError(f"Symbolic-link directories are not supported: {current_path / directory}")
        directories.sort(key=str.casefold)
        for filename in filenames:
            source = current_path / filename
            if source.is_symlink():
                raise ValueError(f"Symbolic-link files are not supported: {source}")
            if not source.is_file():
                continue
            relative = source.relative_to(root)
            name = internal_name(relative)
            folded = name.casefold()
            if folded == "(listfile)":
                raise ValueError("The source tree must not supply an internal (listfile) file")
            if folded in seen:
                raise ValueError(f"Duplicate case-insensitive MPQ path: {name}")
            seen.add(folded)
            entries.append((name, source, source.stat()))

    entries.sort(key=lambda entry: entry[0].casefold())
    if not entries:
        raise ValueError(f"No regular files found under {root}")
    return entries


def next_power_of_two(value: int) -> int:
    result = 1
    while result < value:
        result <<= 1
    return result


def encode_sectors(payload: bytes) -> list[bytes]:
    sector_size = 512 << MPQ_SECTOR_SHIFT
    encoded: list[bytes] = []
    for offset in range(0, len(payload), sector_size):
        raw_sector = payload[offset : offset + sector_size]
        zlib_sector = zlib.compress(raw_sector)
        encoded.append(b"\x02" + zlib_sector if len(zlib_sector) + 1 < len(raw_sector) else raw_sector)
    return encoded


def read_source(source: Path, expected_stat: os.stat_result) -> bytes:
    payload = source.read_bytes()
    current_stat = source.stat()
    if len(payload) != expected_stat.st_size or current_stat.st_size != expected_stat.st_size:
        raise ValueError(f"Source changed while packing: {source}")
    if current_stat.st_mtime_ns != expected_stat.st_mtime_ns:
        raise ValueError(f"Source changed while packing: {source}")
    return payload


def write_entry(payload_sectors: list[bytes], output) -> None:
    if not payload_sectors:
        return
    offsets = [(len(payload_sectors) + 1) * 4]
    for sector in payload_sectors:
        offsets.append(offsets[-1] + len(sector))
    output.write(struct.pack("<" + "I" * len(offsets), *offsets))
    for sector in payload_sectors:
        output.write(sector)


def build_archive(source_root: Path, output_path: Path) -> tuple[int, int]:
    root = source_root.resolve(strict=True)
    output = output_path.resolve()
    try:
        output.relative_to(root)
    except ValueError:
        pass
    else:
        raise ValueError("Output archive must be outside the source tree")

    entries = collect_files(root)
    listfile = ("\r\n".join(name for name, _, _ in entries) + "\r\n").encode("ascii")
    archive_files: list[tuple[str, int, list[bytes], int]] = []
    for name, source, metadata in entries:
        payload_sectors = encode_sectors(read_source(source, metadata))
        stored_size = (len(payload_sectors) + 1) * 4 + sum(map(len, payload_sectors)) if payload_sectors else 0
        archive_files.append((name, metadata.st_size, payload_sectors, stored_size))
    listfile_sectors = encode_sectors(listfile)
    listfile_stored_size = (len(listfile_sectors) + 1) * 4 + sum(map(len, listfile_sectors))
    archive_files.append(("(listfile)", len(listfile), listfile_sectors, listfile_stored_size))
    archive_files.sort(key=lambda entry: entry[0].casefold())

    hash_table_size = next_power_of_two(max(4, len(archive_files) * 2))
    hash_entries: list[tuple[int, int, int, int, int, int]] = [
        (0, 0, 0, 0, 0, MPQ_HASH_EMPTY) for _ in range(hash_table_size)
    ]
    block_entries: list[tuple[int, int, int, int]] = []

    data_offset = MPQ_HEADER_SIZE
    for block_index, (name, size, payload_sectors, stored_size) in enumerate(archive_files):
        if size > UINT32_MASK:
            raise ValueError(f"MPQ v1 cannot store a file larger than 4 GiB: {name}")
        if stored_size > UINT32_MASK:
            raise ValueError(f"MPQ v1 cannot store this file's sector table: {name}")
        block_entries.append((data_offset, stored_size, size, MPQ_FILE_EXISTS | MPQ_FILE_COMPRESS))
        data_offset += stored_size

        slot = mpq_hash(name, 0) & (hash_table_size - 1)
        for _ in range(hash_table_size):
            if hash_entries[slot][5] == MPQ_HASH_EMPTY:
                hash_entries[slot] = (
                    mpq_hash(name, 1),
                    mpq_hash(name, 2),
                    0,
                    0,
                    0,
                    block_index,
                )
                break
            slot = (slot + 1) & (hash_table_size - 1)
        else:
            raise ValueError("MPQ hash table is full")

    hash_offset = data_offset
    block_offset = hash_offset + hash_table_size * 16
    archive_size = block_offset + len(block_entries) * 16
    if archive_size > UINT32_MASK:
        raise ValueError("MPQ v1 cannot store an archive larger than 4 GiB")

    hash_plain = b"".join(struct.pack("<IIHBBI", *entry) for entry in hash_entries)
    block_plain = b"".join(struct.pack("<IIII", *entry) for entry in block_entries)
    hash_cipher = encrypt_table(hash_plain, mpq_hash("(hash table)", 3))
    block_cipher = encrypt_table(block_plain, mpq_hash("(block table)", 3))
    header = struct.pack(
        "<4sIIHHIIII",
        MPQ_MAGIC,
        MPQ_HEADER_SIZE,
        archive_size,
        MPQ_FORMAT_VERSION,
        MPQ_SECTOR_SHIFT,
        hash_offset,
        block_offset,
        hash_table_size,
        len(block_entries),
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as archive:
        archive.write(header)
        for _, _, payload_sectors, _ in archive_files:
            write_entry(payload_sectors, archive)
        archive.write(hash_cipher)
        archive.write(block_cipher)
        if archive.tell() != archive_size:
            raise ValueError(f"Archive length mismatch: wrote {archive.tell()}, expected {archive_size}")
        archive.flush()
        os.fsync(archive.fileno())

    return len(entries), archive_size


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create a deterministic MPQ v1 archive using standard sector tables."
    )
    parser.add_argument("source_dir", type=Path, help="directory whose relative paths become MPQ paths")
    parser.add_argument("output_mpq", type=Path, help="new archive path; existing files are never overwritten")
    args = parser.parse_args()

    try:
        file_count, archive_size = build_archive(args.source_dir, args.output_mpq)
    except (OSError, ValueError, struct.error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"created {args.output_mpq} ({file_count} source files, {archive_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
