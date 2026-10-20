#!/usr/bin/env python3
"""Read BLPs from the pinned offline CASC by FileDataID and keep only a small feature per file (no bytes written).

For each id: w, h, bytes, encoding, and a 16x16 RGBA box-downsample (premultiplied-free, raw RGBA) as hex.  Used to find
a higher-resolution Retail/Forever look-alike of every legacy spell texture.  Read-only; BelowNormal; local analysis only.
Usage: casc_features.py --profile retail --ids ids.txt --out features-retail.json --allow-client-read
"""
import argparse
import ctypes
import importlib.util
import io
import json
import struct
import sys
from pathlib import Path

HERE = Path(r"tools/asset-coverage")
sys.path.insert(0, str(HERE))
from casc_probe import load_adapter, read_tolerant, read_ids  # noqa: E402
from PIL import Image  # noqa: E402

Image.MAX_IMAGE_PIXELS = None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", choices=("retail", "forever"), required=True)
    ap.add_argument("--ids", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--cap", type=int, default=8 * 1024 * 1024)
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
    out = {}
    before = c.snapshot_input_metadata(inventory)
    storage = api.open_storage(callback)
    c.verify_storage_identity(api, storage)
    for n, fid in enumerate(ids):
        rec = {}
        try:
            h = api.open_file(storage, fid)
            try:
                size = api.file_size(h)
                rec["bytes"] = size
                if size <= a.cap:
                    data = bytes(read_tolerant(api, h, size))
                    if data[:4] == b"BLP2":
                        enc, alpha, atype, mips = data[8], data[9], data[10], data[11]
                        w, hh = struct.unpack_from("<II", data, 12)
                        rec.update(enc=enc, alpha=alpha, atype=atype, mips=mips, w=w, h=hh)
                        try:
                            im = Image.open(io.BytesIO(data)).convert("RGBA")
                            rec["f"] = im.resize((16, 16), Image.BOX).tobytes().hex()
                        except Exception as e:  # noqa: BLE001
                            rec["decode_error"] = repr(e)[:80]
                    else:
                        rec["magic"] = data[:4].decode("latin1")
                else:
                    rec["skipped"] = "over cap"
            finally:
                api.close_file(h)
        except c.CascError as e:
            rec["error"] = e.error_code
        except RuntimeError as e:
            rec["error"] = repr(e)[:80]
        if events:
            raise RuntimeError("CascLib reported a download progress event; aborting")
        out[str(fid)] = rec
        if n % 1000 == 0:
            print(n, len(ids), flush=True)
    after = c.snapshot_input_metadata(inventory)
    json.dump({"profile": a.profile, "metadata_unchanged": before == after, "results": out}, open(a.out, "w"))
    print("done", len(out), "metadata_unchanged", before == after)


if __name__ == "__main__":
    main()
