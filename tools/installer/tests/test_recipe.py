import copy
import json
import unittest

from helpers import ROOT, synthetic_recipe  # noqa: F401  (sets sys.path)
from asinstall.recipe import validate_recipe, load_recipes, casc_catalog
from asinstall.steps import load_registry
from asinstall.util import InstallerError


class RecipeParsing(unittest.TestCase):
    def reg(self):
        return load_registry()

    def test_builtin_recipes_all_validate(self):
        recipes = load_recipes(ROOT / "recipes", self.reg())
        self.assertIn("retail-mace-kultiras-silver", recipes)
        self.assertEqual(recipes["retail-mace-kultiras-silver"].slots, ["patch-0.MPQ"])
        self.assertEqual(recipes["hd-terrain-community"].status, "ready")
        self.assertEqual(recipes["hd-terrain-community"].raw["sources"]["vanillahd"]["kind"], "user-supplied")
        self.assertEqual(recipes["hd-spells-community"].slots, ["patch-0.MPQ"])
        self.assertTrue(recipes["renderer-v3-post"].exe_patches[0]["edits"])
        self.assertTrue(recipes["water-shader-adt-liquid"].exe_patches)

    def test_synthetic_valid(self):
        self.assertEqual(validate_recipe(synthetic_recipe()).id, "synthetic-overlay")

    def bad(self, mutate, needle):
        raw = copy.deepcopy(synthetic_recipe())
        mutate(raw)
        with self.assertRaises(InstallerError) as cm:
            validate_recipe(raw, None, self.reg())
        self.assertIn(needle, cm.exception.message)

    def test_rejects_unknown_top_key(self):
        self.bad(lambda r: r.update(script="evil()"), "unknown keys")

    def test_rejects_bad_schema(self):
        self.bad(lambda r: r.update(schema="as-recipe/2"), "Unsupported recipe schema")

    def test_rejects_bad_hash(self):
        self.bad(lambda r: r["outputs"]["mpq_files"][0].update(sha256="xyz"), "64 hex")

    def test_rejects_forbidden_slot(self):
        self.bad(lambda r: r["outputs"]["mpq_files"][0].update(slot="patch-5.MPQ"), "slot must be one of")

    def test_rejects_slash_internal_path(self):
        self.bad(lambda r: r["outputs"]["mpq_files"][1].update(path="Item/Thing/model.bin"), "backslash-separated")

    def test_rejects_traversal_in_ref(self):
        self.bad(lambda r: r["outputs"]["mpq_files"][1].update({"from": "extra:../x"}), "unsafe segment")

    def test_rejects_ref_to_unknown_step(self):
        self.bad(lambda r: r["outputs"]["mpq_files"][1].update({"from": "nope:model.bin"}), "no earlier step")

    def test_rejects_unknown_op(self):
        self.bad(lambda r: r["steps"][0].update(op="shell"), "op must be one of")

    def test_rejects_unregistered_tool(self):
        def m(r):
            r["steps"].append({"id": "t", "op": "run-tool", "tool": "my-script", "inputs": {},
                               "expect_outputs": [{"path": "a", "sha256": "0" * 64}]})
        self.bad(m, "not in tools/registry.json")

    def test_unavailable_needs_reason(self):
        raw = {"schema": "as-recipe/1", "id": "no-reason", "version": "1.0.0", "title": "t", "description": "d",
               "status": "unavailable"}
        with self.assertRaises(InstallerError):
            validate_recipe(raw)
        raw["unavailable_reason"] = "needs community files"
        self.assertFalse(validate_recipe(raw).available)

    def _exe_recipe(self, edits):
        return {"schema": "as-recipe/1", "id": "exe-edit", "version": "1.0.0", "title": "t", "description": "d",
                "status": "experimental",
                "outputs": {"exe_patches": [{"target": "AzerothSurvivors.exe", "expect_exe_sha256": "0" * 64,
                                             "result_exe_sha256": "1" * 64, "edits": edits}]}}

    def test_exe_edit_must_keep_length(self):
        raw = self._exe_recipe([{"offset": 0, "length": 4, "expect_sha256": "2" * 64, "new_b64": "AAAAAAA="}])
        with self.assertRaises(InstallerError) as cm:
            validate_recipe(raw)
        self.assertIn("length unchanged", cm.exception.message)

    def test_exe_edits_must_not_overlap(self):
        def e(off):
            return {"offset": off, "length": 4, "expect_sha256": "2" * 64, "new_b64": "AAAAAA=="}
        with self.assertRaises(InstallerError):
            validate_recipe(self._exe_recipe([e(10), e(12)]))

    def test_duplicate_output_path_rejected(self):
        def m(r):
            r["outputs"]["mpq_files"].append(dict(r["outputs"]["mpq_files"][1], path="ITEM\\THING\\MODEL.BIN"))
        self.bad(m, "duplicates")

    def test_casc_catalog_shape(self):
        recipes = load_recipes(ROOT / "recipes", self.reg())
        cat = casc_catalog(recipes["retail-mace-kultiras-silver"].sources["retail"])
        self.assertEqual(cat["version"], "12.1.5.70448")
        self.assertEqual(len(cat["assets"]), 4)
        self.assertEqual(cat["locale_mask"], 2)
        json.dumps(cat)


if __name__ == "__main__":
    unittest.main()
