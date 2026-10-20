import csv
import struct
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import rigid_item_convert as R  # noqa: E402

ROOT = HERE.parent.parent
CACHE = ROOT / "analysis" / "asset-coverage" / "work" / "cache-items"
MACE_SHA = "b69b55d092912005907d9190a3616160d0b2e4cf845d8eb8860354eefab8004d"


def load(fdid):
    model = (CACHE / f"{fdid}.bin").read_bytes()
    skin_id = struct.unpack_from("<I", R.split_chunks(model)["SFID"], 0)[0]
    return model, (CACHE / f"{skin_id}.bin").read_bytes()


@unittest.skipUnless((CACHE / "1693135.bin").exists(), "local item cache not present")
class RigidItemConvert(unittest.TestCase):
    def test_mace_regression_is_byte_exact(self):
        model, skin = load(1693135)
        info = R.inspect(model, skin)
        slots = R.derive_textures(info, {0: 1733940})
        self.assertEqual(slots, {0: 1733940})
        out, stats = R.build(info, "x", {0: r"Item\ObjectComponents\Weapon\KultirasQuestB01_Silver_Static.blp"}, "KultirasQuestB01_Silver_Static")
        self.assertEqual(R.sha256(out), MACE_SHA)
        R.validate_output(out, info)
        self.assertEqual((stats["vertices"], stats["triangles"]), (1712, 1228))

    def test_animated_model_is_rejected(self):
        pool = ROOT / "analysis" / "asset-coverage" / "rigid-pool.csv"
        bad = next((r["fdid"] for r in csv.DictReader(pool.open(encoding="utf8")) if "is animated" in r["gate"]), None)
        if bad is None or not (CACHE / f"{bad}.bin").exists():
            self.skipTest("no animated sample cached")
        with self.assertRaises(ValueError):
            R.inspect(*load(int(bad)))


if __name__ == "__main__":
    unittest.main()
