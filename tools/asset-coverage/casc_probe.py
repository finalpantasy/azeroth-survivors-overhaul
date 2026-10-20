#!/usr/bin/env python3
"""Read-only membership/size probe of the pinned offline CASC storage, by FileDataID.

Reuses the audited adapter (tools/casc-offline/casc_offline.py): pinned build identity, offline-only storage
features, strict data check, metadata unchanged before/after, any download progress event aborts.  Files are read
in memory only (never written outside --out); --parse reads each file (size-capped) and records the M2/skin header
counts via m2lib.  Run at BelowNormal; take coordination/locks/heavy.lock for large id lists.
"""
import argparse
import ctypes
import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import m2lib  # noqa: E402


def load_adapter():
    path = HERE.parent / "casc-offline" / "casc_offline.py"
    spec = importlib.util.spec_from_file_location("approved_casc_adapter", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def read_tolerant(api, handle, size):
    """Like the adapter's read_file_bytes but tolerates a short final read (some DB2s are shorter than the declared size)."""
    payload = bytearray()
    while len(payload) < size:
        request = min(1 << 20, size - len(payload))
        buf = ctypes.create_string_buffer(request)
        actual = ctypes.c_uint32()
        ok = api._functions["CascReadFile"](handle, ctypes.cast(buf, ctypes.c_void_p), request, ctypes.byref(actual))
        api.check(bool(ok), "CascReadFile")
        if actual.value == 0:
            break
        payload.extend(buf.raw[: actual.value])
        if actual.value != request:
            break
    return payload


def read_ids(path):
    ids = []
    for line in Path(path).read_text(encoding="utf8").splitlines():
        line = line.strip()
        if line:
            ids.append(int(line.split(";")[0]))
    return ids


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", choices=("retail", "forever"), required=True)
    ap.add_argument("--ids", required=True, help="file: one FileDataID per line (optionally 'id;name')")
    ap.add_argument("--out", required=True)
    ap.add_argument("--parse", action="store_true", help="read each file (<= --cap bytes) and parse M2/skin/BLP headers")
    ap.add_argument("--cap", type=int, default=64 * 1024 * 1024)
    ap.add_argument("--total-cap", type=int, default=768 * 1024 * 1024)
    ap.add_argument("--dump-dir", help="also write each read file to <dir>/<fdid>.bin (local analysis cache only; implies --parse)")
    ap.add_argument("--allow-client-read", action="store_true")
    a = ap.parse_args()
    if a.dump_dir:
        a.parse = True
        Path(a.dump_dir).mkdir(parents=True, exist_ok=True)
    if not a.allow_client_read:
        raise SystemExit("refusing client access without --allow-client-read")
    if sys.platform == "win32":
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)
    c = load_adapter()
    c.activate_client_profile(a.profile)
    layout = c.verify_runtime_shape()
    c.load_source_manifest()
    inventory = c.load_inventory()
    ids = read_ids(a.ids)
    events = []
    callback = c.create_progress_callback(events)
    rec = {"profile": a.profile, "started_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "requested": len(ids), "results": {}}
    api = c.CascApi(c.DLL_PATH)
    storage = ctypes.c_void_p()
    total = 0
    try:
        rec["metadata_before"] = c.snapshot_input_metadata(inventory)
        storage = api.open_storage(callback)
        rec["storage_identity"] = c.verify_storage_identity(api, storage)
        for n, fid in enumerate(ids):
            res = {}
            try:
                h = api.open_file(storage, fid)
                try:
                    size = api.file_size(h)
                    res["bytes"] = size
                    if a.parse and size <= a.cap and total + size <= a.total_cap:
                        data = read_tolerant(api, h, size)
                        total += len(data)
                        if len(data) != size:
                            res["short_read"] = len(data)
                        res["md5"] = __import__("hashlib").md5(bytes(data)).hexdigest()
                        if a.dump_dir:
                            (Path(a.dump_dir) / f"{fid}.bin").write_bytes(bytes(data))
                        head = bytes(data[:4])
                        res["magic"] = head.decode("ascii", "replace")
                        try:
                            if head in (b"MD20", b"MD21"):
                                info = m2lib.parse_any(bytes(data))
                                res["m2"] = {k: v for k, v in info.items() if k not in ("txid",) and not (k == "sfid" and len(v) > 12)}
                                if "txid" in info:
                                    res["m2"]["txid"] = info["txid"][:24]
                                if "sfid" in info:
                                    res["m2"]["sfid"] = info["sfid"][:12]
                            elif head == b"SKIN":
                                res["skin"] = m2lib.skin_counts(bytes(data))
                            elif head in (b"BLP2", b"BLP1"):
                                import struct
                                enc, alpha, atype, mips = data[8], data[9], data[10], data[11]
                                w, hh = struct.unpack_from("<II", data, 12)
                                res["blp"] = {"encoding": enc, "alpha_bits": alpha, "alpha_type": atype, "mips": mips, "w": w, "h": hh}
                        except Exception as e:  # noqa: BLE001
                            res["parse_error"] = repr(e)
                    elif a.parse:
                        res["skipped_read"] = "over cap"
                finally:
                    api.close_file(h)
            except c.CascError as e:
                res["error"] = e.error_code
            except RuntimeError as e:
                res["error"] = repr(e)
            if events:
                raise RuntimeError("CascLib reported a download progress event; aborting")
            rec["results"][str(fid)] = res
            if n % 500 == 0:
                print(n, len(ids), flush=True)
        rec["metadata_after"] = c.snapshot_input_metadata(inventory)
        rec["metadata_unchanged"] = rec["metadata_before"] == rec["metadata_after"]
        rec["download_events"] = len(events)
        rec["bytes_read"] = total
        rec["status"] = "ok"
    finally:
        try:
            api.close_storage(storage)
        except Exception:  # noqa: BLE001
            pass
        rec["finished_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
        Path(a.out).write_text(json.dumps(rec, indent=1), encoding="utf8")
    found = sum(1 for r in rec["results"].values() if "bytes" in r)
    print("requested", len(ids), "present", found, "bytes_read", total, "metadata_unchanged", rec.get("metadata_unchanged"))


if __name__ == "__main__":
    main()
