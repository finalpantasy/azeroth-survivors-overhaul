"""Packet 2: binary-edit, EXE composition/append, slot-sha256, profiles, snapshot copy, recipe status, integrate, fetch helper."""
import base64
import contextlib
import copy
import io
import json
import os
import stat
import sys
import unittest

from helpers import EXE, HERE, NEW_FILE, GameCase, ROOT, sha, synthetic_recipe
from asinstall import cli, engine, profile as profmod, status as statmod, txn
from asinstall.recipe import validate_recipe
from asinstall.steps import load_registry
from asinstall.util import InstallerError, Progress

sys.path.insert(0, str(ROOT / "tools"))
import fetch_community_sources as fetch  # noqa: E402


def b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def exe_patch(edits, append=b"", base=EXE):
    new = bytearray(base)
    out = []
    for off, data in edits:
        out.append({"offset": off, "length": len(data), "expect_sha256": sha(base[off:off + len(data)]), "new_b64": b64(data)})
        new[off:off + len(data)] = data
    patch = {"target": "AzerothSurvivors.exe", "expect_exe_sha256": sha(base), "result_exe_sha256": sha(bytes(new) + append), "edits": out}
    if append:
        patch["append_b64"] = b64(append)
    return patch


def exe_recipe(rid, patch):
    return validate_recipe({
        "schema": "as-recipe/1", "id": rid, "version": "1.0.0", "title": rid, "description": "d", "status": "experimental",
        "requires": {"game": {"exe_sha256": [sha(EXE)]}}, "outputs": {"exe_patches": [patch]},
        "acceptance": [{"check": "exe-sha256", "equals": patch["result_exe_sha256"]}],
    })


class Base(GameCase):
    def rec(self, mutate=None, rid="synthetic-overlay"):
        raw = synthetic_recipe(rid)
        if mutate:
            mutate(raw)
        return validate_recipe(raw, None, load_registry())

    def build_apply(self, recipes, foreign="refuse", **kw):
        work = self.work_dir()
        b = engine.build(game_root=self.game, client_root=None, recipes=recipes, work=work, foreign=foreign,
                         user_sources={"extra": self.sources}, **kw)
        return b, work, txn.apply_build(b, work, self.game, saves_dir=self.saves, progress=Progress(False), snapshot_copy=True)


class BinaryEdit(Base):
    def recipe_with_binary_edit(self):
        edited = bytearray(NEW_FILE)
        edited[0:4] = b"EDIT"
        edited += b"TAIL"

        def mutate(raw):
            raw["steps"].append({"id": "patched", "op": "binary-edit", "input": "extra:model.bin", "output": "model2.bin",
                                 "expect_input_sha256": sha(NEW_FILE),
                                 "edits": [{"offset": 0, "length": 4, "expect_sha256": sha(NEW_FILE[:4]), "new_b64": b64(b"EDIT")}],
                                 "append_b64": b64(b"TAIL"), "expect_output_sha256": sha(bytes(edited))})
            raw["outputs"]["mpq_files"].append({"slot": "patch-0.MPQ", "path": "Item\\Thing\\model2.bin", "from": "patched:model2.bin",
                                                "sha256": sha(bytes(edited))})
        return self.rec(mutate), bytes(edited)

    def test_binary_edit_builds_and_installs(self):
        rec, edited = self.recipe_with_binary_edit()
        _, _, receipt = self.build_apply([rec])
        from asinstall import mpq
        with mpq.MpqArchive(self.game / "Data" / "patch-0.MPQ") as arc:
            self.assertEqual(arc.read("Item\\Thing\\model2.bin"), edited)
        self.assertEqual(receipt["status"], "installed-and-verified")

    def test_binary_edit_rejects_wrong_input(self):
        rec, _ = self.recipe_with_binary_edit()
        (self.sources / "model.bin").write_bytes(NEW_FILE + b"x")
        with self.assertRaises(InstallerError):
            engine.build(game_root=self.game, client_root=None, recipes=[rec], work=self.work_dir(), user_sources={"extra": self.sources})

    def test_binary_edit_needs_edit_or_append(self):
        def mutate(raw):
            raw["steps"].append({"id": "p", "op": "binary-edit", "input": "extra:model.bin", "output": "m.bin",
                                 "expect_input_sha256": sha(NEW_FILE), "expect_output_sha256": sha(NEW_FILE)})
        with self.assertRaises(InstallerError):
            self.rec(mutate)


class ExeComposition(Base):
    def test_disjoint_patches_compose(self):
        a = exe_recipe("exe-a", exe_patch([(10, b"AAAA")]))
        b = exe_recipe("exe-b", exe_patch([(100, b"BBBB")], append=b"SECTION"))
        built, work, _ = self.build_apply([a, b])
        want = bytearray(EXE)
        want[10:14] = b"AAAA"
        want[100:104] = b"BBBB"
        self.assertEqual(self.file_hash("AzerothSurvivors.exe"), sha(bytes(want) + b"SECTION"))
        txn.rollback(self.game, "latest", apply=True, saves_dir=self.saves, progress=Progress(False))
        self.assertEqual(self.file_hash("AzerothSurvivors.exe"), sha(EXE))

    def test_overlapping_patches_are_blocked(self):
        a = exe_recipe("exe-a", exe_patch([(10, b"AAAA")]))
        b = exe_recipe("exe-b", exe_patch([(12, b"BBBB")]))
        plan = engine.build_plan(game_root=self.game, client_root=None, recipes=[a, b])
        self.assertFalse(plan["ok"])
        self.assertTrue(any("both edit" in x or "exe-conflict" in str(x) for x in map(str, plan["blockers"])), plan["blockers"])

    def test_two_appends_are_blocked(self):
        a = exe_recipe("exe-a", exe_patch([(10, b"AAAA")], append=b"ONE"))
        b = exe_recipe("exe-b", exe_patch([(100, b"BBBB")], append=b"TWO"))
        problems = engine.exe_composition_problems([a.exe_patches[0], b.exe_patches[0]])
        self.assertTrue(any("appends" in p for p in problems), problems)

    def test_different_bases_are_blocked(self):
        other = EXE + b"zz"
        problems = engine.exe_composition_problems([exe_patch([(10, b"AAAA")]), exe_patch([(10, b"BBBB")], base=other)])
        self.assertTrue(any("different base" in p for p in problems), problems)

    def test_single_append_patch_round_trips(self):
        rec = exe_recipe("exe-app", exe_patch([(10, b"AAAA")], append=b"NEWSECTION"))
        self.build_apply([rec])
        self.assertTrue((self.game / "AzerothSurvivors.exe").read_bytes().endswith(b"NEWSECTION"))
        row = statmod.recipe_status(self.game, [rec])[0]
        self.assertEqual((row["state"], row["matched"], row["of"]), ("installed", 2, 2))


class SlotHashAcceptance(Base):
    def with_slot_check(self, digest):
        def mutate(raw):
            raw["acceptance"].append({"check": "slot-sha256", "slot": "patch-0.MPQ", "equals": digest})
        return self.rec(mutate)

    def test_matching_slot_hash_passes_and_wrong_hash_fails(self):
        first = engine.build(game_root=self.game, client_root=None, recipes=[self.rec()], work=self.work_dir(),
                             user_sources={"extra": self.sources})
        digest = next(t["sha256"] for t in first["targets"] if t["rel"] == "Data/patch-0.MPQ")
        ok = engine.build(game_root=self.game, client_root=None, recipes=[self.with_slot_check(digest)], work=self.work_dir(),
                          user_sources={"extra": self.sources})
        self.assertTrue(any(a.get("check") == "slot-sha256" and a["ok"] for a in ok["acceptance"]))
        with self.assertRaises(InstallerError):
            engine.build(game_root=self.game, client_root=None, recipes=[self.with_slot_check("0" * 64)], work=self.work_dir(),
                         user_sources={"extra": self.sources})

    def test_slot_hash_is_skipped_when_slot_is_shared(self):
        other = self.rec(lambda raw: raw["outputs"]["mpq_files"].__setitem__(0, raw["outputs"]["mpq_files"][0]), rid="other-recipe")
        raw = synthetic_recipe("second")
        raw["outputs"]["mpq_files"] = [{"slot": "patch-0.MPQ", "path": "Item\\Thing\\second.bin", "from": "extra:model.bin", "sha256": sha(NEW_FILE)}]
        raw["acceptance"] = [{"check": "slot-sha256", "slot": "patch-0.MPQ", "equals": "0" * 64}]
        second = validate_recipe(raw, None, load_registry())
        built = engine.build(game_root=self.game, client_root=None, recipes=[self.rec(), second], work=self.work_dir(),
                             user_sources={"extra": self.sources})
        skipped = [a for a in built["acceptance"] if a.get("skipped")]
        self.assertTrue(skipped)


class Profiles(Base):
    def raw(self, **kw):
        base = {"schema": "as-profile/1", "id": "test-profile", "version": "1.0.0", "title": "T", "description": "d",
                "recipes": ["synthetic-overlay"]}
        base.update(kw)
        return base

    def test_validation(self):
        profmod.validate_profile(self.raw())
        for bad in (self.raw(recipes=[]), self.raw(recipes=["a", "a"]), self.raw(expect_slot_sha256={"patch-9.MPQ": "0" * 64}),
                    self.raw(expect_slot_sha256={"patch-0.MPQ": "xyz"}), self.raw(extra=1), self.raw(id="Bad Id")):
            with self.assertRaises(InstallerError):
                profmod.validate_profile(bad)

    def test_shipped_profiles_resolve(self):
        catalog = engine.load_catalog()
        profiles = profmod.load_profiles()
        self.assertIn("current-upgrade", profiles)
        recipes = profmod.resolve_profile(profiles["current-upgrade"], catalog, False)
        self.assertEqual([r.id for r in recipes], profiles["current-upgrade"].recipe_ids)
        both = profmod.resolve_profile(profiles["current-upgrade-renderer"], catalog, False)
        self.assertEqual(len(both), 5)
        self.assertTrue(all(r.status in ("ready", "experimental") for r in both))

    def test_build_enforces_expected_slot_hash(self):
        with self.assertRaises(InstallerError) as cm:
            engine.build(game_root=self.game, client_root=None, recipes=[self.rec()], work=self.work_dir(),
                         user_sources={"extra": self.sources}, expect_slot_sha256={"patch-0.MPQ": "0" * 64})
        self.assertEqual(cm.exception.code, "profile-slot-hash")


class SnapshotAndStatus(Base):
    def test_copy_snapshot_is_read_only_and_exact(self):
        dest = self.work / "snap"
        info = txn.copy_snapshot(self.game, self.saves, dest)
        self.assertEqual(info["files"], 2)
        copy_ = dest / "saves" / "characters.txt"
        self.assertEqual(copy_.read_bytes(), (self.saves / "characters.txt").read_bytes())
        self.assertFalse(os.stat(copy_).st_mode & stat.S_IWRITE)
        self.assertTrue((dest / "benilla-config" / "config.toml").is_file())
        self.assertEqual((self.saves / "characters.txt").read_text(), "hero\n")

    def test_apply_records_snapshot_and_leaves_saves_alone(self):
        before = sha((self.saves / "characters.txt").read_bytes())
        _, _, receipt = self.build_apply([self.rec()])
        self.assertEqual(receipt["snapshot_copy"]["files"], 2)
        self.assertEqual(sha((self.saves / "characters.txt").read_bytes()), before)
        self.assertTrue((txn.state_dir(self.game) / "transactions" / receipt["id"] / "saves-snapshot" / "saves" / "characters.txt").is_file())

    def test_recipe_status_absent_installed_partial(self):
        rec = self.rec()
        self.assertEqual(statmod.recipe_status(self.game, [rec])[0]["state"], "absent")
        self.build_apply([rec])
        self.assertEqual(statmod.recipe_status(self.game, [rec])[0]["state"], "installed")
        raw = synthetic_recipe()
        raw["outputs"]["mpq_files"].append({"slot": "patch-0.MPQ", "path": "Item\\Thing\\gone.bin", "from": "extra:model.bin", "sha256": sha(NEW_FILE)})
        bigger = validate_recipe(raw, None, load_registry())
        row = statmod.recipe_status(self.game, [bigger])[0]
        self.assertEqual((row["state"], row["matched"], row["of"]), ("partial", 2, 3))


class ConflictPriority(Base):
    def other(self, rid, data):
        raw = synthetic_recipe(rid)
        raw["sources"]["extra"]["files"] = [{"name": "model.bin", "bytes": len(data), "sha256": sha(data)}]
        for o in raw["outputs"]["mpq_files"]:
            if o["path"].endswith("model.bin"):
                o["sha256"] = sha(data)
        raw["steps"] = [s for s in raw["steps"] if s["id"] != "dbc"]
        raw["outputs"]["mpq_files"] = [o for o in raw["outputs"]["mpq_files"] if o["path"].endswith("model.bin")]
        raw["acceptance"] = []
        return validate_recipe(raw, None, load_registry())

    def test_identical_bytes_are_not_a_conflict(self):
        a, b = self.other("rec-a", NEW_FILE), self.other("rec-b", NEW_FILE)
        slots = engine._slot_files([a, b])
        self.assertEqual(len(slots["patch-0.MPQ"]), 1)

    def test_different_bytes_error_names_both_recipes(self):
        a, b = self.other("rec-a", NEW_FILE), self.other("rec-b", NEW_FILE + b"x")
        with self.assertRaises(InstallerError) as cm:
            engine._slot_files([a, b])
        self.assertEqual(cm.exception.code, "recipe-conflict")
        self.assertIn("rec-a", cm.exception.message)
        self.assertIn("rec-b", cm.exception.message)
        self.assertIn("priority", cm.exception.hint)

    def test_priority_picks_the_winner_either_order(self):
        a, b = self.other("rec-a", NEW_FILE), self.other("rec-b", NEW_FILE + b"x")
        for recipes in ([a, b], [b, a]):
            resolved = []
            slots = engine._slot_files(recipes, ["rec-b", "rec-a"], resolved)
            rec, f = next(iter(slots["patch-0.MPQ"].values()))
            self.assertEqual(rec.id, "rec-b")
            self.assertEqual(resolved[0]["winner"], "rec-b")
            self.assertEqual(resolved[0]["loser"], "rec-a")

    def test_priority_does_not_cover_an_unlisted_recipe(self):
        a, b = self.other("rec-a", NEW_FILE), self.other("rec-b", NEW_FILE + b"x")
        with self.assertRaises(InstallerError):
            engine._slot_files([a, b], ["rec-a"])

    def test_profile_priority_validation(self):
        base = {"schema": "as-profile/1", "id": "prio-profile", "version": "1.0.0", "title": "T", "description": "d", "recipes": ["rec-a", "rec-b"]}
        self.assertEqual(profmod.validate_profile(dict(base, priority=["rec-b", "rec-a"])).priority, ["rec-b", "rec-a"])
        for bad in (["rec-z"], ["rec-a", "rec-a"], "rec-a"):
            with self.assertRaises(InstallerError):
                profmod.validate_profile(dict(base, priority=bad))

    def test_exe_conflict_message_names_recipes(self):
        a = exe_patch([(10, b"AAAA")])
        b = exe_patch([(12, b"BBBB")])
        problems = engine.exe_composition_problems([a, b], ["exe-a", "exe-b"])
        self.assertIn("exe-a", problems[0])
        self.assertIn("exe-b", problems[0])


class IntegrateCli(Base):
    def setUp(self):
        super().setUp()
        self.rdir = self.work.parent / "recipes"
        self.pdir = self.work.parent / "profiles"
        self.rdir.mkdir()
        self.pdir.mkdir()
        (self.rdir / "synthetic-overlay.json").write_text(json.dumps(synthetic_recipe()))
        (self.pdir / "p.json").write_text(json.dumps({"schema": "as-profile/1", "id": "test-profile", "version": "1.0.0", "title": "T",
                                                      "description": "d", "recipes": ["synthetic-overlay"]}))

    def tree_state(self):
        out = {}
        for p in sorted(self.game.rglob("*")):
            if p.is_file():
                out[str(p.relative_to(self.game))] = sha(p.read_bytes())
        return out

    def run_cli(self, *argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.main(list(argv))
        return code, buf.getvalue()

    def common(self):
        return ["--game-root", str(self.game), "--profile", "test-profile", "--recipes-dir", str(self.rdir),
                "--profiles-dir", str(self.pdir), "--source", f"extra={self.sources}", "--saves-dir", str(self.saves), "--quiet"]

    def test_integrate_plan_writes_nothing(self):
        before = self.tree_state()
        code, out = self.run_cli("integrate", *self.common(), "--json")
        self.assertEqual(code, 0, out)
        plan = json.loads(out)
        self.assertEqual(plan["integrate"]["mode"], "plan-only")
        self.assertTrue(plan["integrate"]["saves_dir_found"])
        self.assertEqual(plan["integrate"]["verdict"], "not-installed")
        self.assertEqual(self.tree_state(), before)
        self.assertFalse(txn.state_dir(self.game).exists())

    def test_integrate_apply_needs_yes_then_installs_and_reports_status(self):
        with contextlib.redirect_stderr(io.StringIO()) as err:
            code, _ = self.run_cli("integrate", *self.common(), "--apply", "--work", str(self.work_dir()))
        self.assertNotEqual(code, 0)
        self.assertIn("--yes", err.getvalue())
        self.assertFalse((self.game / "Data" / "patch-0.MPQ").exists())
        code, _ = self.run_cli("integrate", *self.common(), "--apply", "--yes", "--work", str(self.work_dir()))
        self.assertEqual(code, 0)
        code, out = self.run_cli("status", "--game-root", str(self.game), "--recipes-dir", str(self.rdir), "--json")
        rows = {r["id"]: r for r in json.loads(out)["recipes"]}
        self.assertEqual(rows["synthetic-overlay"]["state"], "installed")
        code, out = self.run_cli("integrate", *self.common(), "--json")
        self.assertTrue(json.loads(out)["integrate"]["verdict"].startswith("already-installed"))

    def test_profile_and_recipe_flags_conflict(self):
        with contextlib.redirect_stderr(io.StringIO()) as err:
            code, _ = self.run_cli("plan", "--game-root", str(self.game), "--profile", "test-profile", "--profiles-dir", str(self.pdir),
                                   "--recipes-dir", str(self.rdir), "--recipe", "synthetic-overlay", "--quiet")
        self.assertNotEqual(code, 0)
        self.assertIn("either --profile or --recipe", err.getvalue())


class FetchHelper(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        from pathlib import Path
        self.base = Path(self._t.name)
        self.src = self.base / "origin.bin"
        self.src.write_bytes(b"pinned-bytes" * 10)
        self.entry = {"source": "s", "name": "Patch-X/a/file.bin", "bytes": 120, "sha256": sha(self.src.read_bytes()),
                      "origin": self.src.as_uri()}

    def test_download_verifies_hash_and_is_idempotent(self):
        dest = self.base / "dest"
        self.assertEqual(fetch.fetch_one(self.entry, dest, True), "downloaded")
        self.assertEqual((dest / "Patch-X" / "a" / "file.bin").read_bytes(), self.src.read_bytes())
        self.assertEqual(fetch.fetch_one(self.entry, dest, True), "present")

    def test_bad_hash_is_rejected_and_not_kept(self):
        dest = self.base / "dest"
        bad = dict(self.entry, sha256="0" * 64)
        with self.assertRaises(SystemExit):
            fetch.fetch_one(bad, dest, True)
        self.assertFalse((dest / "Patch-X" / "a" / "file.bin").exists())
        self.assertEqual(list(dest.rglob("*.part")), [])

    def test_origin_and_path_guards(self):
        with self.assertRaises(SystemExit):
            fetch.check_origin("https://evil.example/x", False)
        with self.assertRaises(SystemExit):
            fetch.check_origin(self.src.as_uri(), False)
        with self.assertRaises(SystemExit):
            fetch.safe_target(self.base / "dest", "../escape.bin")

    def test_shipped_recipes_pin_only_allowed_origins(self):
        for name in ("hd-terrain-community", "hd-spells-community"):
            files = fetch.load_files(ROOT / "recipes" / f"{name}.json")
            self.assertTrue(files)
            for f in files:
                fetch.check_origin(f["origin"], False)


if __name__ == "__main__":
    unittest.main()
