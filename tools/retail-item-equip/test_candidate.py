import sys
import struct
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_candidate as candidate


class NativeBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.outfit_data = candidate.OUTFIT_PATH.read_bytes()
        cls.display_data = candidate.DISPLAY_PATH.read_bytes()

    def test_all_native_shaman_starters_bind_item_36_to_display_5194(self):
        evidence = candidate.validate_native_binding(self.outfit_data, self.display_data)
        bindings = evidence["binding"]["rows"]
        self.assertEqual(len(bindings), 6)
        self.assertEqual({row["race_id"] for row in bindings}, {2, 6, 8})
        self.assertEqual({row["sex_id"] for row in bindings}, {0, 1})
        self.assertEqual(
            {(row["item_id"], row["display_id"], row["inventory_type"]) for row in bindings},
            {(36, 5194, 21)},
        )
        self.assertEqual(evidence["native_display"]["model_name_left"], "Mace_1H_Flanged_A_01.mdx")

    def test_display_5194_sharing_scope_is_the_native_starter_reference_set(self):
        references = candidate.char_start_display_references(self.outfit_data, 5194)
        actual = {
            (row["row_id"], row["race_id"], row["class_id"], row["sex_id"], row["slot_index"])
            for row in references
        }
        expected = {
            (11, 1, 5, 0, 4), (38, 1, 5, 1, 4),
            (19, 2, 7, 0, 4), (45, 2, 7, 1, 4),
            (22, 3, 5, 0, 4), (50, 3, 5, 1, 4),
            (26, 4, 5, 0, 0), (56, 4, 5, 1, 0),
            (32, 5, 5, 0, 3), (60, 5, 5, 1, 3),
            (29, 6, 7, 0, 3), (66, 6, 7, 1, 3),
            (298, 8, 5, 0, 0), (311, 8, 5, 1, 0),
            (300, 8, 7, 0, 0), (312, 8, 7, 1, 0),
        }
        self.assertEqual(actual, expected)
        self.assertTrue(all(row["item_id"] == 36 for row in references))
        self.assertTrue(all(row["inventory_type"] == 21 for row in references))

    def test_display_row_patch_changes_only_model_left_pointer(self):
        patched = candidate.patch_item_display_info(
            self.display_data, 5194, candidate.NEW_MODEL_DBC_PATH
        )
        records, _, old_string_size = candidate.parse_item_display_rows(self.display_data)
        record_index = next(i for i, row in enumerate(records) if row[0] == 5194)
        expected = bytearray(self.display_data)
        new_string = candidate.NEW_MODEL_DBC_PATH.encode("ascii") + b"\0"
        struct_offset = 20 + record_index * candidate.ITEM_DISPLAY_RECORD_SIZE + 4
        candidate.struct.pack_into("<I", expected, struct_offset, old_string_size)
        candidate.struct.pack_into("<I", expected, 16, old_string_size + len(new_string))
        expected.extend(new_string)
        self.assertEqual(patched, bytes(expected))
        self.assertEqual(
            candidate.item_display_model_name(patched, 5194), candidate.NEW_MODEL_DBC_PATH
        )

    def test_display_model_basename_resolves_to_candidate_mpq_path(self):
        model_name = candidate.NEW_MODEL_DBC_PATH
        self.assertNotRegex(model_name, r"[\\/]")
        resolved = candidate.resolve_runtime_weapon_model_path(model_name)
        self.assertEqual(
            resolved,
            r"Item\ObjectComponents\Weapon\kultirasquestb01_silver_static.m2",
        )
        self.assertEqual(resolved.casefold(), candidate.MODEL_INTERNAL_PATH.casefold())
        with self.assertRaisesRegex(ValueError, "basename"):
            candidate.resolve_runtime_weapon_model_path(candidate.MODEL_INTERNAL_PATH)

    def test_invalid_char_start_outfit_layout_is_rejected(self):
        malformed = bytearray(self.outfit_data)
        candidate.struct.pack_into("<I", malformed, 8, 40)
        with self.assertRaisesRegex(ValueError, "schema mismatch"):
            candidate.parse_char_start_outfit(bytes(malformed))


if __name__ == "__main__":
    unittest.main()
