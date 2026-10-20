#!/usr/bin/env python3
"""Dump raw files by FileDataID from the offline CASC into a directory (read-only client access; local analysis only).
Usage: casc_dump.py --profile retail --ids ids.txt --out DIR --allow-client-read   (ids: one FDID per line, '#' comments)
"""
import argparse
import ctypes
import sys
from pathlib import Path

sys.path.insert(0, r"tools/asset-coverage")
from casc_probe import load_adapter, read_tolerant, read_ids  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", choices=("retail", "forever"), required=True)
    ap.add_argument("--ids", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--cap", type=int, default=32 * 1024 * 1024)
    ap.add_argument("--allow-client-read", action="store_true")
    a = ap.parse_args()
    if not a.allow_client_read:
        raise SystemExit("refusing client access without --allow-client-read")
    ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)
    c = load_adapter()
    c.activate_client_profile(a.profile)
    c.verify_runtime_shape()
    c.load_source_manifest()
    inventory = c.load_inventory()
    ids = read_ids(a.ids)
    events = []
    callback = c.create_progress_callback(events)
    api = c.CascApi(c.DLL_PATH)
    before = c.snapshot_input_metadata(inventory)
    storage = api.open_storage(callback)
    c.verify_storage_identity(api, storage)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    n_ok = 0
    for fid in ids:
        try:
            h = api.open_file(storage, fid)
            try:
                size = api.file_size(h)
                if size <= a.cap:
                    (out / f"{fid}.bin").write_bytes(bytes(read_tolerant(api, h, size)))
                    n_ok += 1
            finally:
                api.close_file(h)
        except (c.CascError, RuntimeError) as e:
            print("miss", fid, repr(e)[:60])
    if events:
        raise RuntimeError("CascLib reported a download progress event; aborting")
    print("dumped", n_ok, "of", len(ids))


if __name__ == "__main__":
    main()
