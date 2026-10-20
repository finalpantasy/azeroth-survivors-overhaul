import base64
import json
import unittest
from unittest import mock

from helpers import EXE, NEW_FILE, GameCase, pack_files, sha, synthetic_recipe, DBC_PATH, BASE_DBC
from asinstall import detect, engine, mpq, txn
from asinstall.recipe import validate_recipe
from asinstall.steps import load_registry
from asinstall.util import InstallerError, Progress


def exe_recipe(rid="synthetic-exe"):
    new = bytearray(EXE)
    new[10:14] = b"ABCD"
    return validate_recipe({
        "schema": "as-recipe/1", "id": rid, "version": "1.0.0", "title": "exe", "description": "d", "status": "experimental",
        "requires": {"game": {"exe_sha256": [sha(EXE)]}},
        "outputs": {"exe_patches": [{"target": "AzerothSurvivors.exe", "expect_exe_sha256": sha(EXE), "result_exe_sha256": sha(bytes(new)),
                                     "edits": [{"offset": 10, "length": 4, "expect_sha256": sha(EXE[10:14]),
                                                "new_b64": base64.b64encode(b"ABCD").decode()}]}]},
        "acceptance": [{"check": "exe-sha256", "equals": sha(bytes(new))}],
    }), sha(bytes(new))


class InstallFlow(GameCase):
    def recipe(self):
        return validate_recipe(synthetic_recipe(), None, load_registry())

    def build(self, recipes=None, foreign="refuse", sources=True):
        return engine.build(game_root=self.game, client_root=None, recipes=recipes or [self.recipe()], work=self.work_dir(),
                            foreign=foreign, user_sources={"extra": self.sources} if sources else {})

    def install(self, build_dir):
        b = engine.load_build(build_dir)
        return txn.apply_build(b, build_dir, self.game, saves_dir=self.saves, progress=Progress(False))

    def do(self, recipes=None, foreign="refuse"):
        recipes = recipes or [self.recipe()]
        work = self.work_dir()
        b = engine.build(game_root=self.game, client_root=None, recipes=recipes, work=work, foreign=foreign,
                         user_sources={"extra": self.sources})
        return work, txn.apply_build(b, work, self.game, saves_dir=self.saves, progress=Progress(False))

    # -- plan ---------------------------------------------------------------
    def test_plan_clean_game(self):
        plan = engine.build_plan(game_root=self.game, client_root=None, recipes=[self.recipe()], user_sources={"extra": self.sources})
        self.assertTrue(plan["ok"], plan["blockers"])
        self.assertEqual(plan["targets"][0]["mode"], "create")
        self.assertEqual(plan["backups"], [])
        self.assertFalse(plan["writes"])
        self.assertFalse((self.game / "Data" / "patch-0.MPQ").exists())

    def test_plan_blocks_on_missing_user_source(self):
        plan = engine.build_plan(game_root=self.game, client_root=None, recipes=[self.recipe()])
        self.assertFalse(plan["ok"])
        self.assertEqual(plan["blockers"][0]["code"], "user-source-missing")
        self.assertIn("model.bin", plan["blockers"][0]["hint"])

    def test_plan_blocks_wrong_game_build(self):
        (self.game / "AzerothSurvivors.exe").write_bytes(EXE + b"x")
        plan = engine.build_plan(game_root=self.game, client_root=None, recipes=[self.recipe()], user_sources={"extra": self.sources})
        self.assertIn("game-build", [b["code"] for b in plan["blockers"]])

    def test_plan_blocks_running_game(self):
        with mock.patch.object(detect, "game_running", return_value=["x"]):
            plan = engine.build_plan(game_root=self.game, client_root=None, recipes=[self.recipe()], user_sources={"extra": self.sources})
        self.assertIn("game-running", [b["code"] for b in plan["blockers"]])

    # -- build never writes the game -----------------------------------------------------
    def test_build_leaves_game_untouched(self):
        before = {p.name: sha(p.read_bytes()) for p in (self.game / "Data").iterdir()}
        work = self.work_dir()
        b = engine.build(game_root=self.game, client_root=None, recipes=[self.recipe()], work=work, user_sources={"extra": self.sources})
        self.assertEqual(before, {p.name: sha(p.read_bytes()) for p in (self.game / "Data").iterdir()})
        self.assertTrue((work / "build.json").is_file())
        self.assertEqual(b["targets"][0]["rel"], "Data/patch-0.MPQ")
        self.assertFalse((self.game / "mod-backups").exists())

    def test_build_rejects_work_inside_game(self):
        with self.assertRaises(InstallerError) as cm:
            engine.build(game_root=self.game, client_root=None, recipes=[self.recipe()], work=self.game / "work",
                         user_sources={"extra": self.sources})
        self.assertEqual(cm.exception.code, "work-inside")

    def test_build_fails_on_changed_game_data(self):
        pack_files({DBC_PATH: BASE_DBC + b"\0\0\0\0"}, self.game / "Data" / "patch-2.MPQ")  # later archive overrides the DBC
        with self.assertRaises(InstallerError) as cm:
            self.build()
        self.assertIn(cm.exception.code, ("game-data-mismatch", "dbc-invalid"))

    def test_build_fails_on_corrupt_user_source(self):
        (self.sources / "model.bin").write_bytes(NEW_FILE + b"!")
        with self.assertRaises(InstallerError) as cm:
            self.build()
        self.assertEqual(cm.exception.code, "user-source-hash")

    # -- apply / rollback ------------------------------------------------------------------
    def test_apply_then_rollback_restores_absent_slot(self):
        _, receipt = self.do()
        self.assertEqual(receipt["status"], "installed-and-verified")
        patch0 = self.game / "Data" / "patch-0.MPQ"
        with mpq.MpqArchive(patch0) as arc:
            self.assertEqual(arc.read("Item\\Thing\\model.bin"), NEW_FILE)
        st = txn.status(self.game)
        self.assertTrue(st["installed_targets"][0]["intact"])
        dry = txn.rollback(self.game, "latest", apply=False, saves_dir=self.saves, progress=Progress(False))
        self.assertFalse(dry["applied"])
        self.assertTrue(patch0.exists())
        res = txn.rollback(self.game, receipt["id"], apply=True, saves_dir=self.saves, progress=Progress(False))
        self.assertTrue(res["applied"])
        self.assertFalse(patch0.exists())
        self.assertEqual(txn.status(self.game)["installed_targets"], [])
        with self.assertRaises(InstallerError) as cm:
            txn.rollback(self.game, receipt["id"], apply=True, saves_dir=self.saves, progress=Progress(False))
        self.assertEqual(cm.exception.code, "already-rolled-back")

    def test_rollback_refuses_after_external_change(self):
        _, receipt = self.do()
        patch0 = self.game / "Data" / "patch-0.MPQ"
        patch0.write_bytes(patch0.read_bytes() + b"tamper")
        tampered = sha(patch0.read_bytes())
        with self.assertRaises(InstallerError) as cm:
            txn.rollback(self.game, receipt["id"], apply=True, saves_dir=self.saves, progress=Progress(False))
        self.assertEqual(cm.exception.code, "rollback-refused")
        self.assertEqual(sha(patch0.read_bytes()), tampered)  # nothing touched

    def test_second_install_merges_owned_slot_and_rolls_back_to_first(self):
        _, first = self.do()
        after_first = self.file_hash("Data/patch-0.MPQ")
        extra = synthetic_recipe("synthetic-two")
        extra["outputs"]["mpq_files"] = [dict(extra["outputs"]["mpq_files"][1], path="Item\\Thing\\second.bin")]
        extra["acceptance"] = []
        extra["steps"] = [s for s in extra["steps"] if s["id"] == "extra"]
        extra["sources"].pop("base")
        extra.pop("requires")
        rec2 = validate_recipe(extra, None, load_registry())
        _, second = self.do([rec2])
        with mpq.MpqArchive(self.game / "Data" / "patch-0.MPQ") as arc:
            self.assertEqual(sorted(arc.names()), ["DBFilesClient\\Thing.dbc", "Item\\Thing\\model.bin", "Item\\Thing\\second.bin"])
        with self.assertRaises(InstallerError) as cm:   # newest first
            txn.rollback(self.game, first["id"], apply=True, saves_dir=self.saves, progress=Progress(False))
        self.assertEqual(cm.exception.code, "rollback-refused")
        txn.rollback(self.game, second["id"], apply=True, saves_dir=self.saves, progress=Progress(False))
        self.assertEqual(self.file_hash("Data/patch-0.MPQ"), after_first)
        txn.rollback(self.game, first["id"], apply=True, saves_dir=self.saves, progress=Progress(False))
        self.assertIsNone(self.file_hash("Data/patch-0.MPQ"))

    # -- foreign slots ---------------------------------------------------------------
    def foreign_slot(self):
        path = pack_files({"Spells\\Fire.m2": b"fire", "Item\\Thing\\keep.bin": b"keep"}, self.game / "Data" / "patch-0.MPQ")
        return sha(path.read_bytes())

    def test_foreign_slot_refused_by_default(self):
        self.foreign_slot()
        plan = engine.build_plan(game_root=self.game, client_root=None, recipes=[self.recipe()], user_sources={"extra": self.sources})
        self.assertEqual(plan["blockers"][0]["code"], "foreign-slot")
        with self.assertRaises(InstallerError):
            self.build()

    def test_foreign_slot_merge_and_exact_restore(self):
        original = self.foreign_slot()
        work, receipt = self.do(foreign="merge")
        with mpq.MpqArchive(self.game / "Data" / "patch-0.MPQ") as arc:
            self.assertEqual(sorted(arc.names()), ["DBFilesClient\\Thing.dbc", "Item\\Thing\\keep.bin", "Item\\Thing\\model.bin", "Spells\\Fire.m2"])
            self.assertEqual(arc.read("Spells\\Fire.m2"), b"fire")
        txn.rollback(self.game, receipt["id"], apply=True, saves_dir=self.saves, progress=Progress(False))
        self.assertEqual(self.file_hash("Data/patch-0.MPQ"), original)
        self.assertFalse(txn.status(self.game)["installed_targets"])

    def test_foreign_slot_replace_backs_up(self):
        original = self.foreign_slot()
        work, receipt = self.do(foreign="replace")
        with mpq.MpqArchive(self.game / "Data" / "patch-0.MPQ") as arc:
            self.assertFalse(arc.has("Spells\\Fire.m2"))
        backup = txn.state_dir(self.game) / "transactions" / receipt["id"] / "backup" / "Data" / "patch-0.MPQ"
        self.assertEqual(sha(backup.read_bytes()), original)
        txn.rollback(self.game, receipt["id"], apply=True, saves_dir=self.saves, progress=Progress(False))
        self.assertEqual(self.file_hash("Data/patch-0.MPQ"), original)

    # -- safety ---------------------------------------------------------------------------
    def test_target_changed_since_build_is_refused(self):
        work = self.work_dir()
        b = engine.build(game_root=self.game, client_root=None, recipes=[self.recipe()], work=work, user_sources={"extra": self.sources})
        self.foreign_slot()   # someone created patch-0.MPQ after the build
        with self.assertRaises(InstallerError) as cm:
            txn.apply_build(b, work, self.game, saves_dir=self.saves, progress=Progress(False))
        self.assertEqual(cm.exception.code, "target-changed")

    def test_tampered_artifact_is_refused(self):
        work = self.work_dir()
        b = engine.build(game_root=self.game, client_root=None, recipes=[self.recipe()], work=work, user_sources={"extra": self.sources})
        art = work / "artifacts" / "patch-0.MPQ"
        art.write_bytes(art.read_bytes() + b"x")
        with self.assertRaises(InstallerError) as cm:
            txn.apply_build(b, work, self.game, saves_dir=self.saves, progress=Progress(False))
        self.assertEqual(cm.exception.code, "build-tampered")
        self.assertFalse((self.game / "Data" / "patch-0.MPQ").exists())

    def test_running_game_blocks_apply_and_rollback(self):
        work = self.work_dir()
        b = engine.build(game_root=self.game, client_root=None, recipes=[self.recipe()], work=work, user_sources={"extra": self.sources})
        with mock.patch.object(detect, "game_running", return_value=["C:/game.exe"]):
            with self.assertRaises(InstallerError) as cm:
                txn.apply_build(b, work, self.game, saves_dir=self.saves, progress=Progress(False))
        self.assertEqual(cm.exception.code, "game-running")
        self.assertFalse((self.game / "Data" / "patch-0.MPQ").exists())
        txn.apply_build(b, work, self.game, saves_dir=self.saves, progress=Progress(False))
        with mock.patch.object(detect, "game_running", return_value=["C:/game.exe"]):
            with self.assertRaises(InstallerError) as cm:
                txn.rollback(self.game, "latest", apply=True, saves_dir=self.saves, progress=Progress(False))
        self.assertEqual(cm.exception.code, "game-running")
        self.assertTrue((self.game / "Data" / "patch-0.MPQ").exists())

    def test_locked_file_blocks_apply(self):
        self.foreign_slot()
        work = self.work_dir()
        b = engine.build(game_root=self.game, client_root=None, recipes=[self.recipe()], work=work, foreign="replace",
                         user_sources={"extra": self.sources})
        with mock.patch.object(detect, "file_lock_state", return_value="in use by another process"):
            with self.assertRaises(InstallerError) as cm:
                txn.apply_build(b, work, self.game, saves_dir=self.saves, progress=Progress(False))
        self.assertEqual(cm.exception.code, "file-locked")

    def test_failure_midway_restores_every_file(self):
        rec_exe, exe_sha = exe_recipe()
        work = self.work_dir()
        b = engine.build(game_root=self.game, client_root=None, recipes=[self.recipe(), rec_exe], work=work,
                         user_sources={"extra": self.sources})
        self.assertEqual({t["rel"] for t in b["targets"]}, {"Data/patch-0.MPQ", "AzerothSurvivors.exe"})
        real = txn._replace_atomic
        calls = []

        def flaky(src, dest):
            calls.append(dest.name)
            if len(calls) == 2:
                raise InstallerError("simulated disk failure", code="sim")
            return real(src, dest)

        exe_before = self.file_hash("AzerothSurvivors.exe")
        with mock.patch.object(txn, "_replace_atomic", flaky):
            with self.assertRaises(InstallerError):
                txn.apply_build(b, work, self.game, saves_dir=self.saves, progress=Progress(False))
        self.assertEqual(self.file_hash("AzerothSurvivors.exe"), exe_before)
        self.assertIsNone(self.file_hash("Data/patch-0.MPQ"))
        rec = json.loads(next((txn.state_dir(self.game) / "transactions").glob("*/receipt.json")).read_text())
        self.assertIn(rec["status"], ("failed-and-rolled-back",))
        self.assertFalse(txn.status(self.game)["installed_targets"])

    def test_save_change_during_install_aborts_and_restores(self):
        work = self.work_dir()
        b = engine.build(game_root=self.game, client_root=None, recipes=[self.recipe()], work=work, user_sources={"extra": self.sources})
        real = txn._replace_atomic

        def touch_saves(src, dest):
            (self.saves / "characters.txt").write_text("changed\n")
            return real(src, dest)

        with mock.patch.object(txn, "_replace_atomic", touch_saves):
            with self.assertRaises(InstallerError) as cm:
                txn.apply_build(b, work, self.game, saves_dir=self.saves, progress=Progress(False))
        self.assertIn("preserve-changed", cm.exception.code)
        self.assertIsNone(self.file_hash("Data/patch-0.MPQ"))

    def test_exe_patch_apply_and_rollback(self):
        rec, exe_sha = exe_recipe()
        _, receipt = self.do([rec])
        self.assertEqual(self.file_hash("AzerothSurvivors.exe"), exe_sha)
        txn.rollback(self.game, receipt["id"], apply=True, saves_dir=self.saves, progress=Progress(False))
        self.assertEqual(self.file_hash("AzerothSurvivors.exe"), sha(EXE))

    def test_exe_patch_on_wrong_exe_fails_closed(self):
        rec, _ = exe_recipe()
        (self.game / "AzerothSurvivors.exe").write_bytes(EXE + b"zz")
        plan = engine.build_plan(game_root=self.game, client_root=None, recipes=[rec])
        self.assertFalse(plan["ok"])

    def test_forbidden_target_in_build_is_refused(self):
        work = self.work_dir()
        b = engine.build(game_root=self.game, client_root=None, recipes=[self.recipe()], work=work, user_sources={"extra": self.sources})
        b["targets"][0]["rel"] = "Data/patch.MPQ"
        with self.assertRaises(InstallerError) as cm:
            txn.apply_build(b, work, self.game, saves_dir=self.saves, progress=Progress(False))
        self.assertEqual(cm.exception.code, "target-forbidden")

    def test_stale_lock_file_is_recovered(self):
        d = txn.state_dir(self.game)
        d.mkdir(parents=True)
        (d / "lock").write_text("999999 2026-01-01T00:00:00Z\n")
        _, receipt = self.do()
        self.assertEqual(receipt["status"], "installed-and-verified")


if __name__ == "__main__":
    unittest.main()
