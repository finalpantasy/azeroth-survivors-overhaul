#!/usr/bin/env python3
"""Build an isolated vanilla-MPQ cosmetic candidate for native starter mace 5194."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import struct
import subprocess
import sys
from pathlib import Path
from typing import Any


def discover_project_root(script_path: Path) -> Path:
    script_dir = script_path.resolve().parent
    for candidate in (script_dir, *script_dir.parents):
        if (candidate / "analysis").is_dir() and (candidate / "tools").is_dir():
            return candidate
    return script_dir


ROOT = discover_project_root(Path(__file__))
EVIDENCE_DIR = ROOT / "analysis/retail-item-equip/native-binding"
DEFAULT_OUTPUT_DIR = EVIDENCE_DIR / "candidate-5194-silver-mace-basename"
OUTFIT_PATH = EVIDENCE_DIR / "CharStartOutfit.dbc"
DISPLAY_PATH = EVIDENCE_DIR / "ItemDisplayInfo.dbc"
MACE_SOURCE = (
    ROOT
    / "analysis/retail-rigid-preview/output-attachments/payload/Item/ObjectComponents/Weapon"
    / "KultirasQuestB01_Silver_Static.m2"
)
TEXTURE_SOURCE = (
    ROOT
    / "analysis/retail-rigid-preview/output-attachments/payload/Item/ObjectComponents/Weapon"
    / "KultirasQuestB01_Silver_Static.blp"
)
MPQ_PACKER = ROOT / "tools/pack-mpq.py"
NATIVE_INSPECTOR = ROOT / "tools/mpq-inspect.exe"

TARGET_DISPLAY_ID = 5194
SHAMAN_CLASS_ID = 7
STARTER_ITEM_ID = 36
MAIN_HAND_INVENTORY_TYPE = 21
NEW_MODEL_DBC_PATH = "KultirasQuestB01_Silver_Static.mdx"
RUNTIME_WEAPON_DIRECTORY = r"Item\ObjectComponents\Weapon"
MODEL_INTERNAL_PATH = r"Item\ObjectComponents\Weapon\KultirasQuestB01_Silver_Static.m2"
TEXTURE_INTERNAL_PATH = r"Item\ObjectComponents\Weapon\KultirasQuestB01_Silver_Static.blp"
MODEL_PATH_SOURCE_REFERENCES = {
    "dbc_basename_contract": "../_old-version-inactive/crates/benilla-formats/src/items.rs:19-25",
    "dbc_parse_and_normalize": "../_old-version-inactive/crates/benilla-formats/src/items.rs:149-150",
    "mdx_to_m2_normalization": "../_old-version-inactive/crates/benilla-formats/src/models/mod.rs:108-116",
    "weapon_directory_join": "../_old-version-inactive/crates/benilla-app/src/entities/equipment/mod.rs:256,274",
}
EXPECTED_MODEL_SHA256 = "b69b55d092912005907d9190a3616160d0b2e4cf845d8eb8860354eefab8004d"
EXPECTED_TEXTURE_SHA256 = "9c2f5b24dfe99d8ee7bc0233c825e0ae4f32abed04ca09ee9865b46970b0462b"

# In this native CharStartOutfit table, the four byte fields follow the row ID;
# each of the three 12-entry arrays then stores a u32 per slot.
CHAR_START_ROW_FORMAT = "<I4B36I"
CHAR_START_FIELD_COUNT = 41
CHAR_START_RECORD_SIZE = struct.calcsize(CHAR_START_ROW_FORMAT)
ITEM_DISPLAY_FIELD_COUNT = 23
ITEM_DISPLAY_RECORD_SIZE = ITEM_DISPLAY_FIELD_COUNT * 4


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def read_wdbc_header(data: bytes, table_name: str) -> tuple[int, int, int, int]:
    if len(data) < 20:
        raise ValueError(f"{table_name}: truncated WDBC header")
    magic, record_count, field_count, record_size, string_size = struct.unpack_from("<4s4I", data)
    if magic != b"WDBC":
        raise ValueError(f"{table_name}: expected WDBC, received {magic!r}")
    if 20 + record_count * record_size + string_size != len(data):
        raise ValueError(f"{table_name}: header sizes do not match file length")
    return record_count, field_count, record_size, string_size


def parse_char_start_outfit(data: bytes) -> list[tuple[int, ...]]:
    record_count, field_count, record_size, _ = read_wdbc_header(data, "CharStartOutfit.dbc")
    if (field_count, record_size) != (CHAR_START_FIELD_COUNT, CHAR_START_RECORD_SIZE):
        raise ValueError(
            "CharStartOutfit.dbc schema mismatch: "
            f"expected {CHAR_START_FIELD_COUNT} fields/{CHAR_START_RECORD_SIZE} bytes, "
            f"received {field_count} fields/{record_size} bytes"
        )
    return [
        struct.unpack_from(CHAR_START_ROW_FORMAT, data, 20 + i * record_size)
        for i in range(record_count)
    ]


def char_start_display_references(data: bytes, display_id: int) -> list[dict[str, int]]:
    references: list[dict[str, int]] = []
    for row in parse_char_start_outfit(data):
        row_id, race_id, class_id, sex_id, outfit_id = row[:5]
        item_ids = row[5:17]
        display_ids = row[17:29]
        inventory_types = row[29:41]
        for slot_index, found_display_id in enumerate(display_ids):
            if found_display_id != display_id:
                continue
            references.append(
                {
                    "row_id": row_id,
                    "race_id": race_id,
                    "class_id": class_id,
                    "sex_id": sex_id,
                    "outfit_id": outfit_id,
                    "slot_index": slot_index,
                    "item_id": item_ids[slot_index],
                    "display_id": found_display_id,
                    "inventory_type": inventory_types[slot_index],
                }
            )
    return references


def parse_item_display_rows(data: bytes) -> tuple[list[tuple[int, ...]], int, int]:
    record_count, field_count, record_size, string_size = read_wdbc_header(
        data, "ItemDisplayInfo.dbc"
    )
    if (field_count, record_size) != (ITEM_DISPLAY_FIELD_COUNT, ITEM_DISPLAY_RECORD_SIZE):
        raise ValueError(
            "ItemDisplayInfo.dbc schema mismatch: "
            f"expected {ITEM_DISPLAY_FIELD_COUNT} fields/{ITEM_DISPLAY_RECORD_SIZE} bytes, "
            f"received {field_count} fields/{record_size} bytes"
        )
    records = [
        struct.unpack_from("<23I", data, 20 + i * record_size)
        for i in range(record_count)
    ]
    return records, 20 + record_count * record_size, string_size


def dbc_string(data: bytes, string_block_start: int, string_block_size: int, offset: int) -> str:
    if offset >= string_block_size:
        raise ValueError(f"DBC string offset {offset} is outside its string block")
    start = string_block_start + offset
    end_limit = string_block_start + string_block_size
    end = data.find(b"\0", start, end_limit)
    if end < 0:
        raise ValueError(f"DBC string at offset {offset} is not NUL-terminated")
    return data[start:end].decode("ascii")


def item_display_record(data: bytes, display_id: int) -> tuple[int, tuple[int, ...], int, int]:
    records, strings_start, string_size = parse_item_display_rows(data)
    matches = [(i, row) for i, row in enumerate(records) if row[0] == display_id]
    if len(matches) != 1:
        raise ValueError(f"expected one ItemDisplayInfo row {display_id}; found {len(matches)}")
    record_index, row = matches[0]
    return record_index, row, strings_start, string_size


def item_display_model_name(data: bytes, display_id: int) -> str:
    _, row, strings_start, string_size = item_display_record(data, display_id)
    return dbc_string(data, strings_start, string_size, row[1])


def resolve_runtime_weapon_model_path(model_name: str) -> str:
    """Apply the pinned client model basename normalization and weapon-directory join."""
    if not model_name or "\\" in model_name or "/" in model_name:
        raise ValueError("ItemDisplayInfo model name must be a nonempty basename")
    normalized = model_name.lower()
    if normalized.endswith(".mdx") or normalized.endswith(".mdl"):
        normalized = normalized.rsplit(".", 1)[0] + ".m2"
    return f"{RUNTIME_WEAPON_DIRECTORY}\\{normalized}"


def patch_item_display_info(data: bytes, display_id: int, model_path: str) -> bytes:
    if not model_path or "\0" in model_path:
        raise ValueError("replacement model path must be nonempty and contain no NUL")
    encoded_path = model_path.encode("ascii") + b"\0"
    record_index, row, strings_start, string_size = item_display_record(data, display_id)
    existing_model = dbc_string(data, strings_start, string_size, row[1])
    if existing_model != "Mace_1H_Flanged_A_01.mdx":
        raise ValueError(
            f"ItemDisplayInfo {display_id} no longer has the expected native model: {existing_model!r}"
        )

    record_count, _, record_size, _ = read_wdbc_header(data, "ItemDisplayInfo.dbc")
    if record_index >= record_count:
        raise ValueError("ItemDisplayInfo record index is outside the table")
    patched = bytearray(data)
    replacement_offset = string_size
    struct.pack_into("<I", patched, 20 + record_index * record_size + 4, replacement_offset)
    struct.pack_into("<I", patched, 16, string_size + len(encoded_path))
    patched.extend(encoded_path)
    return bytes(patched)


def validate_native_binding(outfit_data: bytes, display_data: bytes) -> dict[str, Any]:
    outfits = parse_char_start_outfit(outfit_data)
    shaman_rows = [row for row in outfits if row[2] == SHAMAN_CLASS_ID]
    shaman_bindings: list[dict[str, int]] = []
    for row in shaman_rows:
        matches = [
            i
            for i in range(12)
            if row[5 + i] == STARTER_ITEM_ID
            and row[17 + i] == TARGET_DISPLAY_ID
            and row[29 + i] == MAIN_HAND_INVENTORY_TYPE
        ]
        if len(matches) != 1:
            raise ValueError(
                f"Shaman outfit row {row[0]} must have one starter main-hand binding; found {len(matches)}"
            )
        slot_index = matches[0]
        shaman_bindings.append(
            {
                "row_id": row[0],
                "race_id": row[1],
                "class_id": row[2],
                "sex_id": row[3],
                "outfit_id": row[4],
                "slot_index": slot_index,
                "item_id": row[5 + slot_index],
                "display_id": row[17 + slot_index],
                "inventory_type": row[29 + slot_index],
            }
        )
    if len(shaman_bindings) != 6:
        raise ValueError(f"expected six race/sex Shaman starter rows; found {len(shaman_bindings)}")

    display_references = char_start_display_references(outfit_data, TARGET_DISPLAY_ID)
    if len(display_references) != 16 or any(
        reference["item_id"] != STARTER_ITEM_ID
        or reference["inventory_type"] != MAIN_HAND_INVENTORY_TYPE
        for reference in display_references
    ):
        raise ValueError("display 5194 starter outfit reference scope changed")

    native_model = item_display_model_name(display_data, TARGET_DISPLAY_ID)
    if native_model != "Mace_1H_Flanged_A_01.mdx":
        raise ValueError(f"unexpected native display model for {TARGET_DISPLAY_ID}: {native_model!r}")

    return {
        "binding": {
            "source_table": "DBFilesClient\\CharStartOutfit.dbc",
            "class_id": SHAMAN_CLASS_ID,
            "item_id": STARTER_ITEM_ID,
            "display_id": TARGET_DISPLAY_ID,
            "inventory_type": MAIN_HAND_INVENTORY_TYPE,
            "rows": shaman_bindings,
        },
        "display_scope": {
            "source_table": "DBFilesClient\\CharStartOutfit.dbc",
            "reference_count": len(display_references),
            "references": display_references,
            "broader_item_or_npc_scope": (
                "Item.dbc and CreatureEquipment.dbc are absent from the packaged client asset "
                "inventory, so references outside CharStartOutfit and NPC equipment usage "
                "cannot be enumerated from these local client files."
            ),
        },
        "native_display": {
            "table": "DBFilesClient\\ItemDisplayInfo.dbc",
            "display_id": TARGET_DISPLAY_ID,
            "model_name_left": native_model,
        },
        "semantic_key": {
            "key": "weapon.shaman.0.0",
            "label": "Spiked Club",
            "binding_status": "not proven by the inspected native tables; no item name table is present",
        },
    }


def run_native(inspector: Path, *args: str) -> str:
    result = subprocess.run(
        [str(inspector), *args],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout + result.stderr


def write_and_verify_candidate(output_dir: Path = DEFAULT_OUTPUT_DIR) -> Path:
    output_dir = output_dir.resolve()
    try:
        output_dir.relative_to(EVIDENCE_DIR.resolve())
    except ValueError as exc:
        raise ValueError("candidate output must stay inside the native-binding evidence directory") from exc
    if output_dir.exists():
        raise FileExistsError(f"candidate output already exists: {output_dir}")
    for required in (OUTFIT_PATH, DISPLAY_PATH, MACE_SOURCE, TEXTURE_SOURCE, MPQ_PACKER, NATIVE_INSPECTOR):
        if not required.is_file():
            raise FileNotFoundError(required)

    outfit_data = OUTFIT_PATH.read_bytes()
    display_data = DISPLAY_PATH.read_bytes()
    evidence = validate_native_binding(outfit_data, display_data)
    resolved_runtime_model_path = resolve_runtime_weapon_model_path(NEW_MODEL_DBC_PATH)
    if resolved_runtime_model_path.casefold() != MODEL_INTERNAL_PATH.casefold():
        raise ValueError(
            "normalized ItemDisplayInfo weapon path does not resolve to the candidate MPQ model"
        )
    model_data = MACE_SOURCE.read_bytes()
    texture_data = TEXTURE_SOURCE.read_bytes()
    if sha256(model_data) != EXPECTED_MODEL_SHA256:
        raise ValueError("converted mace model SHA-256 does not match the preverified asset")
    if sha256(texture_data) != EXPECTED_TEXTURE_SHA256:
        raise ValueError("converted mace texture SHA-256 does not match the preverified asset")

    patched_display = patch_item_display_info(display_data, TARGET_DISPLAY_ID, NEW_MODEL_DBC_PATH)
    output_dir.mkdir(parents=True)
    payload_root = output_dir / "payload"
    model_path = payload_root / Path(MODEL_INTERNAL_PATH.replace("\\", "/"))
    texture_path = payload_root / Path(TEXTURE_INTERNAL_PATH.replace("\\", "/"))
    display_path = payload_root / "DBFilesClient/ItemDisplayInfo.dbc"
    for path in (model_path, texture_path, display_path):
        path.parent.mkdir(parents=True, exist_ok=True)
    model_path.write_bytes(model_data)
    texture_path.write_bytes(texture_data)
    display_path.write_bytes(patched_display)

    archive_path = output_dir / "NativeStarterMace_5194_Silver_Candidate.mpq"
    pack_result = subprocess.run(
        [sys.executable, str(MPQ_PACKER), str(payload_root), str(archive_path)],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )

    readback_root = output_dir / "readback"
    readback_root.mkdir()
    db_readback = readback_root / "ItemDisplayInfo.dbc"
    csv_readback = readback_root / "ItemDisplayInfo.csv"
    model_readback = readback_root / Path(MODEL_INTERNAL_PATH.replace("\\", "/"))
    texture_readback = readback_root / Path(TEXTURE_INTERNAL_PATH.replace("\\", "/"))
    for path in (db_readback, model_readback, texture_readback):
        path.parent.mkdir(parents=True, exist_ok=True)
    (readback_root / "mpq-list.txt").write_text(
        run_native(NATIVE_INSPECTOR, str(archive_path), "list"), encoding="utf-8"
    )
    (readback_root / "extract-dbc.txt").write_text(
        run_native(
            NATIVE_INSPECTOR,
            str(archive_path),
            "extract",
            r"DBFilesClient\ItemDisplayInfo.dbc",
            str(db_readback),
        ),
        encoding="utf-8",
    )
    (readback_root / "dump-display-row.txt").write_text(
        run_native(
            NATIVE_INSPECTOR,
            str(archive_path),
            "dbc",
            r"DBFilesClient\ItemDisplayInfo.dbc",
            str(csv_readback),
        ),
        encoding="utf-8",
    )
    (readback_root / "extract-model.txt").write_text(
        run_native(
            NATIVE_INSPECTOR,
            str(archive_path),
            "extract",
            MODEL_INTERNAL_PATH,
            str(model_readback),
        ),
        encoding="utf-8",
    )
    (readback_root / "extract-texture.txt").write_text(
        run_native(
            NATIVE_INSPECTOR,
            str(archive_path),
            "extract",
            TEXTURE_INTERNAL_PATH,
            str(texture_readback),
        ),
        encoding="utf-8",
    )
    (readback_root / "m2anim.txt").write_text(
        run_native(NATIVE_INSPECTOR, str(archive_path), "m2anim", MODEL_INTERNAL_PATH),
        encoding="utf-8",
    )

    if db_readback.read_bytes() != patched_display:
        raise ValueError("native readback ItemDisplayInfo.dbc differs from the candidate payload")
    if sha256(model_readback.read_bytes()) != EXPECTED_MODEL_SHA256:
        raise ValueError("native readback model SHA-256 differs from the preverified mace")
    if sha256(texture_readback.read_bytes()) != EXPECTED_TEXTURE_SHA256:
        raise ValueError("native readback texture SHA-256 differs from the preverified texture")
    with csv_readback.open("r", encoding="utf-8", newline="") as stream:
        rows = csv.DictReader(stream)
        native_row = next((row for row in rows if int(row["ID"]) == TARGET_DISPLAY_ID), None)
    if native_row is None or native_row["ModelNameLeft"] != NEW_MODEL_DBC_PATH:
        raise ValueError("native CSV readback did not resolve display 5194 to the candidate model path")

    report = {
        "candidate_mpq": str(archive_path.relative_to(ROOT)).replace("\\", "/"),
        "candidate_scope": "ItemDisplayInfo.dbc row 5194 ModelNameLeft only; all item/stat tables omitted",
        "source_hashes": {
            "CharStartOutfit.dbc": sha256(outfit_data),
            "ItemDisplayInfo.dbc": sha256(display_data),
            "converted_model.m2": EXPECTED_MODEL_SHA256,
            "converted_texture.blp": EXPECTED_TEXTURE_SHA256,
        },
        "candidate_model_path_in_display_row": NEW_MODEL_DBC_PATH,
        "candidate_payload_model_path": MODEL_INTERNAL_PATH,
        "candidate_payload_texture_path": TEXTURE_INTERNAL_PATH,
        "path_resolution": {
            "source_references": MODEL_PATH_SOURCE_REFERENCES,
            "dbc_model_name_is_basename": True,
            "normalized_runtime_model_path": resolved_runtime_model_path,
            "candidate_payload_model_path": MODEL_INTERNAL_PATH,
            "resolved_path_matches_payload_case_insensitively": True,
        },
        "candidate_hashes": {
            "ItemDisplayInfo.dbc": sha256(patched_display),
            "model.m2": sha256(model_readback.read_bytes()),
            "texture.blp": sha256(texture_readback.read_bytes()),
        },
        "native_readback": {
            "dbc_bytes_match_payload": True,
            "display_row_model_name_left": native_row["ModelNameLeft"],
            "model_sha256": sha256(model_readback.read_bytes()),
            "texture_sha256": sha256(texture_readback.read_bytes()),
            "model_structure": "read through mpq-inspect m2anim; see readback/m2anim.txt",
            "pack_log": pack_result.stdout.strip(),
        },
        **evidence,
    }
    (output_dir / "binding-report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    (readback_root / "pack-mpq.txt").write_text(
        pack_result.stdout + pack_result.stderr, encoding="utf-8"
    )
    return archive_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="new candidate output directory under analysis/retail-item-equip/native-binding",
    )
    args = parser.parse_args()
    try:
        archive = write_and_verify_candidate(args.output_dir)
    except (OSError, ValueError, subprocess.CalledProcessError, struct.error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"created and native-read back {archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
