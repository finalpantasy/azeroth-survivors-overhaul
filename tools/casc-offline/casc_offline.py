#!/usr/bin/env python3
"""Constrained offline CASC reader for the pinned local WoW build."""

from __future__ import annotations

import argparse
import csv
import ctypes
import datetime as dt
import hashlib
import io
import json
import mmap
import os
import struct
import sys
import uuid
import zipfile
from pathlib import Path, PureWindowsPath


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
RUNTIME_ROOT = PROJECT_ROOT / "analysis" / "casc-reader-runtime"
DLL_PATH = PROJECT_ROOT / "tools" / "casc-offline" / "build-x64" / "Release" / "CascLib.dll"
SOURCE_MANIFEST = RUNTIME_ROOT / "source-manifest.json"
INVENTORY_PATH = PROJECT_ROOT / "analysis" / "client-sources" / "forever" / "source-inventory.json"
ASSET_MAP_PATH = RUNTIME_ROOT / "asset-map.json"
LISTFILE_ARCHIVE_PATH = PROJECT_ROOT / "tools" / "casc-offline" / "source" / "listfile" / "listfile-archive.zip"
MODEL_FILE_DATA_ID = 1011653
MODEL_NAME = "Character/Human/Male/HumanMale_HD.m2"
STORAGE_BUILD_INFO = r"E:\World of Warcraft\.build.info"
STORAGE_ROOT = r"E:\World of Warcraft"
PRODUCT_CODE = "wowf"
BUILD_KEY = "e8c4ef4e67bd1d94d820254984258fa8"
EXPECTED_BUILD_NUMBER = 70245
EXPECTED_VERSION = "1.60.1.70245"
EXPECTED_LOCALE_MASK = 0x00000002
CDN_KEY = "f065eade5276a5ac05606e60a4c5a1f7"
EXPECTED_ROOT_KEYS = ["101ff6795c7de0e10e8df3cae043fb24"]
EXPECTED_ENCODING_KEYS = [
    "53149bb4bdfa86da1b2673acda9a9557",
    "11e7d357e8516de7eb068e9d50b3b1a8",
]
ACTIVE_PROFILE_NAME = "forever"
CLIENT_PROFILES = {
    "forever": {
        "inventory_path": PROJECT_ROOT / "analysis" / "client-sources" / "forever" / "source-inventory.json",
        "asset_map_path": RUNTIME_ROOT / "asset-map.json",
        "product_code": "wowf",
        "build_key": "e8c4ef4e67bd1d94d820254984258fa8",
        "cdn_key": CDN_KEY,
        "build_number": 70245,
        "version": "1.60.1.70245",
        "locale_mask": 0x00000002,
        "root_keys": ["101ff6795c7de0e10e8df3cae043fb24"],
        "encoding_keys": [
            "53149bb4bdfa86da1b2673acda9a9557",
            "11e7d357e8516de7eb068e9d50b3b1a8",
        ],
        "model_file_data_id": 1011653,
        "model_name": "Character/Human/Male/HumanMale_HD.m2",
    },
    "retail": {
        "inventory_path": PROJECT_ROOT / "analysis" / "client-sources" / "retail" / "source-inventory.json",
        "asset_map_path": RUNTIME_ROOT / "asset-map-retail.json",
        "product_code": "wow",
        "build_key": "88d5e8ef05ec5a8446cdec93d142d7c6",
        "cdn_key": CDN_KEY,
        "build_number": 70448,
        "version": "12.1.5.70448",
        "locale_mask": 0x00000002,
        "root_keys": ["e84483ee756c69a7dc5fbff04af81316"],
        "encoding_keys": [
            "c36d0a7a0645d12c52932ec8c8f7fea2",
            "6e9260e140bd5f5b116d47229e937993",
        ],
        "model_candidates": [
            {
                "file_data_id": 1693135,
                "name": "item/objectcomponents/weapon/mace_1h_kultirasquest_b_01.m2",
            },
            {
                "file_data_id": 1661325,
                "name": "item/objectcomponents/weapon/mace_1h_zandalarquest_b_01.m2",
            },
        ],
    },
}
ONLINE_FEATURE = 0x00000400
FORCE_DOWNLOAD_FEATURE = 0x00001000
OPEN_BY_FILEID = 0x00000003
STRICT_DATA_CHECK = 0x00000010
OVERCOME_ENCRYPTED = 0x00000020
MAX_FILE_BYTES = 128 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024
MAX_ITEM_CLOSURE_BYTES = 128 * 1024 * 1024
READ_CHUNK_BYTES = 4 * 1024 * 1024
DOWNLOAD_PROGRESS_MESSAGE = 2


def activate_client_profile(name: str) -> None:
    """Select one explicitly pinned installed build while preserving Forever defaults."""
    global ACTIVE_PROFILE_NAME, INVENTORY_PATH, ASSET_MAP_PATH, PRODUCT_CODE, BUILD_KEY
    global CDN_KEY, EXPECTED_BUILD_NUMBER, EXPECTED_VERSION, EXPECTED_LOCALE_MASK
    global EXPECTED_ROOT_KEYS, EXPECTED_ENCODING_KEYS, MODEL_FILE_DATA_ID, MODEL_NAME
    profile = CLIENT_PROFILES.get(name)
    if profile is None:
        raise ValueError(f"Unknown client profile: {name}")
    ACTIVE_PROFILE_NAME = name
    INVENTORY_PATH = profile["inventory_path"]
    ASSET_MAP_PATH = profile["asset_map_path"]
    PRODUCT_CODE = profile["product_code"]
    BUILD_KEY = profile["build_key"]
    CDN_KEY = profile["cdn_key"]
    EXPECTED_BUILD_NUMBER = profile["build_number"]
    EXPECTED_VERSION = profile["version"]
    EXPECTED_LOCALE_MASK = profile["locale_mask"]
    EXPECTED_ROOT_KEYS = list(profile["root_keys"])
    EXPECTED_ENCODING_KEYS = list(profile["encoding_keys"])
    if name == "forever":
        MODEL_FILE_DATA_ID = profile["model_file_data_id"]
        MODEL_NAME = profile["model_name"]
    else:
        candidate = profile["model_candidates"][0]
        MODEL_FILE_DATA_ID = candidate["file_data_id"]
        MODEL_NAME = candidate["name"]


class CascError(RuntimeError):
    def __init__(self, operation: str, error_code: int, detail: str = "") -> None:
        self.operation = operation
        self.error_code = int(error_code)
        self.detail = detail
        suffix = f" ({self.error_code})"
        if detail:
            suffix += f": {detail}"
        super().__init__(f"{operation} failed{suffix}")


class OpenStorageArgs(ctypes.Structure):
    _fields_ = [
        ("Size", ctypes.c_size_t),
        ("szLocalPath", ctypes.c_wchar_p),
        ("szCodeName", ctypes.c_wchar_p),
        ("szRegion", ctypes.c_wchar_p),
        ("PfnProgressCallback", ctypes.c_void_p),
        ("PtrProgressParam", ctypes.c_void_p),
        ("PfnProductCallback", ctypes.c_void_p),
        ("PtrProductParam", ctypes.c_void_p),
        ("dwLocaleMask", ctypes.c_uint32),
        ("dwFlags", ctypes.c_uint32),
        ("szBuildKey", ctypes.c_wchar_p),
        ("szCdnHostUrl", ctypes.c_wchar_p),
    ]


class StorageProduct(ctypes.Structure):
    _fields_ = [("szCodeName", ctypes.c_char * 0x1C), ("BuildNumber", ctypes.c_uint32)]


class FileFullInfo(ctypes.Structure):
    _fields_ = [
        ("CKey", ctypes.c_ubyte * 16),
        ("EKey", ctypes.c_ubyte * 16),
        ("DataFileName", ctypes.c_char * 0x10),
        ("StorageOffset", ctypes.c_uint64),
        ("SegmentOffset", ctypes.c_uint64),
        ("TagBitMask", ctypes.c_uint64),
        ("FileNameHash", ctypes.c_uint64),
        ("ContentSize", ctypes.c_uint64),
        ("EncodedSize", ctypes.c_uint64),
        ("SegmentIndex", ctypes.c_uint32),
        ("SpanCount", ctypes.c_uint32),
        ("FileDataId", ctypes.c_uint32),
        ("LocaleFlags", ctypes.c_uint32),
        ("ContentFlags", ctypes.c_uint32),
    ]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_runtime_shape() -> dict[str, int]:
    if ctypes.sizeof(ctypes.c_void_p) != 8:
        raise RuntimeError("This adapter requires the audited 64-bit Windows ABI")
    observed = {
        "open_storage_args": ctypes.sizeof(OpenStorageArgs),
        "storage_product": ctypes.sizeof(StorageProduct),
        "file_full_info": ctypes.sizeof(FileFullInfo),
    }
    expected = {"open_storage_args": 88, "storage_product": 32, "file_full_info": 120}
    if observed != expected:
        raise RuntimeError(f"CascLib structure layout mismatch: expected {expected}, got {observed}")
    if OPEN_BY_FILEID | STRICT_DATA_CHECK != 0x13 or OPEN_BY_FILEID & OVERCOME_ENCRYPTED:
        raise RuntimeError("Unsafe CascLib open flags")
    return observed


def load_source_manifest() -> dict:
    manifest = json.loads(SOURCE_MANIFEST.read_text(encoding="utf-8"))
    if manifest.get("commit") != "4971d363e665551ac4142f541e5f2d71f1cda653":
        raise RuntimeError("Unexpected CascLib source commit")
    for entry in manifest.get("source_files", []):
        path = PROJECT_ROOT / "tools" / "casc-offline" / "source" / Path(entry["path"])
        if not path.is_file() or sha256_file(path) != entry["sha256"]:
            raise RuntimeError(f"CascLib source hash mismatch: {entry['path']}")
    return manifest


def load_inventory() -> dict:
    inventory = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))
    public = inventory["public_build_metadata"]
    selected_build = inventory["selected_build_config"]
    selected = selected_build["manifest"]
    selected_cdn = inventory["selected_cdn_config_summary"]
    cdn = selected_cdn["manifest"]
    fields = public["public_fields"]
    build_path = f"Data/config/{BUILD_KEY[:2]}/{BUILD_KEY[2:4]}/{BUILD_KEY}"
    cdn_path = f"Data/config/{CDN_KEY[:2]}/{CDN_KEY[2:4]}/{CDN_KEY}"
    if (
        inventory.get("profile", ACTIVE_PROFILE_NAME) != ACTIVE_PROFILE_NAME
        or inventory.get("source_root") != STORAGE_ROOT
        or public["path"] not in (STORAGE_BUILD_INFO, ".build.info")
        or fields["product"] != PRODUCT_CODE
        or fields["version"] != EXPECTED_VERSION
        or fields["build_key"].lower() != BUILD_KEY
        or fields["cdn_key"].lower() != CDN_KEY
        or selected["path"].replace("\\", "/") != build_path
        or cdn["path"].replace("\\", "/") != cdn_path
    ):
        raise RuntimeError("The audited local build inventory does not match this reader's pinned storage")
    relevant = selected_build["relevant_keys"]
    if relevant.get("root") != EXPECTED_ROOT_KEYS:
        raise RuntimeError(f"Pinned {ACTIVE_PROFILE_NAME} root keys differ from its audited profile")
    if relevant.get("encoding") != EXPECTED_ENCODING_KEYS:
        raise RuntimeError(f"Pinned {ACTIVE_PROFILE_NAME} encoding keys differ from its audited profile")
    if selected["bytes"] > 262_144 or cdn["bytes"] > 262_144:
        raise RuntimeError("Selected build/CDN manifests exceed the small metadata limit")
    return inventory


def input_metadata_paths(inventory: dict) -> list[tuple[str, Path, dict]]:
    public = inventory["public_build_metadata"]
    build = inventory["selected_build_config"]["manifest"]
    cdn = inventory["selected_cdn_config_summary"]["manifest"]
    entries = [("build_info", public), ("build_config", build), ("cdn_config", cdn)]
    result: list[tuple[str, Path, dict]] = []
    root = os.path.normcase(os.path.abspath(STORAGE_ROOT))
    for name, entry in entries:
        raw_path = entry["path"]
        pure = PureWindowsPath(raw_path)
        full_path = str(pure if pure.is_absolute() else PureWindowsPath(STORAGE_ROOT) / pure)
        normalized = os.path.normcase(os.path.abspath(full_path))
        if not normalized.startswith(root + os.sep):
            raise RuntimeError(f"Input metadata path escapes the audited client root: {raw_path}")
        result.append((name, Path(full_path), entry))
    return result


def read_metadata_snapshot(path: Path, name: str, expected: dict) -> dict[str, object]:
    before = path.stat()
    limit = 2 * 1024 * 1024
    if before.st_size > limit:
        raise RuntimeError(f"Input metadata exceeds the small-file limit: {path}")
    with path.open("rb") as stream:
        payload = stream.read(limit + 1)
    if len(payload) > limit:
        raise RuntimeError(f"Input metadata grew beyond the small-file limit: {path}")
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or len(payload) != after.st_size:
        raise RuntimeError(f"Input metadata changed while being read: {path}")
    snapshot: dict[str, object] = {
        "path": str(path),
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "last_write_time_ns": after.st_mtime_ns,
    }
    if name == "build_info":
        text = payload.decode("utf-8-sig", errors="strict")
        parsed_rows = list(csv.DictReader(io.StringIO(text, newline=""), delimiter="|"))
        rows = [
            {key.split("!", 1)[0]: value for key, value in row.items() if key is not None}
            for row in parsed_rows
        ]
        matches = [
            row for row in rows
            if row.get("Product") == PRODUCT_CODE and row.get("Build Key", "").lower() == BUILD_KEY
        ]
        if len(matches) != 1:
            raise RuntimeError(f"Expected exactly one pinned {PRODUCT_CODE} build row in {path}; found {len(matches)}")
        row = matches[0]
        public = expected["public_fields"]
        identity = {
            "product": row.get("Product"),
            "version": row.get("Version"),
            "build_key": row.get("Build Key", "").lower(),
            "cdn_key": row.get("CDN Key", "").lower(),
            "locale_tags": row.get("Tags", ""),
        }
        if (
            identity["version"] != EXPECTED_VERSION
            or identity["cdn_key"] != public["cdn_key"].lower()
            or "enUS" not in identity["locale_tags"]
        ):
            raise RuntimeError(f"The pinned {PRODUCT_CODE} row changed identity: {identity}")
        snapshot["selected_build"] = identity
    else:
        if len(payload) != expected["bytes"] or snapshot["sha256"] != expected["sha256"]:
            raise RuntimeError(f"Input metadata differs from its audited build snapshot: {path}")
    return snapshot


def snapshot_input_metadata(inventory: dict) -> dict[str, dict[str, object]]:
    snapshot: dict[str, dict[str, object]] = {}
    for name, path, entry in input_metadata_paths(inventory):
        snapshot[name] = read_metadata_snapshot(path, name, entry)
    return snapshot


def validate_root_alias_evidence(item: dict) -> dict | None:
    alias = item.get("root_alias")
    if alias is None:
        return None
    requested_id = item["file_data_id"]
    reported_id = alias.get("reported_file_data_id")
    if (
        alias.get("requested_file_data_id") != requested_id
        or not isinstance(reported_id, int)
        or reported_id <= 0
        or reported_id > 0xFFFFFFFF
        or reported_id == requested_id
    ):
        raise RuntimeError("Invalid root FileDataID alias mapping")
    ckey = alias.get("CKey")
    sha256 = alias.get("sha256")
    size = alias.get("bytes")
    evidence_name = alias.get("verification_record")
    evidence_sha256 = alias.get("verification_record_sha256")
    if (
        not isinstance(ckey, str) or len(ckey) != 32
        or not isinstance(sha256, str) or len(sha256) != 64
        or not isinstance(size, int) or size <= 0 or size > MAX_FILE_BYTES
        or not isinstance(evidence_name, str) or Path(evidence_name).name != evidence_name
        or not isinstance(evidence_sha256, str) or len(evidence_sha256) != 64
    ):
        raise RuntimeError("Incomplete root FileDataID alias evidence")
    evidence_path = RUNTIME_ROOT / evidence_name
    if not evidence_path.is_file() or sha256_file(evidence_path) != evidence_sha256.lower():
        raise RuntimeError("Root FileDataID alias evidence file is missing or changed")
    proof = json.loads(evidence_path.read_text(encoding="utf-8"))
    if (
        proof.get("status") != "alias-verified"
        or proof.get("source_commit") != json.loads(SOURCE_MANIFEST.read_text(encoding="utf-8")).get("commit")
        or proof.get("strict_open_flags") != (OPEN_BY_FILEID | STRICT_DATA_CHECK)
        or proof.get("byte_for_byte_equal") is not True
        or proof.get("metadata_unchanged") is not True
        or proof.get("progress_download_events") != []
    ):
        raise RuntimeError("Root FileDataID alias proof did not pass strict offline checks")
    storage = proof.get("storage_identity", {})
    if (
        storage.get("product") != PRODUCT_CODE
        or storage.get("build_number") != EXPECTED_BUILD_NUMBER
        or int(storage.get("features", 0)) & (ONLINE_FEATURE | FORCE_DOWNLOAD_FEATURE)
    ):
        raise RuntimeError("Root FileDataID alias proof came from an unexpected or online storage")
    opens = {
        entry.get("requested_file_data_id"): entry
        for entry in proof.get("opens", [])
        if isinstance(entry, dict)
    }
    requested = opens.get(requested_id)
    reported = opens.get(reported_id)
    if requested is None or reported is None:
        raise RuntimeError("Root FileDataID alias proof is missing one of the strict opens")
    for opened in (requested, reported):
        read = opened.get("read", {})
        if (
            opened.get("reported_file_data_id") != reported_id
            or opened.get("CKey", "").lower() != ckey.lower()
            or opened.get("bytes") != size
            or read.get("bytes") != size
            or read.get("content_md5", "").lower() != ckey.lower()
            or read.get("sha256", "").lower() != sha256.lower()
        ):
            raise RuntimeError("Root FileDataID alias opens do not match the mapped content proof")
    return alias


def load_asset_map() -> dict:
    if not ASSET_MAP_PATH.is_file():
        raise RuntimeError(f"Missing asset mapping evidence: {ASSET_MAP_PATH}")
    mapping = json.loads(ASSET_MAP_PATH.read_text(encoding="utf-8"))
    if mapping.get("schema_version") != 1:
        raise RuntimeError("Unsupported asset-map schema")
    model = mapping.get("model", {})
    texture = mapping.get("texture", {})
    for label, item, suffix in (("model", model, ".m2"), ("texture", texture, ".blp")):
        file_id = item.get("file_data_id")
        name = item.get("name")
        if not isinstance(file_id, int) or not (0 < file_id <= 0xFFFFFFFF):
            raise RuntimeError(f"Invalid {label} FileDataID in asset map")
        if not isinstance(name, str) or not name.lower().endswith(suffix):
            raise RuntimeError(f"Invalid {label} filename in asset map; expected {suffix}")
    reference = mapping.get("texture_reference", {})
    if reference.get("encoding") != "u32le" or not isinstance(reference.get("offset"), int):
        raise RuntimeError("Asset map must include a directly verifiable u32le texture reference")
    if reference.get("file_data_id") != texture.get("file_data_id"):
        raise RuntimeError("The reference evidence does not identify the mapped texture FileDataID")
    if not mapping.get("name_resolution_evidence"):
        raise RuntimeError("Asset map lacks name-to-FileDataID evidence")
    validate_root_alias_evidence(texture)
    return mapping


class CascApi:
    """Binds only the audited subset of CascLib exports."""

    def __init__(self, dll_path: Path) -> None:
        if os.name != "nt":
            raise RuntimeError("CascLib runtime is supported only on Windows")
        if not dll_path.is_absolute() or not dll_path.is_file():
            raise RuntimeError(f"Project-local CascLib DLL not found: {dll_path}")
        self.dll_path = dll_path.resolve()
        self.dll_sha256 = sha256_file(self.dll_path)
        flags = 0x00000100 | 0x00001000  # LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | DEFAULT_DIRS
        self._library = ctypes.WinDLL(str(self.dll_path), winmode=flags)
        self._functions: dict[str, object] = {}
        self._bind("CascOpenStorageEx", [ctypes.c_wchar_p, ctypes.POINTER(OpenStorageArgs), ctypes.c_bool, ctypes.POINTER(ctypes.c_void_p)], ctypes.c_bool)
        self._bind("CascGetStorageInfo", [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)], ctypes.c_bool)
        self._bind("CascOpenFile", [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)], ctypes.c_bool)
        self._bind("CascGetFileInfo", [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)], ctypes.c_bool)
        self._bind("CascGetFileSize64", [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint64)], ctypes.c_bool)
        self._bind("CascReadFile", [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32)], ctypes.c_bool)
        self._bind("CascCloseFile", [ctypes.c_void_p], ctypes.c_bool)
        self._bind("CascCloseStorage", [ctypes.c_void_p], ctypes.c_bool)
        self._bind("GetCascError", [], ctypes.c_uint32)

    def _bind(self, name: str, argtypes: list, restype: object) -> None:
        function = getattr(self._library, name)
        function.argtypes = argtypes
        function.restype = restype
        self._functions[name] = function

    @property
    def bound_exports(self) -> list[str]:
        return sorted(self._functions)

    def error(self) -> int:
        return int(self._functions["GetCascError"]())

    def check(self, succeeded: bool, operation: str) -> None:
        if not succeeded:
            raise CascError(operation, self.error())

    def open_storage_path(self, local_path: str, progress_callback, open_flags: int = 0, online_storage: bool = False) -> ctypes.c_void_p:
        args = OpenStorageArgs()
        args.Size = ctypes.sizeof(OpenStorageArgs)
        args.szLocalPath = local_path
        args.szCodeName = PRODUCT_CODE
        args.szRegion = None
        args.PfnProgressCallback = ctypes.cast(progress_callback, ctypes.c_void_p)
        args.PtrProgressParam = None
        args.PfnProductCallback = None
        args.PtrProductParam = None
        args.dwLocaleMask = EXPECTED_LOCALE_MASK
        args.dwFlags = open_flags
        args.szBuildKey = BUILD_KEY
        args.szCdnHostUrl = None
        storage = ctypes.c_void_p()
        ok = self._functions["CascOpenStorageEx"](None, ctypes.byref(args), online_storage, ctypes.byref(storage))
        self.check(bool(ok), "CascOpenStorageEx")
        if not storage.value:
            raise RuntimeError("CascOpenStorageEx succeeded without returning a storage handle")
        return storage

    def open_storage(self, progress_callback) -> ctypes.c_void_p:
        return self.open_storage_path(STORAGE_BUILD_INFO, progress_callback, open_flags=0)

    def storage_info(self, storage: ctypes.c_void_p, info_class: int, output: ctypes.Structure) -> None:
        needed = ctypes.c_size_t()
        ok = self._functions["CascGetStorageInfo"](storage, info_class, ctypes.byref(output), ctypes.sizeof(output), ctypes.byref(needed))
        self.check(bool(ok), f"CascGetStorageInfo({info_class})")

    def open_file(self, storage: ctypes.c_void_p, file_id: int) -> ctypes.c_void_p:
        handle = ctypes.c_void_p()
        # CascLib encodes a FileDataID in the pointer value, not in a pointed-to integer buffer.
        ok = self._functions["CascOpenFile"](storage, ctypes.c_void_p(file_id), EXPECTED_LOCALE_MASK, OPEN_BY_FILEID | STRICT_DATA_CHECK, ctypes.byref(handle))
        self.check(bool(ok), f"CascOpenFile(FileDataID={file_id})")
        if not handle.value:
            raise RuntimeError(f"CascOpenFile returned no handle for FileDataID {file_id}")
        return handle

    def file_info(self, handle: ctypes.c_void_p) -> FileFullInfo:
        info = FileFullInfo()
        needed = ctypes.c_size_t()
        ok = self._functions["CascGetFileInfo"](handle, 2, ctypes.byref(info), ctypes.sizeof(info), ctypes.byref(needed))
        self.check(bool(ok), "CascGetFileInfo(CascFileFullInfo)")
        return info

    def file_size(self, handle: ctypes.c_void_p) -> int:
        size = ctypes.c_uint64()
        self.check(bool(self._functions["CascGetFileSize64"](handle, ctypes.byref(size))), "CascGetFileSize64")
        return int(size.value)

    def read_file(self, handle: ctypes.c_void_p, size: int, temporary_path: Path) -> dict:
        sha256 = hashlib.sha256()
        content_md5 = hashlib.md5()
        bytes_written = 0
        with temporary_path.open("xb") as output:
            while bytes_written < size:
                request = min(READ_CHUNK_BYTES, size - bytes_written)
                buffer = ctypes.create_string_buffer(request)
                actual = ctypes.c_uint32()
                ok = self._functions["CascReadFile"](handle, ctypes.cast(buffer, ctypes.c_void_p), request, ctypes.byref(actual))
                self.check(bool(ok), "CascReadFile")
                if actual.value != request:
                    raise RuntimeError(f"Short CascReadFile: requested {request}, received {actual.value}")
                block = buffer.raw[: actual.value]
                output.write(block)
                sha256.update(block)
                content_md5.update(block)
                bytes_written += actual.value
            output.flush()
            os.fsync(output.fileno())
        return {"bytes": bytes_written, "sha256": sha256.hexdigest(), "content_md5": content_md5.hexdigest()}

    def read_file_bytes(self, handle: ctypes.c_void_p, size: int) -> tuple[bytearray, dict[str, int | str]]:
        payload = bytearray()
        sha256 = hashlib.sha256()
        content_md5 = hashlib.md5()
        while len(payload) < size:
            request = min(READ_CHUNK_BYTES, size - len(payload))
            buffer = ctypes.create_string_buffer(request)
            actual = ctypes.c_uint32()
            ok = self._functions["CascReadFile"](handle, ctypes.cast(buffer, ctypes.c_void_p), request, ctypes.byref(actual))
            self.check(bool(ok), "CascReadFile")
            if actual.value != request:
                raise RuntimeError(f"Short CascReadFile: requested {request}, received {actual.value}")
            block = buffer.raw[: actual.value]
            payload.extend(block)
            sha256.update(block)
            content_md5.update(block)
        return payload, {"bytes": len(payload), "sha256": sha256.hexdigest(), "content_md5": content_md5.hexdigest()}

    def close_file(self, handle: ctypes.c_void_p) -> None:
        if handle and handle.value:
            self.check(bool(self._functions["CascCloseFile"](handle)), "CascCloseFile")

    def close_storage(self, storage: ctypes.c_void_p) -> None:
        if storage and storage.value:
            self.check(bool(self._functions["CascCloseStorage"](storage)), "CascCloseStorage")


def create_progress_callback(events: list[dict]):
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32, ctypes.c_uint32)

    @callback_type
    def callback(_user, message, object_name, current, total):
        if message == DOWNLOAD_PROGRESS_MESSAGE:
            events.append({
                "message": int(message),
                "object": object_name.decode("ascii", errors="replace") if object_name else None,
                "current": int(current),
                "total": int(total),
            })
            return True
        return False

    return callback


def verify_storage_identity(api: CascApi, storage: ctypes.c_void_p) -> dict:
    features = ctypes.c_uint32()
    api.storage_info(storage, 2, features)  # CascStorageFeatures
    if features.value & (ONLINE_FEATURE | FORCE_DOWNLOAD_FEATURE):
        raise RuntimeError(f"CascLib opened an unsafe storage mode: features=0x{features.value:08x}")
    product = StorageProduct()
    api.storage_info(storage, 4, product)  # CascStorageProduct
    code = bytes(product.szCodeName).split(b"\0", 1)[0].decode("ascii", errors="strict")
    if code != PRODUCT_CODE or product.BuildNumber != EXPECTED_BUILD_NUMBER:
        raise RuntimeError(f"Unexpected storage identity: product={code!r}, build={product.BuildNumber}")
    return {"product": code, "build_number": int(product.BuildNumber), "features": int(features.value)}


def inspect_asset_bytes(path: Path, extension: str) -> dict:
    with path.open("rb") as stream:
        head = stream.read(16)
    magic = head[:4].decode("ascii", errors="replace")
    if extension == ".m2" and magic not in ("MD20", "MD21"):
        raise RuntimeError(f"Model format not recognized: {magic!r}")
    if extension == ".blp" and magic not in ("BLP1", "BLP2"):
        raise RuntimeError(f"Texture format not recognized: {magic!r}")
    if extension == ".skin" and magic != "SKIN":
        raise RuntimeError(f"Skin format not recognized: {magic!r}")
    version = struct.unpack_from("<I", head, 4)[0] if extension == ".m2" and magic == "MD20" and len(head) >= 8 else None
    result = {"magic": magic, "version": version, "header_hex": head.hex()}
    if extension == ".blp":
        with path.open("rb") as stream:
            blp_header = stream.read(148)
        if len(blp_header) < 20:
            raise RuntimeError("BLP header is truncated before its dimensions")
        encoding = blp_header[8]
        width = struct.unpack_from("<I", blp_header, 12)[0]
        height = struct.unpack_from("<I", blp_header, 16)[0]
        if width == 0 or height == 0 or width > 16_384 or height > 16_384:
            raise RuntimeError(f"BLP dimensions outside allowed bounds: {width}x{height}")
        result["blp"] = {
            "type": struct.unpack_from("<I", blp_header, 4)[0],
            "encoding": encoding,
            "encoding_name": {0: "jpeg", 1: "paletted", 2: "dxt", 3: "bgra8888", 4: "argb1555", 5: "argb4444", 7: "bgra8888"}.get(encoding, "unknown"),
            "alpha_depth": blp_header[9],
            "alpha_encoding": blp_header[10],
            "has_mipmaps": bool(blp_header[11]),
            "width": width,
            "height": height,
        }
    return result


def verify_texture_reference(model_path: Path, model_sha256: str, texture_id: int, reference: dict) -> dict:
    offset = reference["offset"]
    with model_path.open("rb") as model:
        model.seek(offset)
        raw = model.read(4)
    if len(raw) != 4:
        raise RuntimeError("Texture-reference offset is outside the extracted M2 file")
    observed = struct.unpack("<I", raw)[0]
    if observed != texture_id:
        raise RuntimeError(f"M2 texture reference mismatch at {offset}: expected {texture_id}, found {observed}")
    return {"offset": offset, "encoding": "u32le", "file_data_id": observed, "model_sha256": model_sha256}


def resolve_listfile_ids(file_ids: set[int], manifest: dict) -> dict[int, list[str]]:
    archive_rel = "listfile/listfile-archive.zip"
    archive_entry = next((item for item in manifest.get("source_files", []) if item.get("path") == archive_rel), None)
    if archive_entry is None or sha256_file(LISTFILE_ARCHIVE_PATH) != archive_entry.get("sha256"):
        raise RuntimeError("Pinned listfile archive hash is missing or does not match the source manifest")
    wanted = {str(value) for value in file_ids}
    matches: dict[int, list[str]] = {value: [] for value in file_ids}
    with zipfile.ZipFile(LISTFILE_ARCHIVE_PATH) as archive:
        with archive.open("listfile.csv") as listing:
            for raw_line in listing:
                line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
                file_id_text, separator, name = line.partition(";")
                if separator and file_id_text in wanted:
                    matches[int(file_id_text)].append(name)
    return matches


def parse_chunked_m2_texture_refs(payload: bytes | bytearray) -> dict[str, object]:
    if payload[:4] != b"MD21":
        raise RuntimeError(f"Expected a chunked MD21 M2, found {payload[:4]!r}")
    cursor = 0
    model_version = None
    core_range = None
    texture_ids: list[dict[str, int]] = []
    skin_ids: list[dict[str, int]] = []
    saw_txid = False
    saw_sfid = False
    while cursor < len(payload):
        if cursor + 8 > len(payload):
            raise RuntimeError(f"Truncated M2 chunk header at offset {cursor}")
        tag = bytes(payload[cursor : cursor + 4])
        size = struct.unpack_from("<I", payload, cursor + 4)[0]
        data_start = cursor + 8
        data_end = data_start + size
        if data_end > len(payload):
            raise RuntimeError(f"M2 chunk {tag!r} exceeds the model bounds")
        if tag == b"MD21":
            if core_range is not None:
                raise RuntimeError("M2 contains more than one MD21 core chunk")
            if size < 8 or payload[data_start : data_start + 4] != b"MD20":
                raise RuntimeError("MD21 core chunk does not contain an MD20 header")
            model_version = struct.unpack_from("<I", payload, data_start + 4)[0]
            core_range = (data_start, data_end)
        elif tag == b"TXID":
            if saw_txid:
                raise RuntimeError("M2 contains more than one TXID texture-reference chunk")
            saw_txid = True
            if size % 4:
                raise RuntimeError("TXID texture-reference chunk is not a u32 array")
            for index in range(size // 4):
                value_offset = data_start + index * 4
                file_id = struct.unpack_from("<I", payload, value_offset)[0]
                texture_ids.append({"index": index, "file_data_id": file_id, "offset": value_offset})
        elif tag == b"SFID":
            if saw_sfid:
                raise RuntimeError("M2 contains more than one SFID skin-reference chunk")
            saw_sfid = True
            if size % 4:
                raise RuntimeError("SFID skin-reference chunk is not a u32 array")
            for index in range(size // 4):
                file_id = struct.unpack_from("<I", payload, data_start + index * 4)[0]
                skin_ids.append({"index": index, "file_data_id": file_id})
        cursor = data_end
    if cursor != len(payload) or model_version is None or core_range is None:
        raise RuntimeError("M2 chunk stream is incomplete or lacks its MD21 core")
    return {
        "container": "MD21",
        "md20_version": model_version,
        "core_range": core_range,
        "texture_references": texture_ids,
        "skin_file_ids": skin_ids,
        "txid_present": saw_txid,
        "sfid_present": saw_sfid,
    }


def parse_modern_m2_header(core: bytes | bytearray | memoryview, md20_version: int) -> dict[str, int]:
    """Read stable count fields from the version-264+ MD20 header without decoding arrays."""
    if len(core) < 116 or bytes(core[:4]) != b"MD20":
        raise RuntimeError("Modern MD20 core is truncated or has an invalid signature")
    version = struct.unpack_from("<I", core, 4)[0]
    if version != md20_version or version < 264:
        raise RuntimeError(f"Unsupported chunked model version for retail summary: {version}")

    def count_at(offset: int) -> int:
        if offset + 4 > len(core):
            raise RuntimeError(f"MD20 count field is outside the core at 0x{offset:x}")
        return struct.unpack_from("<I", core, offset)[0]

    fields = {
        "animations": 0x1C,
        "bones": 0x2C,
        "vertices": 0x3C,
        "views": 0x44,
        "colors": 0x48,
        "textures": 0x50,
        "transparency_tracks": 0x58,
        "texture_animations": 0x60,
        "texture_replacements": 0x68,
        "render_flags": 0x70,
    }
    result = {name: count_at(offset) for name, offset in fields.items()}
    limits = {
        "animations": 100_000, "bones": 100_000, "vertices": 10_000_000,
        "views": 1_000, "colors": 100_000, "textures": 100_000,
        "transparency_tracks": 100_000, "texture_animations": 100_000,
        "texture_replacements": 100_000, "render_flags": 100_000,
    }
    for name, value in result.items():
        if value > limits[name]:
            raise RuntimeError(f"Implausible modern M2 {name} count: {value}")
    return result


def parse_skin_metadata(payload: bytes | bytearray, md20_version: int) -> dict[str, object]:
    """Validate the first external skin profile's arrays and report section/batch counts."""
    if len(payload) < 48 or payload[:4] != b"SKIN":
        raise RuntimeError("Skin payload is truncated or lacks the SKIN signature")
    arrays = []
    for offset in (4, 12, 20, 28, 36):
        count, start = struct.unpack_from("<II", payload, offset)
        arrays.append({"count": count, "offset": start})
    vertex_lookup, triangles, properties, sections, batches = arrays
    bone_count = struct.unpack_from("<I", payload, 44)[0]
    if bone_count > 10_000:
        raise RuntimeError("Implausible skin-profile bone count")
    section_size = 48 if md20_version >= 260 else 32
    for entry, name in ((vertex_lookup, "vertex lookup"), (triangles, "triangle indices"), (properties, "properties")):
        if entry["count"] > 10_000_000 or entry["offset"] > len(payload):
            raise RuntimeError(f"Skin {name} array starts outside the payload bounds")
    for entry, stride, name in ((sections, section_size, "sections"), (batches, 24, "batches")):
        count, start = entry["count"], entry["offset"]
        if count > 10_000_000 or start > len(payload) or count * stride > len(payload) - start:
            raise RuntimeError(f"Skin {name} array exceeds the payload bounds")
    return {
        "magic": "SKIN",
        "vertex_lookup_count": vertex_lookup["count"],
        "triangle_index_count": triangles["count"],
        "property_count": properties["count"],
        "section_count": sections["count"],
        "batch_count": batches["count"],
        "bone_count": bone_count,
        "section_record_bytes": section_size,
        "header_bytes": 48,
    }


def parse_modern_texture_slots(core: bytes | bytearray | memoryview) -> list[dict[str, int]]:
    """Separate model-owned textures from display-selected replaceable textures."""
    if len(core) < 0x58 or bytes(core[:4]) != b"MD20":
        raise RuntimeError("Invalid modern M2 texture header")
    count, start = struct.unpack_from("<II", core, 0x50)
    if count > 100_000 or start > len(core) or count * 16 > len(core) - start:
        raise RuntimeError("Modern M2 texture records exceed the core bounds")
    return [
        {"index": i, "texture_type": struct.unpack_from("<I", core, start + i * 16)[0]}
        for i in range(count)
    ]


def extract_asset(
    api: CascApi,
    storage: ctypes.c_void_p,
    item: dict,
    output_path: Path,
    max_payload_bytes: int = MAX_FILE_BYTES,
    *,
    allow_file_id_alias: bool = True,
) -> dict:
    file_id = item["file_data_id"]
    name = item["name"]
    suffix = Path(name).suffix.lower()
    handle = api.open_file(storage, file_id)
    temporary = output_path.with_suffix(output_path.suffix + ".partial")
    if temporary.exists() or output_path.exists():
        api.close_file(handle)
        raise FileExistsError(f"Refusing to overwrite {output_path}")
    try:
        info = api.file_info(handle)
        reported_file_id = int(info.FileDataId)
        if not allow_file_id_alias and reported_file_id != file_id:
            raise RuntimeError(f"Exact FileDataID required: requested {file_id}, returned {reported_file_id}")
        alias = validate_root_alias_evidence(item)
        alias_verified = reported_file_id not in (file_id, 0xFFFFFFFF)
        size = api.file_size(handle)
        if size <= 0 or size > min(MAX_FILE_BYTES, max_payload_bytes):
            raise RuntimeError(f"File size outside allowed remaining payload budget: {size}")
        if info.ContentSize not in (0, size):
            raise RuntimeError(f"Content size mismatch: info={info.ContentSize}, file-size={size}")
        alias_open_evidence = None
        if alias_verified:
            if alias is not None:
                if (
                    reported_file_id != alias["reported_file_data_id"]
                    or bytes(info.CKey).hex() != alias["CKey"].lower()
                    or size != alias["bytes"]
                ):
                    raise RuntimeError(f"FileDataID mismatch: requested {file_id}, returned {reported_file_id}")
            else:
                alias_handle = api.open_file(storage, reported_file_id)
                try:
                    alias_info = api.file_info(alias_handle)
                    alias_size = api.file_size(alias_handle)
                    if (
                        int(alias_info.FileDataId) not in (reported_file_id, 0xFFFFFFFF)
                        or alias_size != size
                        or alias_info.ContentSize not in (0, alias_size)
                        or bytes(alias_info.CKey) != bytes(info.CKey)
                    ):
                        raise RuntimeError(f"Strict alias open did not match requested FileDataID {file_id}")
                    alias_payload, alias_read = api.read_file_bytes(alias_handle, alias_size)
                    if alias_read["content_md5"] != bytes(alias_info.CKey).hex():
                        raise RuntimeError(f"Alias CKey integrity mismatch for FileDataID {reported_file_id}")
                    alias_open_evidence = {
                        "status": "strict-open-read",
                        "requested_file_data_id": reported_file_id,
                        "reported_file_data_id": int(alias_info.FileDataId),
                        "strict_open_flags": OPEN_BY_FILEID | STRICT_DATA_CHECK,
                        "bytes": alias_read["bytes"],
                        "sha256": alias_read["sha256"],
                        "content_md5": alias_read["content_md5"],
                        "CKey": bytes(alias_info.CKey).hex(),
                    }
                    del alias_payload
                finally:
                    api.close_file(alias_handle)
        read = api.read_file(handle, size, temporary)
        if read["content_md5"] != bytes(info.CKey).hex():
            raise RuntimeError(f"CKey integrity mismatch for FileDataID {file_id}")
        if alias_verified:
            if alias is not None and read["sha256"] != alias["sha256"].lower():
                raise RuntimeError(f"Aliased FileDataID content differs from its retained proof: {file_id}")
            if alias_open_evidence is not None and (
                read["sha256"] != alias_open_evidence["sha256"]
                or read["content_md5"] != alias_open_evidence["content_md5"]
            ):
                raise RuntimeError(f"Strict alias-open content differs from requested FileDataID {file_id}")
        format_info = inspect_asset_bytes(temporary, suffix)
        os.rename(temporary, output_path)
        return {
            "name": name,
            "file_data_id": file_id,
            "requested_file_data_id": file_id,
            "reported_file_data_id": reported_file_id,
            "file_data_id_alias_verified": alias_verified,
            "file_data_id_alias_evidence": alias["verification_record"] if alias_verified and alias is not None else alias_open_evidence,
            "path": str(output_path),
            "bytes": read["bytes"],
            "sha256": read["sha256"],
            "content_md5": read["content_md5"],
            "CKey": bytes(info.CKey).hex(),
            "EKey": bytes(info.EKey).hex(),
            "content_size": int(info.ContentSize),
            "encoded_size": int(info.EncodedSize),
            "span_count": int(info.SpanCount),
            "format": format_info,
        }
    finally:
        if temporary.exists():
            temporary.unlink()
        api.close_file(handle)


def unique_run_directory() -> tuple[str, Path]:
    run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:12]
    output = RUNTIME_ROOT / "extracted" / run_id
    output.mkdir(parents=True, exist_ok=False)
    return run_id, output


def run_extract_item_closure(args: argparse.Namespace) -> int:
    if not args.allow_client_read:
        raise RuntimeError("Refusing client access without the explicit --allow-client-read gate")
    if args.profile != "retail":
        raise RuntimeError("extract-item-closure currently supports only the pinned Retail profile")
    activate_client_profile(args.profile)
    layout = verify_runtime_shape()
    manifest = load_source_manifest()
    inventory = load_inventory()
    candidates = CLIENT_PROFILES[args.profile]["model_candidates"]
    candidate_ids = {item["file_data_id"] for item in candidates}
    candidate_names = resolve_listfile_ids(candidate_ids, manifest)
    for candidate in candidates:
        if not any(name.casefold() == candidate["name"].casefold() for name in candidate_names[candidate["file_data_id"]]):
            raise RuntimeError(f"Pinned listfile does not map FileDataID {candidate['file_data_id']} to {candidate['name']}")

    events: list[dict] = []
    callback = create_progress_callback(events)
    run_id, output_dir = unique_run_directory()
    log_path = RUNTIME_ROOT / f"item-closure-{run_id}.json"
    record: dict = {
        "schema_version": 1,
        "run_id": run_id,
        "started_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "status": "started",
        "profile": args.profile,
        "source_commit": manifest["commit"],
        "source_archive_sha256": manifest["archive_sha256"],
        "dll_path": str(DLL_PATH),
        "abi_layout": layout,
        "storage": {
            "path": STORAGE_BUILD_INFO,
            "product": PRODUCT_CODE,
            "build_key": BUILD_KEY,
            "version": EXPECTED_VERSION,
            "locale_mask": EXPECTED_LOCALE_MASK,
            "root_key": inventory["selected_build_config"]["relevant_keys"]["root"],
            "encoding_keys": inventory["selected_build_config"]["relevant_keys"]["encoding"],
        },
        "candidate_attempts": [],
        "output_directory": str(output_dir),
        "total_payload_limit_bytes": MAX_ITEM_CLOSURE_BYTES,
        "progress_download_events": events,
        "assets": [],
    }
    api = None
    storage = ctypes.c_void_p()
    try:
        record["input_metadata_before"] = snapshot_input_metadata(inventory)
        api = CascApi(DLL_PATH)
        record["dll_sha256"] = api.dll_sha256
        record["bound_exports"] = api.bound_exports
        storage = api.open_storage(callback)
        record["storage_identity"] = verify_storage_identity(api, storage)
        if events:
            raise RuntimeError("CascLib reported a download progress event; storage open was cancelled")

        model_asset = None
        selected_candidate = None
        for candidate in candidates:
            item = {"file_data_id": candidate["file_data_id"], "name": candidate["name"]}
            try:
                model_asset = extract_asset(api, storage, item, output_dir / f"model-{item['file_data_id']}.m2", MAX_ITEM_CLOSURE_BYTES)
                if events:
                    raise RuntimeError("CascLib reported a download progress event during model extraction")
                selected_candidate = candidate
                record["candidate_attempts"].append({"file_data_id": item["file_data_id"], "name": item["name"], "result": "opened-and-verified"})
                break
            except CascError as exc:
                absent_from_root = exc.error_code == 2 and exc.operation == f"CascOpenFile(FileDataID={item['file_data_id']})"
                record["candidate_attempts"].append({
                    "file_data_id": item["file_data_id"],
                    "name": item["name"],
                    "result": "absent-from-local-root" if absent_from_root else "open-failed",
                    "error_code": exc.error_code,
                    "operation": exc.operation,
                })
                if absent_from_root and candidate is candidates[0]:
                    continue
                raise
            if events:
                raise RuntimeError("CascLib reported a download progress event during model extraction")
        if model_asset is None or selected_candidate is None:
            raise RuntimeError("Neither pinned Retail item model candidate exists in the local root")
        record["assets"].append({"role": "model", **model_asset})
        total_bytes = model_asset["bytes"]

        with output_dir.joinpath(f"model-{selected_candidate['file_data_id']}.m2").open("rb") as stream:
            with mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as model_payload:
                parsed = parse_chunked_m2_texture_refs(model_payload)
                core_start, core_end = parsed["core_range"]
                parsed["header_counts"] = parse_modern_m2_header(
                    memoryview(model_payload)[core_start:core_end], parsed["md20_version"]
                )
                parsed["texture_slots"] = parse_modern_texture_slots(
                    memoryview(model_payload)[core_start:core_end]
                )
        texture_refs = parsed["texture_references"]
        skin_refs = parsed["skin_file_ids"]
        first_skin = next((entry for entry in skin_refs if entry["file_data_id"]), None)
        if first_skin is None:
            raise RuntimeError("The model has no nonzero SFID skin FileDataID")
        texture_ids = []
        seen_texture_ids: set[int] = set()
        for reference in texture_refs:
            file_id = reference["file_data_id"]
            if file_id and file_id not in seen_texture_ids:
                seen_texture_ids.add(file_id)
                texture_ids.append(file_id)
        if not parsed["txid_present"]:
            raise RuntimeError("The model has no TXID chunk containing direct texture references")

        resolved_ids = resolve_listfile_ids({first_skin["file_data_id"], *texture_ids}, manifest)
        skin_names = [name for name in resolved_ids[first_skin["file_data_id"]] if name.lower().endswith(".skin")]
        skin_name = skin_names[0] if skin_names else f"FileDataID-{first_skin['file_data_id']}.skin"
        skin_item = {"file_data_id": first_skin["file_data_id"], "name": skin_name}
        skin_asset = extract_asset(
            api, storage, skin_item,
            output_dir / f"skin-{skin_item['file_data_id']}.skin",
            MAX_ITEM_CLOSURE_BYTES - total_bytes,
        )
        if events:
            raise RuntimeError("CascLib reported a download progress event during skin extraction")
        skin_payload = (output_dir / f"skin-{skin_item['file_data_id']}.skin").read_bytes()
        skin_asset["skin_profile"] = parse_skin_metadata(skin_payload, parsed["md20_version"])
        del skin_payload
        record["assets"].append({"role": "first-nonzero-SFID-skin", **skin_asset})
        total_bytes += skin_asset["bytes"]

        for file_id in texture_ids:
            names = resolved_ids[file_id]
            blp_names = [name for name in names if name.lower().endswith(".blp")]
            texture_name = blp_names[0] if blp_names else f"FileDataID-{file_id}.blp"
            texture_asset = extract_asset(
                api, storage,
                {"file_data_id": file_id, "name": texture_name},
                output_dir / f"texture-{file_id}.blp",
                MAX_ITEM_CLOSURE_BYTES - total_bytes,
            )
            if events:
                raise RuntimeError(f"CascLib reported a download progress event during TXID {file_id} extraction")
            record["assets"].append({"role": "direct-TXID-BLP", **texture_asset})
            total_bytes += texture_asset["bytes"]
            if total_bytes > MAX_ITEM_CLOSURE_BYTES:
                raise RuntimeError("Combined item closure exceeds 128 MiB")

        selected_variant = None
        if args.kultiras_silver:
            # Type 2 is supplied by ItemDisplayInfo, not by TXID. This explicitly
            # chooses a named cosmetic variant; it does not recover an item-ID mapping.
            if selected_candidate["file_data_id"] != 1693135 or parsed["texture_slots"] != [
                {"index": 0, "texture_type": 2}, {"index": 1, "texture_type": 0}
            ]:
                raise RuntimeError("Silver variant requires the pinned Kultiras model texture layout")
            file_id = 1733940
            name = "item/objectcomponents/weapon/mace_1h_kultirasquest_b_01silver.blp"
            names = resolve_listfile_ids({file_id}, manifest)[file_id]
            if not any(value.casefold() == name.casefold() for value in names):
                raise RuntimeError("Pinned listfile does not match the selected silver texture")
            variant_asset = extract_asset(
                api, storage, {"file_data_id": file_id, "name": name},
                output_dir / f"texture-{file_id}.blp", MAX_ITEM_CLOSURE_BYTES - total_bytes,
            )
            if events:
                raise RuntimeError("Download event during selected replaceable-texture extraction")
            record["assets"].append({"role": "selected-type2-variant-BLP", **variant_asset})
            total_bytes += variant_asset["bytes"]
            selected_variant = {
                "texture_index": 0, "texture_type": 2, "file_data_id": file_id,
                "selection_basis": "explicit named silver cosmetic variant; item display mapping unverified",
            }

        verified_references = [
            verify_texture_reference(
                output_dir / f"model-{selected_candidate['file_data_id']}.m2",
                model_asset["sha256"],
                entry["file_data_id"],
                entry,
            )
            for entry in texture_refs if entry["file_data_id"]
        ]
        record["model"] = {
            "file_data_id": selected_candidate["file_data_id"],
            "name": selected_candidate["name"],
            "format": "MD21",
            "md20_version": parsed["md20_version"],
            "header_counts": parsed["header_counts"],
            "sfid_present": parsed["sfid_present"],
            "skin_file_ids": skin_refs,
            "first_nonzero_skin_file_data_id": first_skin["file_data_id"],
            "txid_present": parsed["txid_present"],
            "texture_references": texture_refs,
            "verified_texture_references": verified_references,
            "unique_nonzero_txid_ids": texture_ids,
            "texture_slots": parsed["texture_slots"],
            "selected_replaceable_variant": selected_variant,
            "unresolved_replaceable_texture_slots": [
                slot for slot in parsed["texture_slots"]
                if slot["texture_type"] != 0
                and not (selected_variant and slot["index"] == selected_variant["texture_index"])
            ],
        }
        record["total_payload_bytes"] = total_bytes
        record["status"] = "success"
    except Exception as exc:
        record["status"] = "failed"
        record["error"] = {"type": type(exc).__name__, "message": str(exc)}
        if isinstance(exc, CascError):
            record["error"]["casc_error_code"] = exc.error_code
            record["error"]["operation"] = exc.operation
    finally:
        if api is not None and storage.value:
            try:
                api.close_storage(storage)
            except Exception as exc:
                record.setdefault("cleanup_errors", []).append(str(exc))
        if events and record.get("status") == "success":
            record["status"] = "failed"
            record.setdefault("error", {"type": "DownloadEvent", "message": "CascLib emitted a download progress event"})
        try:
            record["input_metadata_after"] = snapshot_input_metadata(inventory)
            record["metadata_unchanged"] = record.get("input_metadata_before") == record.get("input_metadata_after")
            if not record["metadata_unchanged"]:
                record["status"] = "failed"
                record.setdefault("error", {"type": "MetadataChanged", "message": "Input metadata changed during the run"})
        except Exception as exc:
            record["metadata_after_error"] = str(exc)
            record["metadata_unchanged"] = False
            record["status"] = "failed"
        record["finished_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
        with log_path.open("x", encoding="utf-8", newline="\n") as log:
            log.write(json.dumps(record, indent=2) + "\n")
    print(f"{record['status']}: {log_path}")
    if record.get("error"):
        print(record["error"]["message"], file=sys.stderr)
    return 0 if record["status"] == "success" else 1


def run_extract(args: argparse.Namespace) -> int:
    if not args.allow_client_read:
        raise RuntimeError("Refusing client access without the explicit --allow-client-read gate")
    layout = verify_runtime_shape()
    manifest = load_source_manifest()
    inventory = load_inventory()
    asset_map = load_asset_map()
    events: list[dict] = []
    callback = create_progress_callback(events)
    run_id, output_dir = unique_run_directory()
    log_path = RUNTIME_ROOT / f"run-{run_id}.json"
    record: dict = {
        "schema_version": 1,
        "run_id": run_id,
        "started_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "status": "started",
        "source_commit": manifest["commit"],
        "source_archive_sha256": manifest["archive_sha256"],
        "dll_path": str(DLL_PATH),
        "abi_layout": layout,
        "storage": {
            "path": STORAGE_BUILD_INFO,
            "product": PRODUCT_CODE,
            "build_key": BUILD_KEY,
            "version": EXPECTED_VERSION,
            "locale_mask": EXPECTED_LOCALE_MASK,
            "root_key": inventory["selected_build_config"]["relevant_keys"]["root"],
            "encoding_keys": inventory["selected_build_config"]["relevant_keys"]["encoding"],
        },
        "asset_map": asset_map,
        "output_directory": str(output_dir),
        "bound_exports": [],
        "progress_download_events": events,
        "assets": [],
    }
    api = None
    storage = ctypes.c_void_p()
    try:
        record["input_metadata_before"] = snapshot_input_metadata(inventory)
        api = CascApi(DLL_PATH)
        record["dll_sha256"] = api.dll_sha256
        record["bound_exports"] = api.bound_exports
        storage = api.open_storage(callback)
        record["storage_identity"] = verify_storage_identity(api, storage)
        if events:
            raise RuntimeError("CascLib reported a download progress event; storage open was cancelled")

        model_path = output_dir / f"model-{asset_map['model']['file_data_id']}.m2"
        model = extract_asset(api, storage, asset_map["model"], model_path)
        record["assets"].append(model)
        reference = verify_texture_reference(model_path, model["sha256"], asset_map["texture"]["file_data_id"], asset_map["texture_reference"])
        record["texture_reference"] = reference
        texture_path = output_dir / f"texture-{asset_map['texture']['file_data_id']}.blp"
        texture = extract_asset(api, storage, asset_map["texture"], texture_path)
        record["assets"].append(texture)
        if sum(asset["bytes"] for asset in record["assets"]) > MAX_TOTAL_BYTES:
            raise RuntimeError("Combined output exceeds 256 MiB")
        record["status"] = "success"
        return_code = 0
    except Exception as exc:  # Preserve partial proof and exact error context in the run log.
        record["status"] = "failed"
        record["error"] = {"type": type(exc).__name__, "message": str(exc)}
        if isinstance(exc, CascError):
            record["error"]["casc_error_code"] = exc.error_code
        return_code = 1
    finally:
        if api is not None and storage.value:
            try:
                api.close_storage(storage)
            except Exception as exc:
                record.setdefault("cleanup_errors", []).append(str(exc))
        try:
            record["input_metadata_after"] = snapshot_input_metadata(inventory)
            if record.get("input_metadata_before") != record.get("input_metadata_after"):
                record["metadata_unchanged"] = False
                record["status"] = "failed"
                record.setdefault("error", {"type": "MetadataChanged", "message": "Input metadata changed during the run"})
                return_code = 1
            else:
                record["metadata_unchanged"] = True
        except Exception as exc:
            record["metadata_after_error"] = str(exc)
            record["metadata_unchanged"] = False
            record["status"] = "failed"
            return_code = 1
        record["finished_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
        with log_path.open("x", encoding="utf-8", newline="\n") as log:
            log.write(json.dumps(record, indent=2) + "\n")
    print(f"{record['status']}: {log_path}")
    if record.get("error"):
        print(record["error"]["message"], file=sys.stderr)
    return return_code


def run_resolve_model(args: argparse.Namespace) -> int:
    if not args.allow_client_read:
        raise RuntimeError("Refusing client access without the explicit --allow-client-read gate")
    if ASSET_MAP_PATH.exists():
        raise FileExistsError(f"Refusing to overwrite existing asset map: {ASSET_MAP_PATH}")
    verify_runtime_shape()
    manifest = load_source_manifest()
    inventory = load_inventory()
    run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:12]
    log_path = RUNTIME_ROOT / f"resolver-{run_id}.json"
    events: list[dict] = []
    callback = create_progress_callback(events)
    record: dict = {
        "schema_version": 1,
        "run_id": run_id,
        "started_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "status": "started",
        "source_commit": manifest["commit"],
        "dll_path": str(DLL_PATH),
        "storage": {
            "path": STORAGE_BUILD_INFO,
            "product": PRODUCT_CODE,
            "version": EXPECTED_VERSION,
            "build_key": BUILD_KEY,
            "locale_mask": EXPECTED_LOCALE_MASK,
            "root_keys": inventory["selected_build_config"]["relevant_keys"]["root"],
            "encoding_keys": inventory["selected_build_config"]["relevant_keys"]["encoding"],
        },
        "model_name_candidate": MODEL_NAME,
        "model_file_data_id_candidate": MODEL_FILE_DATA_ID,
        "bound_exports": [],
        "progress_download_events": events,
    }
    api = None
    storage = ctypes.c_void_p()
    file_handle = ctypes.c_void_p()
    model_payload: bytearray | None = None
    asset_map: dict | None = None
    model_only_reason: str | None = None
    try:
        record["input_metadata_before"] = snapshot_input_metadata(inventory)
        api = CascApi(DLL_PATH)
        record["dll_sha256"] = api.dll_sha256
        record["bound_exports"] = api.bound_exports
        storage = api.open_storage(callback)
        record["storage_identity"] = verify_storage_identity(api, storage)
        if events:
            raise RuntimeError("CascLib reported a download progress event; storage open was cancelled")

        file_handle = api.open_file(storage, MODEL_FILE_DATA_ID)
        info = api.file_info(file_handle)
        if info.FileDataId not in (MODEL_FILE_DATA_ID, 0xFFFFFFFF):
            raise RuntimeError(f"FileDataID mismatch: requested {MODEL_FILE_DATA_ID}, returned {info.FileDataId}")
        size = api.file_size(file_handle)
        if size <= 0 or size > MAX_FILE_BYTES:
            raise RuntimeError(f"File size outside allowed range: {size}")
        if info.ContentSize not in (0, size):
            raise RuntimeError(f"Content size mismatch: info={info.ContentSize}, file-size={size}")
        model_payload, read = api.read_file_bytes(file_handle, size)
        content_key = bytes(info.CKey).hex()
        if read["content_md5"] != content_key:
            raise RuntimeError(f"CKey integrity mismatch for FileDataID {MODEL_FILE_DATA_ID}")
        if bytes(model_payload[:4]) != b"MD21":
            raise RuntimeError(f"Model format is not chunked MD21: {bytes(model_payload[:4])!r}")
        parsed = parse_chunked_m2_texture_refs(model_payload)
        texture_refs = parsed["texture_references"]
        name_rows = resolve_listfile_ids(
            {MODEL_FILE_DATA_ID} | {entry["file_data_id"] for entry in texture_refs if entry["file_data_id"]},
            manifest,
        )
        model_names = name_rows.get(MODEL_FILE_DATA_ID, [])
        matching_model_names = [name for name in model_names if name.casefold() == MODEL_NAME.casefold()]
        if not matching_model_names:
            raise RuntimeError(f"Pinned listfile does not map FileDataID {MODEL_FILE_DATA_ID} to {MODEL_NAME}")

        selected_ref = None
        selected_texture_name = None
        for ref in texture_refs:
            file_id = ref["file_data_id"]
            if file_id == 0:
                continue
            blp_names = [name for name in name_rows.get(file_id, []) if name.lower().endswith(".blp")]
            if blp_names:
                selected_ref = ref
                selected_texture_name = blp_names[0]
                break

        format_info = {"magic": "MD21", "md20_version": parsed["md20_version"]}
        record["model"] = {
            "name": matching_model_names[0],
            "file_data_id": MODEL_FILE_DATA_ID,
            "file_info_file_data_id": int(info.FileDataId),
            "bytes": read["bytes"],
            "sha256": read["sha256"],
            "content_md5": read["content_md5"],
            "CKey": content_key,
            "EKey": bytes(info.EKey).hex(),
            "encoded_size": int(info.EncodedSize),
            "span_count": int(info.SpanCount),
            "format": format_info,
        }
        record["m2_texture_references"] = texture_refs
        if selected_ref is None or selected_texture_name is None:
            model_only_reason = "No nonzero TXID texture FileDataID resolved to a BLP name in the pinned listfile."
            record["model_only_reason"] = model_only_reason
            record["status"] = "model-only"
        else:
            archive_entry = next(item for item in manifest["source_files"] if item["path"] == "listfile/listfile-archive.zip")
            asset_map = {
                "schema_version": 1,
                "model": {"file_data_id": MODEL_FILE_DATA_ID, "name": matching_model_names[0]},
                "texture": {"file_data_id": selected_ref["file_data_id"], "name": selected_texture_name},
                "texture_reference": {
                    "encoding": "u32le",
                    "offset": selected_ref["offset"],
                    "file_data_id": selected_ref["file_data_id"],
                    "chunk": "TXID",
                    "index": selected_ref["index"],
                },
                "name_resolution_evidence": {
                    "source": "listfile/listfile-archive.zip!listfile.csv",
                    "archive_sha256": archive_entry["sha256"],
                    "model_rows": [f"{MODEL_FILE_DATA_ID};{name}" for name in model_names],
                    "selected_model_name": matching_model_names[0],
                    "texture_rows": [f"{selected_ref['file_data_id']};{name}" for name in name_rows[selected_ref["file_data_id"]]],
                    "selected_texture_name": selected_texture_name,
                    "build_membership_verification": {
                        "method": "CascOpenFile(FileDataID, locale=2, OPEN_BY_FILEID|STRICT_DATA_CHECK)",
                        "product": PRODUCT_CODE,
                        "version": EXPECTED_VERSION,
                        "build_key": BUILD_KEY,
                    },
                },
            }
            record["selected_texture_reference"] = selected_ref
            record["selected_texture_name"] = selected_texture_name
            record["asset_map_path"] = str(ASSET_MAP_PATH)
            record["status"] = "asset-map-ready"
        if file_handle.value:
            api.close_file(file_handle)
            file_handle = ctypes.c_void_p()
    except Exception as exc:
        record["status"] = "failed"
        record["error"] = {"type": type(exc).__name__, "message": str(exc)}
        if isinstance(exc, CascError):
            record["error"]["casc_error_code"] = exc.error_code
    finally:
        if api is not None and file_handle.value:
            try:
                api.close_file(file_handle)
            except Exception as exc:
                record.setdefault("cleanup_errors", []).append(str(exc))
        if api is not None and storage.value:
            try:
                api.close_storage(storage)
            except Exception as exc:
                record.setdefault("cleanup_errors", []).append(str(exc))
        try:
            record["input_metadata_after"] = snapshot_input_metadata(inventory)
            record["metadata_unchanged"] = record.get("input_metadata_before") == record["input_metadata_after"]
            if not record["metadata_unchanged"]:
                record["status"] = "failed"
                record.setdefault("error", {"type": "MetadataChanged", "message": "Pinned client metadata changed during resolution"})
        except Exception as exc:
            record["metadata_unchanged"] = False
            record["metadata_after_error"] = str(exc)
            record["status"] = "failed"

        if record.get("status") == "asset-map-ready" and asset_map is not None:
            try:
                with ASSET_MAP_PATH.open("x", encoding="utf-8", newline="\n") as output:
                    output.write(json.dumps(asset_map, indent=2) + "\n")
            except Exception as exc:
                record["status"] = "failed"
                record["error"] = {"type": type(exc).__name__, "message": str(exc)}
        elif record.get("status") == "model-only" and model_payload is not None:
            try:
                output_dir = RUNTIME_ROOT / "extracted" / f"model-probe-{run_id}"
                output_dir.mkdir(parents=True, exist_ok=False)
                model_path = output_dir / f"model-{MODEL_FILE_DATA_ID}.m2"
                with model_path.open("xb") as output:
                    output.write(model_payload)
                    output.flush()
                    os.fsync(output.fileno())
                record["model_export_path"] = str(model_path)
            except Exception as exc:
                record["status"] = "failed"
                record["error"] = {"type": type(exc).__name__, "message": str(exc)}
        record["finished_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
        with log_path.open("x", encoding="utf-8", newline="\n") as log:
            log.write(json.dumps(record, indent=2) + "\n")
    print(f"{record['status']}: {log_path}")
    if record.get("error"):
        print(record["error"]["message"], file=sys.stderr)
    return 0 if record["status"] in ("asset-map-ready", "model-only") else 1


def run_self_test() -> int:
    layout = verify_runtime_shape()
    manifest = load_source_manifest()
    allowlist = {
        "CascOpenStorageEx", "CascGetStorageInfo", "CascOpenFile", "CascGetFileInfo",
        "CascGetFileSize64", "CascReadFile", "CascCloseFile", "CascCloseStorage", "GetCascError",
    }
    expected = {"open_storage_args": 88, "storage_product": 32, "file_full_info": 120}
    if layout != expected or len(allowlist) != 9 or OPEN_BY_FILEID | STRICT_DATA_CHECK != 0x13:
        raise RuntimeError("Adapter self-test failed")
    print(json.dumps({"status": "self-test-passed", "abi_layout": layout, "source_commit": manifest["commit"], "bound_export_allowlist": sorted(allowlist), "encrypted_zero_fill_enabled": False}, indent=2))
    return 0


def run_fixture_tests() -> int:
    verify_runtime_shape()
    manifest = load_source_manifest()
    fixture_root = RUNTIME_ROOT / "fixtures"
    fixture_root.mkdir(parents=True, exist_ok=True)
    run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:12]
    root = fixture_root / run_id
    root.mkdir(exist_ok=False)
    cases = [
        ("online-storage", None, 0, True, 5),
        ("force-download-feature", ".build.info", FORCE_DOWNLOAD_FEATURE, False, 5),
        ("versions-build-file", "versions", 0, False, 5),
    ]
    for name, filename, _flags, _online_storage, _expected in cases:
        directory = root / name
        directory.mkdir()
        if filename is not None:
            (directory / filename).write_bytes(b"fixture metadata; deliberately not a valid CASC manifest\n")
    unsupported_dir = root / "unsupported"
    unsupported_dir.mkdir()
    unsupported = unsupported_dir / "unrecognized-storage-marker.bin"
    unsupported.write_bytes(b"unsupported marker\n")
    before = {
        str(path.relative_to(root)).replace("\\", "/"): {"bytes": path.stat().st_size, "sha256": sha256_file(path)}
        for path in root.rglob("*") if path.is_file()
    }
    api = CascApi(DLL_PATH)
    events: list[dict] = []
    callback = create_progress_callback(events)
    results = []
    for name, filename, flags, online_storage, expected_error in cases:
        path = root / name if filename is None else root / name / filename
        try:
            api.open_storage_path(str(path), callback, open_flags=flags, online_storage=online_storage)
            results.append({"case": name, "passed": False, "unexpected": "storage opened"})
        except CascError as exc:
            results.append({"case": name, "passed": exc.error_code == expected_error, "error_code": exc.error_code, "expected_error_code": expected_error})
    try:
        api.open_storage_path(str(unsupported), callback, open_flags=0)
        results.append({"case": "unsupported-build-file", "passed": False, "unexpected": "storage opened"})
    except CascError as exc:
        results.append({"case": "unsupported-build-file", "passed": exc.error_code == 2, "error_code": exc.error_code, "expected_error_code": 2})
    after = {
        str(path.relative_to(root)).replace("\\", "/"): {"bytes": path.stat().st_size, "sha256": sha256_file(path)}
        for path in root.rglob("*") if path.is_file()
    }
    unchanged = before == after
    record = {
        "schema_version": 1,
        "run_id": run_id,
        "source_commit": manifest["commit"],
        "dll_path": str(DLL_PATH.resolve()),
        "dll_sha256": api.dll_sha256,
        "bound_exports": api.bound_exports,
        "fixture_root": str(root),
        "cases": results,
        "fixture_files_unchanged": unchanged,
        "progress_download_events": events,
        "status": "passed" if all(case["passed"] for case in results) and unchanged and not events else "failed",
    }
    log_path = RUNTIME_ROOT / f"fixture-test-{run_id}.json"
    with log_path.open("x", encoding="utf-8", newline="\n") as log:
        log.write(json.dumps(record, indent=2) + "\n")
    print(f"{record['status']}: {log_path}")
    if record["status"] != "passed":
        print(json.dumps(record, indent=2), file=sys.stderr)
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("self-test", help="Verify the pinned source manifest and 64-bit ctypes layouts without loading the DLL")
    commands.add_parser("fixture-test", help="Exercise the hardened local-build rejection paths on synthetic local fixtures")
    resolve = commands.add_parser("resolve-human-hd", help="Resolve and verify HumanMale_HD plus a directly referenced TXID texture in the pinned local build")
    resolve.add_argument("--allow-client-read", action="store_true", help="Explicitly enable read-only access to the audited E: storage")
    extract = commands.add_parser("extract", help="Extract exactly the model and referenced texture in asset-map.json")
    extract.add_argument("--allow-client-read", action="store_true", help="Explicitly enable read-only access to the audited E: storage")
    item_closure = commands.add_parser("extract-item-closure", help="Export one pinned Retail item model, first SFID skin, and its direct TXID BLP dependencies")
    item_closure.add_argument("--profile", choices=("retail",), default="retail", help="Pinned client profile (Retail only)")
    item_closure.add_argument("--allow-client-read", action="store_true", help="Explicitly enable read-only access to the audited E: storage")
    item_closure.add_argument("--kultiras-silver", action="store_true", help="Also export the pinned silver cosmetic variant for the Kultiras mace type-2 texture slot")
    args = parser.parse_args()
    if args.command == "self-test":
        return run_self_test()
    if args.command == "fixture-test":
        return run_fixture_tests()
    if args.command == "resolve-human-hd":
        return run_resolve_model(args)
    if args.command == "extract-item-closure":
        return run_extract_item_closure(args)
    return run_extract(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
