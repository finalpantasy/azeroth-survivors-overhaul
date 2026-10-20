import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

from helpers import ROOT  # noqa: F401  (sets sys.path)
from asinstall.util import InstallerError, heavy_lock, read_json, safe_rel, sha256_file


class HeavyLock(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.lock = Path(self._t.name) / "locks" / "single.lock"   # one slot; heavy.lock gets two

    def test_take_and_release(self):
        with heavy_lock(self.lock, "x"):
            self.assertEqual(json.loads(self.lock.read_text())["pid"], os.getpid())
        self.assertFalse(self.lock.exists())

    def test_live_holder_blocks_then_wait_succeeds(self):
        self.lock.parent.mkdir(parents=True)
        self.lock.write_text(json.dumps({"owner": "other", "pid": os.getpid(), "what": "w"}))
        with self.assertRaises(InstallerError) as cm:
            with heavy_lock(self.lock, "x"):
                pass
        self.assertEqual(cm.exception.code, "heavy-lock-held")
        threading.Timer(0.6, lambda: self.lock.unlink()).start()
        t0 = time.monotonic()
        with heavy_lock(self.lock, "x", wait=10):
            pass
        self.assertLess(time.monotonic() - t0, 5)

    def test_second_slot_then_full_and_exclusive(self):
        h = self.lock.with_name("heavy.lock")
        with heavy_lock(h, "a"):
            with heavy_lock(h, "b"):
                self.assertTrue(h.with_name("heavy-2.lock").exists())
                with self.assertRaises(InstallerError):
                    with heavy_lock(h, "c"):
                        pass
        self.assertEqual(list(h.parent.iterdir()), [])
        h.with_name("exclusive.lock").write_text("x")
        with self.assertRaises(InstallerError):
            with heavy_lock(h, "d"):
                pass

    def test_stale_lock_is_cleared(self):
        self.lock.parent.mkdir(parents=True)
        self.lock.write_text(json.dumps({"owner": "dead", "pid": 2**22 + 12345, "what": "w"}))
        with heavy_lock(self.lock, "x"):
            self.assertEqual(json.loads(self.lock.read_text())["owner"], "installer")

    def test_none_path_is_noop(self):
        with heavy_lock(None, "x"):
            pass


class Helpers(unittest.TestCase):
    def test_read_json_rejects_duplicate_keys(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "a.json"
            p.write_text('{"a": 1, "a": 2}')
            with self.assertRaises(InstallerError):
                read_json(p)

    def test_safe_rel(self):
        self.assertEqual(safe_rel("a/b.txt", "x"), "a/b.txt")
        for bad in ("../x", "/abs", "a/../b", "", "C:/x"):
            with self.assertRaises(InstallerError):
                safe_rel(bad, "x")

    def test_sha256_file(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "f"
            p.write_bytes(b"abc")
            self.assertEqual(sha256_file(p), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")


if __name__ == "__main__":
    unittest.main()
