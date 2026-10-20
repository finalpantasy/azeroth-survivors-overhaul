import tempfile
import unittest
from pathlib import Path

from helpers import BASE_DBC, pack_files, make_dbc
from asinstall import mpq
from asinstall.dbc import Dbc, apply_edits
from asinstall.util import InstallerError


class MpqRoundTrip(unittest.TestCase):
    def test_pack_and_read(self):
        with tempfile.TemporaryDirectory() as t:
            big = bytes(range(256)) * 200  # spans several sectors and compresses
            out = pack_files({"A\\b.bin": big, "A\\c.txt": b"x"}, Path(t) / "x.mpq")
            with mpq.MpqArchive(out) as arc:
                self.assertEqual(sorted(arc.names()), ["A\\b.bin", "A\\c.txt"])
                self.assertEqual(arc.read("a\\B.BIN"), big)  # case-insensitive, like the game
                self.assertFalse(arc.has("A\\missing"))
                with self.assertRaises(KeyError):
                    arc.read("A\\missing")

    def test_not_an_mpq(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "bad.mpq"
            p.write_bytes(b"not an archive at all, just text" * 4)
            with self.assertRaises(mpq.UnsupportedMpq):
                mpq.MpqArchive(p)

    def test_chain_later_archive_wins(self):
        with tempfile.TemporaryDirectory() as t:
            a = pack_files({"f.txt": b"old"}, Path(t) / "patch.MPQ")
            b = pack_files({"f.txt": b"new"}, Path(t) / "patch-2.MPQ")
            chain = mpq.ChainReader([a, b])
            self.assertEqual(chain.read("f.txt")[0], b"new")
            chain.close()

    def test_official_chain_order_and_exclusion(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            for n in ("patch.MPQ", "patch-10.MPQ", "patch-2.MPQ", "patch-0.MPQ", "other.MPQ"):
                (d / n).write_bytes(b"")
            names = [p.name for p in mpq.official_chain(d, ["patch-0.MPQ"])]
            self.assertEqual(names, ["patch.MPQ", "patch-2.MPQ", "patch-10.MPQ"])


class DbcEdits(unittest.TestCase):
    EDIT = {"row_id": 2, "column": 1, "type": "string", "expect_before": "bravo.mdx", "value": "charlie.mdx"}

    def test_string_edit_appends(self):
        new = apply_edits(BASE_DBC, [self.EDIT])
        self.assertEqual(Dbc(new).get_string(2, 1), "charlie.mdx")
        self.assertEqual(Dbc(new).get_string(1, 1), "alpha.mdx")
        self.assertEqual(len(new), len(BASE_DBC) + len("charlie.mdx") + 1)

    def test_expect_before_mismatch_fails(self):
        with self.assertRaises(InstallerError) as cm:
            apply_edits(BASE_DBC, [dict(self.EDIT, expect_before="wrong.mdx")])
        self.assertEqual(cm.exception.code, "dbc-mismatch")

    def test_missing_row_and_column(self):
        with self.assertRaises(InstallerError):
            apply_edits(BASE_DBC, [dict(self.EDIT, row_id=99)])
        with self.assertRaises(InstallerError):
            apply_edits(BASE_DBC, [dict(self.EDIT, column=5)])

    def test_truncated_dbc(self):
        with self.assertRaises(InstallerError):
            Dbc(BASE_DBC[:-3])

    def test_uint_cell(self):
        d = Dbc(make_dbc([(1, "a")]))
        d.set_uint(1, 1, 5)
        self.assertEqual(d.get_uint(1, 1), 5)


if __name__ == "__main__":
    unittest.main()
