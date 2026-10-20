"""Synthetic game folders and recipes for tests (no Blizzard data)."""
from __future__ import annotations

import hashlib
import struct
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from asinstall import mpq  # noqa: E402
from asinstall.steps import load_registry, resolve_tool  # noqa: E402


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def make_dbc(rows: list[tuple[int, str]]) -> bytes:
    strings = bytearray(b"\0")
    entries = []
    for rid, name in rows:
        entries.append((rid, len(strings)))
        strings += name.encode("ascii") + b"\0"
    body = b"".join(struct.pack("<II", a, b) for a, b in entries)
    return struct.pack("<4s4I", b"WDBC", len(rows), 2, 8, len(strings)) + body + bytes(strings)


def pack_files(files: dict[str, bytes], out: Path) -> Path:
    with tempfile.TemporaryDirectory() as t:
        tree = Path(t) / "tree"
        for name, data in files.items():
            p = tree.joinpath(*name.split("\\"))
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        mpq.pack_tree(resolve_tool(load_registry(), "mpq-packer"), tree, out)
    return out


EXE = b"MZ" + bytes(range(256)) * 40
BASE_DBC = make_dbc([(1, "alpha.mdx"), (2, "bravo.mdx")])
PATCHED_DBC = make_dbc([(1, "alpha.mdx"), (2, "bravo.mdx")])
DBC_PATH = "DBFilesClient\\Thing.dbc"
NEW_FILE = b"NEWMODEL" * 50


def edited_dbc() -> bytes:
    from asinstall.dbc import apply_edits
    return apply_edits(BASE_DBC, [{"row_id": 2, "column": 1, "type": "string", "expect_before": "bravo.mdx", "value": "charlie.mdx"}])


def synthetic_recipe(rid: str = "synthetic-overlay") -> dict:
    new_dbc = edited_dbc()
    return {
        "schema": "as-recipe/1", "id": rid, "version": "1.0.0", "title": "Synthetic", "description": "Test recipe", "status": "ready",
        "default": True,
        "requires": {"game": {"exe_sha256": [sha(EXE)]}},
        "sources": {
            "base": {"kind": "game-chain", "exclude_slots": ["patch-0.MPQ", "patch-1.MPQ"], "files": [{"path": DBC_PATH, "sha256": [sha(BASE_DBC)]}]},
            "extra": {"kind": "user-supplied", "instructions": "Put model.bin in a folder.",
                      "files": [{"name": "model.bin", "bytes": len(NEW_FILE), "sha256": sha(NEW_FILE)}]},
        },
        "steps": [
            {"id": "base", "op": "game-extract", "source": "base"},
            {"id": "extra", "op": "user-source", "source": "extra"},
            {"id": "dbc", "op": "dbc-edit", "input": "base:DBFilesClient/Thing.dbc", "output": "DBFilesClient/Thing.dbc",
             "expect_input_sha256": sha(BASE_DBC),
             "edits": [{"row_id": 2, "column": 1, "type": "string", "expect_before": "bravo.mdx", "value": "charlie.mdx"}],
             "expect_output_sha256": sha(new_dbc)},
        ],
        "outputs": {"mpq_files": [
            {"slot": "patch-0.MPQ", "path": DBC_PATH, "from": "dbc:DBFilesClient/Thing.dbc", "sha256": sha(new_dbc)},
            {"slot": "patch-0.MPQ", "path": "Item\\Thing\\model.bin", "from": "extra:model.bin", "sha256": sha(NEW_FILE)}]},
        "acceptance": [{"check": "dbc-cell", "slot": "patch-0.MPQ", "path": DBC_PATH, "row_id": 2, "column": 1, "type": "string", "equals": "charlie.mdx"}],
    }


class GameCase(unittest.TestCase):
    """Creates a throwaway fake game folder, work area, saves folder and model source folder."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        self.game = base / "game"
        (self.game / "Data").mkdir(parents=True)
        (self.game / "AzerothSurvivors.exe").write_bytes(EXE)
        pack_files({DBC_PATH: BASE_DBC, "Misc\\readme.txt": b"hello"}, self.game / "Data" / "patch.MPQ")
        (self.game / "benilla-config").mkdir()
        (self.game / "benilla-config" / "config.toml").write_text("a = 1\n")
        self.saves = base / "saves"
        self.saves.mkdir()
        (self.saves / "characters.txt").write_text("hero\n")
        self.sources = base / "sources"
        self.sources.mkdir()
        (self.sources / "model.bin").write_bytes(NEW_FILE)
        self.work = base / "work"
        self.work.mkdir()
        self.n = 0

    def work_dir(self) -> Path:
        self.n += 1
        return self.work / f"b{self.n}"

    def file_hash(self, rel: str) -> str | None:
        p = self.game / rel
        return sha(p.read_bytes()) if p.exists() else None
