import unittest
from pathlib import Path
from types import SimpleNamespace

import build_retail_rigid as builder


class FakeStream:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.requested_bytes = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, size: int) -> bytes:
        self.requested_bytes = size
        return self.payload[:size]


class FakePath:
    def __init__(self, stat_size: int, payload: bytes):
        self.stat_size = stat_size
        self.stream = FakeStream(payload)
        self.open_called = False

    def stat(self):
        return SimpleNamespace(st_size=self.stat_size)

    def open(self, mode: str):
        if mode != "rb":
            raise AssertionError(f"Unexpected file mode: {mode}")
        self.open_called = True
        return self.stream


class InputBoundsTests(unittest.TestCase):
    def test_flat_bundle_root_does_not_index_past_parents(self):
        script_path = Path(r"C:\AssetBuilder\build_retail_rigid.py")
        self.assertEqual(builder.discover_project_root(script_path), script_path.resolve().parent)

    def test_oversized_report_is_rejected_before_open(self):
        report = FakePath(builder.MAX_SOURCE_REPORT_BYTES + 1, b"not read")
        with self.assertRaisesRegex(ValueError, "limit"):
            builder.read_bounded_file(report, builder.MAX_SOURCE_REPORT_BYTES, "portable report")
        self.assertFalse(report.open_called)

    def test_oversized_payload_stat_is_rejected_before_open(self):
        expected = builder.EXPECTED_FILE_BYTES["model-1693135.m2"]
        payload = FakePath(expected + 1, b"not read")
        with self.assertRaisesRegex(ValueError, "expected exactly"):
            builder.read_bounded_file(payload, expected, "model", expected_size=expected)
        self.assertFalse(payload.open_called)

    def test_payload_read_stops_at_expected_size_plus_one(self):
        expected = builder.EXPECTED_FILE_BYTES["model-1693135.m2"]
        payload = FakePath(expected, b"x" * (expected + 1))
        with self.assertRaisesRegex(ValueError, "read limit"):
            builder.read_bounded_file(payload, expected, "model", expected_size=expected)
        self.assertEqual(payload.stream.requested_bytes, expected + 1)


if __name__ == "__main__":
    unittest.main()
