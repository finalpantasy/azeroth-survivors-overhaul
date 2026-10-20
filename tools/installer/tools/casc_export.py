#!/usr/bin/env python3
"""Export a small, hash-pinned source set from a caller's own client.

Derived from tools/local-asset-builder/export_local_assets.py (reviewed r3).
Only change: the catalog comes from --catalog (generated from an installer
recipe's ``casc`` source) instead of the single built-in mace catalog.

Default is metadata-only preflight. --extract requires the separately supplied,
audited offline CascLib runtime. No game install or client files are modified.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys


HERE = Path(__file__).resolve().parent
ADAPTER = HERE.parent.parent / "casc-offline" / "casc_offline.py"
LIBRARY_SHA256 = "221833e9afa0d534a4e0e5679cabb24a827a8ac1560b6722acd6d500bfcc28b3"


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def inside(path: Path, parent: Path) -> bool:
    return path.resolve().is_relative_to(parent.resolve())


def ensure_separate_output(client_root: Path, output: Path, catalog: dict) -> None:
    data = client_root / "Data"
    sources = {
        client_root.resolve(), data.resolve(), (data / "data").resolve(),
        (data / "indices").resolve(), (data / "config").resolve(),
        (client_root / ".build.info").resolve().parent,
    }
    for field in ("build_key", "cdn_key"):
        key = catalog[field]
        if len(key) != 32 or any(c not in "0123456789abcdef" for c in key):
            raise ValueError("Invalid catalog content key")
        sources.add((data / "config" / key[:2] / key[2:4] / key).resolve().parent)
    if any(inside(output, source) or inside(source, output) for source in sources):
        raise ValueError("Output and all resolved client source directories must be separate")
    if output.exists():
        raise FileExistsError("Output must be a new directory; existing files are never overwritten")


def load_adapter():
    # The same adapter is included alongside this script in a code-only bundle.
    path = HERE / "casc_offline.py"
    if not path.is_file():
        path = ADAPTER
    spec = importlib.util.spec_from_file_location("approved_casc_adapter", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("CASC adapter source is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prepare(client_root: Path, output: Path, catalog_path: Path):
    client_root = client_root.resolve(strict=True)
    if not client_root.is_dir():
        raise ValueError("Client root must be a directory")
    output = output.resolve()
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    ensure_separate_output(client_root, output, catalog)
    adapter = load_adapter()
    adapter.verify_runtime_shape()
    adapter.STORAGE_ROOT = str(client_root)
    adapter.STORAGE_BUILD_INFO = str(client_root / ".build.info")
    adapter.PRODUCT_CODE = catalog["product"]
    adapter.BUILD_KEY = catalog["build_key"]
    adapter.CDN_KEY = catalog["cdn_key"]
    adapter.EXPECTED_VERSION = catalog["version"]
    adapter.EXPECTED_BUILD_NUMBER = catalog["build_number"]
    adapter.EXPECTED_LOCALE_MASK = int(catalog.get("locale_mask", 2))
    inventory = {
        "public_build_metadata": {
            "path": ".build.info",
            "public_fields": {"cdn_key": catalog["cdn_key"]},
        },
    }
    for name, key in (("build", "build_key"), ("cdn", "cdn_key")):
        content_key = catalog[key]
        metadata = catalog[f"{name}_config"]
        inventory[f"selected_{name}_config" if name == "build" else "selected_cdn_config_summary"] = {
            "manifest": {
                "path": f"Data/config/{content_key[:2]}/{content_key[2:4]}/{content_key}",
                "bytes": metadata["bytes"], "sha256": metadata["sha256"],
            }
        }
    # Bound the only manifest whose size is not already pinned in the catalog.
    if (client_root / ".build.info").stat().st_size > 2 * 1024 * 1024:
        raise ValueError(".build.info exceeds the small metadata limit")
    before = adapter.snapshot_input_metadata(inventory)
    return adapter, catalog, inventory, before, output


def built_from_pinned_source(library: Path) -> bool:
    """True when library is <casc-offline>/build-x64/Release/CascLib.dll and a build-logs record proves
    build_runner.py built exactly this file from the source pinned in source-manifest.json (MSVC output is not
    byte-reproducible, so the pinned hash alone would reject every rebuild)."""
    try:
        tool = library.parents[2]
        manifest = tool / "source-manifest.json"
        if library.parent.name != "Release" or library.parents[1].name != "build-x64" or not manifest.is_file():
            return False
        wanted, manifest_hash = digest(library), digest(manifest)
        for record_path in sorted((tool / "build-logs").glob("build-record-*.json")):
            rec = json.loads(record_path.read_text(encoding="utf-8"))
            if (rec.get("status") == "build-success" and rec.get("dll_sha256") == wanted
                    and rec.get("source_manifest_sha256") == manifest_hash):
                return True
    except (OSError, ValueError, IndexError):
        return False
    return False


def export(args) -> dict:
    adapter, catalog, inventory, before, output = prepare(args.client_root, args.output, args.catalog)
    report = {
        "schema_version": 1, "status": "preflight-passed", "writes": False,
        "product": catalog["product"], "version": catalog["version"],
        "variant": catalog["variant"], "output_directory": str(output),
        "asset_count": len(catalog["assets"]),
        "scope": "cosmetic source export; no conversion, installation, or item-ID mapping",
    }
    if not args.extract:
        return report
    if args.library is None:
        raise ValueError("--extract requires --library pointing to the audited offline CascLib.dll")
    library = args.library.resolve(strict=True)
    if digest(library) != LIBRARY_SHA256 and not built_from_pinned_source(library):
        raise ValueError("CascLib runtime hash is not the audited offline build")
    if os.name != "nt":
        raise RuntimeError("Extraction requires 64-bit Windows")
    events = []
    callback = adapter.create_progress_callback(events)
    api = adapter.CascApi(library)
    storage = ctypes.c_void_p()
    ensure_separate_output(args.client_root.resolve(strict=True), output, catalog)
    output.mkdir(parents=True, exist_ok=False)
    report.update(status="started", writes=True, assets=[], progress_download_events=events)
    try:
        storage = api.open_storage(callback)
        report["storage_identity"] = adapter.verify_storage_identity(api, storage)
        if events:
            raise RuntimeError("Unexpected network/download event while opening offline storage")
        total = 0
        for asset in catalog["assets"]:
            # Both the bounded size and the content are pinned to the verified export.
            filename = asset["output"]
            if filename in (".", "..", "") or Path(filename).name != filename or "/" in filename or "\\" in filename:
                raise ValueError("Invalid catalog output filename")
            entry = adapter.extract_asset(
                api, storage, {"file_data_id": asset["file_data_id"], "name": filename},
                output / filename, asset["bytes"],
                allow_file_id_alias=False,
            )
            if events:
                raise RuntimeError("Unexpected network/download event during offline extraction")
            if entry["bytes"] != asset["bytes"] or entry["sha256"] != asset["sha256"]:
                raise RuntimeError(f"Extracted asset identity mismatch: {filename}")
            total += entry["bytes"]
            report["assets"].append(entry)
        report.update(status="success", total_payload_bytes=total)
    except Exception as exc:
        report.update(status="failed", error=f"{type(exc).__name__}: {exc}")
    finally:
        try:
            api.close_storage(storage)
        except Exception as exc:
            report.update(status="failed", cleanup_error=str(exc))
        try:
            after = adapter.snapshot_input_metadata(inventory)
            report["metadata_unchanged"] = before == after
            if before != after:
                report.update(status="failed", error="Client metadata changed during extraction")
        except Exception as exc:
            report.update(status="failed", metadata_unchanged=False, metadata_error=str(exc))
        if events:
            report.update(status="failed", error="Unexpected download event")
        with (output / "export-report.json").open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2)
            stream.write("\n")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", required=True, type=Path, help="exporter catalog JSON generated from a recipe")
    parser.add_argument("--client-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--library", type=Path)
    parser.add_argument("--extract", action="store_true")
    args = parser.parse_args()
    report = export(args)
    print(json.dumps(report, indent=2))
    return 0 if report["status"] in ("success", "preflight-passed") else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
