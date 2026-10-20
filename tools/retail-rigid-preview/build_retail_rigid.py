#!/usr/bin/env python3
"""Build one pinned, static Vanilla-M2 preview from the verified retail mace closure.

This is deliberately not a general modern-M2 converter. It accepts only the one
retail item closure named below, validates its exact layout, and writes a new
isolated payload/MPQ. Existing output directories are never overwritten.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
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
RUN_ID = "20261019T180333Z-fe59cbbde50e"
MODEL_ID = 1693135
SKIN_ID = 1693139
AUX_TEXTURE_ID = 144333
SILVER_TEXTURE_ID = 1733940
EXPECTED_PREVERIFIED_MODEL_SHA256 = "b69b55d092912005907d9190a3616160d0b2e4cf845d8eb8860354eefab8004d"
MAX_SOURCE_REPORT_BYTES = 1024 * 1024

EXPECTED_FILE_DATA_IDS = {
    "model-1693135.m2": MODEL_ID,
    "skin-1693139.skin": SKIN_ID,
    "texture-144333.blp": AUX_TEXTURE_ID,
    "texture-1733940.blp": SILVER_TEXTURE_ID,
}
PORTABLE_EXPORT_VERSION = "12.1.5.70448"
PORTABLE_EXPORT_TOTAL_BYTES = 194288

EXPECTED_FILE_BYTES = {
    "model-1693135.m2": 83984,
    "skin-1693139.skin": 17808,
    "texture-144333.blp": 3916,
    "texture-1733940.blp": 88580,
}

EXPECTED_SHA256 = {
    "model-1693135.m2": "b8c956910942e5e4cc577ea1ddd92e29bba39ca79a3d65476796a950a3263430",
    "skin-1693139.skin": "a1b1cb5882f84c94a34941153c2181e4950ab6d64794375db6f260e532140a2a",
    "texture-144333.blp": "b1bb3e5b572250e0d5f03a2c5cb8d3c10e25f6ff77fc2e1fe8be8ca6e6c2a835",
    "texture-1733940.blp": "9c2f5b24dfe99d8ee7bc0233c825e0ae4f32abed04ca09ee9865b46970b0462b",
}

MODEL_INTERNAL = r"Item\ObjectComponents\Weapon\KultirasQuestB01_Silver_Static.m2"
TEXTURE_INTERNAL = r"Item\ObjectComponents\Weapon\KultirasQuestB01_Silver_Static.blp"
MODEL_REL = Path("Item/ObjectComponents/Weapon/KultirasQuestB01_Silver_Static.m2")
TEXTURE_REL = Path("Item/ObjectComponents/Weapon/KultirasQuestB01_Silver_Static.blp")

# Retail MD20 v274 header arrays used by this one checked model.
MODERN_ARRAY_OFFSETS = {
    "animations": 0x1C,
    "bones": 0x2C,
    "vertices": 0x3C,
    "colors": 0x48,
    "textures": 0x50,
    "texture_weights": 0x58,
    "texture_animations": 0x60,
    "texture_replacements": 0x68,
    "render_flags": 0x70,
    "bone_lookup": 0x78,
    "texture_lookup": 0x80,
    "texture_unit_lookup": 0x88,
    "transparency_lookup": 0x90,
    "texture_animation_lookup": 0x98,
    "attachments": 0xF0,
    "attachment_lookup": 0xF8,
    "events": 0x100,
    "lights": 0x108,
    "cameras": 0x110,
    "camera_lookup": 0x118,
    "ribbons": 0x120,
    "particles": 0x128,
}

# WoW 1.12.1 pre-Wrath MD20 header arrays.
V256_ARRAY_OFFSETS = {
    "name": 0x08,
    "global_sequences": 0x14,
    "animations": 0x1C,
    "animation_lookup": 0x24,
    "playable_animation_lookup": 0x2C,
    "bones": 0x34,
    "key_bone_lookup": 0x3C,
    "vertices": 0x44,
    "views": 0x4C,
    "colors": 0x54,
    "textures": 0x5C,
    "transparency": 0x64,
    "texture_flipbooks": 0x6C,
    "texture_animations": 0x74,
    "texture_replacements": 0x7C,
    "render_flags": 0x84,
    "bone_lookup": 0x8C,
    "texture_lookup": 0x94,
    "texture_unit_lookup": 0x9C,
    "transparency_lookup": 0xA4,
    "texture_animation_lookup": 0xAC,
    "bounding_triangles": 0xEC,
    "bounding_vertices": 0xF4,
    "bounding_normals": 0xFC,
    "attachments": 0x104,
    "attachment_lookup": 0x10C,
    "events": 0x114,
    "lights": 0x11C,
    "cameras": 0x124,
    "camera_lookup": 0x12C,
    "ribbons": 0x134,
    "particles": 0x13C,
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_bounded_file(path: Path, limit_bytes: int, label: str, *,
                      expected_size: int | None = None) -> bytes:
    size = path.stat().st_size
    if expected_size is not None:
        require(size == expected_size,
                f"{label} size is {size} bytes; expected exactly {expected_size}")
        limit_bytes = expected_size
    else:
        require(size <= limit_bytes,
                f"{label} size is {size} bytes; limit is {limit_bytes}")
    with path.open("rb") as stream:
        payload = stream.read(limit_bytes + 1)
    require(len(payload) <= limit_bytes,
            f"{label} exceeds the {limit_bytes}-byte read limit")
    if expected_size is not None:
        require(len(payload) == expected_size,
                f"{label} read {len(payload)} bytes; expected exactly {expected_size}")
    return payload


def m2_array(buf: bytes, offset: int, label: str) -> tuple[int, int]:
    require(offset + 8 <= len(buf), f"{label} M2Array is outside the header")
    count, start = struct.unpack_from("<II", buf, offset)
    require(count <= 10_000_000, f"{label} count is implausible: {count}")
    if count == 0:
        require(start == 0, f"{label} empty array has nonzero offset {start}")
    else:
        require(start < len(buf), f"{label} begins outside the model")
    return count, start


def bounded_slice(buf: bytes, start: int, count: int, stride: int, label: str) -> bytes:
    require(count >= 0 and stride >= 0, f"{label} has a negative size")
    size = count * stride
    require(start <= len(buf) and size <= len(buf) - start, f"{label} exceeds its payload")
    return buf[start : start + size]


def parse_chunked_model(payload: bytes) -> tuple[bytes, dict[str, Any]]:
    chunks: list[tuple[str, bytes]] = []
    cursor = 0
    while cursor < len(payload):
        require(cursor + 8 <= len(payload), f"Truncated MD21 chunk header at 0x{cursor:x}")
        tag = payload[cursor : cursor + 4].decode("ascii", errors="strict")
        size = struct.unpack_from("<I", payload, cursor + 4)[0]
        end = cursor + 8 + size
        require(end <= len(payload), f"{tag} chunk exceeds the model")
        chunks.append((tag, payload[cursor + 8 : end]))
        cursor = end
    require(cursor == len(payload), "MD21 chunk stream has trailing bytes")
    require([tag for tag, _ in chunks] == ["MD21", "TXAC", "SFID", "TXID"],
            f"Unsupported MD21 chunk sequence: {[tag for tag, _ in chunks]}")
    core = chunks[0][1]
    require(core[:4] == b"MD20" and len(core) == 83936, "Unexpected MD21 core payload")
    version = struct.unpack_from("<I", core, 4)[0]
    require(version == 274, f"Only this pinned v274 core is supported (got {version})")
    require(chunks[1][1] == b"\0\0\0\0", "TXAC must contain exactly zero extended texture animations")
    require(chunks[2][1] == struct.pack("<I", SKIN_ID), "SFID must name only the verified skin")
    require(chunks[3][1] == struct.pack("<II", 0, AUX_TEXTURE_ID),
            "TXID must match the verified direct texture slots [0, 144333]")
    return core, {"chunks": [{"tag": tag, "bytes": len(data)} for tag, data in chunks], "version": version}


def parse_nested_arrays(core: bytes, count: int, offset: int, item_stride: int,
                        label: str) -> list[bytes]:
    require(item_stride > 0, f"{label} has an invalid item stride")
    if count == 0:
        require(offset == 0, f"{label} empty outer array has nonzero offset {offset}")
        return []
    outer = bounded_slice(core, offset, count, 8, f"{label} outer arrays")
    arrays = []
    for index in range(count):
        item_count, item_offset = struct.unpack_from("<II", outer, index * 8)
        require(item_count <= 10_000_000, f"{label}[{index}] count is implausible: {item_count}")
        if item_count == 0:
            require(item_offset == 0, f"{label}[{index}] empty inner array has nonzero offset {item_offset}")
        arrays.append(bounded_slice(core, item_offset, item_count, item_stride,
                                    f"{label}[{index}] data"))
    return arrays


def parse_modern_track(core: bytes, start: int, label: str, *, value_stride: int = 1,
                       record_size: int = 20, expected_sequences: int | None = None) -> dict[str, Any]:
    require(start + record_size <= len(core), f"{label} track is outside MD20")
    interpolation, global_sequence = struct.unpack_from("<Hh", core, start)
    time_sequence_count, time_sequence_offset = m2_array(core, start + 4, f"{label} timestamps")
    value_sequence_count, value_sequence_offset = m2_array(core, start + 12, f"{label} values")
    require(time_sequence_count == value_sequence_count,
            f"{label} nested timestamp/value sequence counts differ")
    if expected_sequences is not None:
        require(time_sequence_count in (0, expected_sequences),
                f"{label} has {time_sequence_count} sequence arrays; expected 0 or {expected_sequences}")

    time_arrays = parse_nested_arrays(core, time_sequence_count, time_sequence_offset, 4,
                                      f"{label} timestamps")
    value_arrays = parse_nested_arrays(core, value_sequence_count, value_sequence_offset, value_stride,
                                       f"{label} values")
    timestamps_by_sequence: list[list[int]] = []
    key_counts: list[int] = []
    for index, (time_data, value_data) in enumerate(zip(time_arrays, value_arrays)):
        time_count = len(time_data) // 4
        value_count = len(value_data) // value_stride
        require(time_count == value_count,
                f"{label} sequence {index} nested timestamp/value key counts differ")
        times = list(struct.unpack(f"<{time_count}I", time_data)) if time_count else []
        timestamps_by_sequence.append(times)
        key_counts.append(time_count)
    return {
        "interpolation": interpolation,
        "global_sequence": global_sequence,
        "outer_sequence_count": time_sequence_count,
        "sequence_key_counts": key_counts,
        "timestamps_by_sequence": timestamps_by_sequence,
        "sequence_value_arrays": value_arrays,
    }


def parse_modern_event_timestamps(core: bytes, start: int, label: str,
                                  expected_sequences: int) -> dict[str, Any]:
    require(start + 12 <= len(core), f"{label} timestamp track is outside MD20")
    interpolation, global_sequence = struct.unpack_from("<Hh", core, start)
    sequence_count, sequence_offset = m2_array(core, start + 4, f"{label} timestamps")
    require(sequence_count in (0, expected_sequences),
            f"{label} has {sequence_count} sequence arrays; expected 0 or {expected_sequences}")
    time_arrays = parse_nested_arrays(core, sequence_count, sequence_offset, 4,
                                      f"{label} timestamps")
    timestamps_by_sequence = [
        list(struct.unpack(f"<{len(data) // 4}I", data)) if data else []
        for data in time_arrays
    ]
    return {
        "interpolation": interpolation,
        "global_sequence": global_sequence,
        "outer_sequence_count": sequence_count,
        "sequence_key_counts": [len(times) for times in timestamps_by_sequence],
        "timestamps_by_sequence": timestamps_by_sequence,
    }


def inspect_inputs(source_dir: Path | None = None) -> tuple[bytes, bytes, bytes, dict[str, Any]]:
    if source_dir is None:
        closure_path = ROOT / "analysis" / "casc-reader-runtime" / f"item-closure-{RUN_ID}.json"
        closure = json.loads(read_bounded_file(
            closure_path, MAX_SOURCE_REPORT_BYTES, "Pinned item closure report"
        ).decode("utf-8"))
        require(closure.get("status") == "success" and closure.get("run_id") == RUN_ID,
                "Pinned item closure did not report success")
        input_dir = ROOT / "analysis" / "casc-reader-runtime" / "extracted" / RUN_ID
        require(Path(closure.get("output_directory", "")).resolve() == input_dir.resolve(),
                "Pinned closure points to a different extraction directory")
        require(closure.get("model", {}).get("md20_version") == 274,
                "Pinned closure is no longer MD20 v274")
        require(closure.get("model", {}).get("selected_replaceable_variant", {}).get("file_data_id") == SILVER_TEXTURE_ID,
                "The explicit silver cosmetic texture selection changed")
        records = {entry["path"].split("\\")[-1]: entry for entry in closure.get("assets", [])}
        require(set(records) >= set(EXPECTED_SHA256),
                "Pinned closure is missing one of the four required local assets")
        source_provenance = {"mode": "pinned-project-closure", "run_id": RUN_ID}
    else:
        input_dir = source_dir.resolve(strict=True)
        require(input_dir.is_dir(), f"Source directory is not a directory: {input_dir}")
        export_report_path = input_dir / "export-report.json"
        export_report = json.loads(read_bounded_file(
            export_report_path, MAX_SOURCE_REPORT_BYTES, "Portable export report"
        ).decode("utf-8"))
        require(export_report.get("schema_version") == 1 and export_report.get("status") == "success" and
                export_report.get("writes") is True,
                "Portable export report did not report a successful write")
        require(export_report.get("product") == "wow" and
                export_report.get("version") == PORTABLE_EXPORT_VERSION,
                "Portable export report is not for the pinned WoW 12.1.5.70448 source")
        require(export_report.get("metadata_unchanged") is True and
                export_report.get("progress_download_events") == [],
                "Portable export report indicates changed metadata or download events")
        assets = export_report.get("assets")
        require(isinstance(assets, list) and len(assets) == 4 and
                export_report.get("asset_count") == 4,
                "Portable export report must contain exactly the four pinned assets")
        records = {entry.get("name"): entry for entry in assets if isinstance(entry, dict)}
        require(set(records) == set(EXPECTED_SHA256),
                "Portable export report asset names do not match the four pinned filenames")
        source_provenance = {
            "mode": "portable-export-report",
            "export_report": "export-report.json",
            "product": "wow",
            "version": PORTABLE_EXPORT_VERSION,
            "metadata_unchanged": True,
            "asset_count": 4,
            "total_payload_bytes": PORTABLE_EXPORT_TOTAL_BYTES,
            "progress_download_event_count": 0,
        }

    payloads: dict[str, bytes] = {}
    total_bytes = 0
    for name, expected_hash in EXPECTED_SHA256.items():
        entry = records[name]
        if source_dir is not None:
            expected_file_data_id = EXPECTED_FILE_DATA_IDS[name]
            require(entry.get("file_data_id") == expected_file_data_id and
                    entry.get("requested_file_data_id") == expected_file_data_id and
                    entry.get("reported_file_data_id") == expected_file_data_id,
                    f"Portable export report has an unexpected FileDataID for {name}")
        require(entry.get("bytes") == EXPECTED_FILE_BYTES[name],
                f"{name} source report byte count is not the pinned size")
        path = input_dir / name
        data = read_bounded_file(path, EXPECTED_FILE_BYTES[name], name,
                                 expected_size=EXPECTED_FILE_BYTES[name])
        require(len(data) == entry.get("bytes"), f"{name} byte count disagrees with source report")
        require(entry.get("sha256") == expected_hash and sha256(data) == expected_hash,
                f"{name} SHA-256 does not match the pinned source")
        total_bytes += len(data)
        payloads[name] = data
    if source_dir is not None:
        require(total_bytes == PORTABLE_EXPORT_TOTAL_BYTES and
                export_report.get("total_payload_bytes") == PORTABLE_EXPORT_TOTAL_BYTES,
                "Portable export payload total is not the pinned 194,288 bytes")

    core, chunk_summary = parse_chunked_model(payloads["model-1693135.m2"])
    counts: dict[str, tuple[int, int]] = {
        name: m2_array(core, offset, name) for name, offset in MODERN_ARRAY_OFFSETS.items()
    }
    expected_counts = {
        "animations": 1, "bones": 8, "vertices": 1712, "colors": 0,
        "textures": 2, "texture_weights": 1, "texture_animations": 0,
        "texture_replacements": 3, "render_flags": 2, "attachments": 5,
        "events": 2, "lights": 0, "cameras": 0, "ribbons": 0, "particles": 0,
    }
    for name, expected in expected_counts.items():
        require(counts[name][0] == expected, f"Unsupported {name} count {counts[name][0]}")
    require(struct.unpack_from("<I", core, 0x44)[0] == 1, "Expected exactly one external skin view")
    animation_count = counts["animations"][0]
    require(animation_count == 1, f"Expected one source sequence, got {animation_count}")

    vertex_count, vertex_offset = counts["vertices"]
    vertex_blob = bounded_slice(core, vertex_offset, vertex_count, 48, "retail vertices")
    positions: list[tuple[float, float, float]] = []
    for index in range(vertex_count):
        vertex = vertex_blob[index * 48 : (index + 1) * 48]
        position = struct.unpack_from("<3f", vertex, 0)
        weights = tuple(vertex[12:16])
        bone_indices = tuple(vertex[16:20])
        normal = struct.unpack_from("<3f", vertex, 20)
        uv0 = struct.unpack_from("<2f", vertex, 32)
        uv1 = struct.unpack_from("<2f", vertex, 40)
        require(all(math.isfinite(value) for value in (*position, *normal, *uv0, *uv1)),
                f"Vertex {index} has nonfinite position, normal, UV0, or UV1")
        require(weights == (255, 0, 0, 0) and bone_indices == (0, 0, 0, 0),
                f"Vertex {index} is not fully rigid on source bone 0")
        positions.append(position)

    bone_count, bone_offset = counts["bones"]
    bone_blob = bounded_slice(core, bone_offset, bone_count, 88, "retail bones")
    bone_tracks = 0
    bone_summary: list[dict[str, Any]] = []
    for index in range(bone_count):
        base = bone_offset + index * 88
        key_bone, flags, parent, submesh, name_crc = struct.unpack_from("<Ii h H I", core, base)
        tracks = [parse_modern_track(core, base + track_offset, f"bone[{index}].{name}",
                                     expected_sequences=animation_count)
                  for name, track_offset in (("translation", 16), ("rotation", 36), ("scale", 56))]
        require(all(sum(track["sequence_key_counts"]) == 0 for track in tracks),
                f"Bone {index} has animated transforms; a rest-pose conversion is unsafe")
        bone_tracks += len(tracks)
        bone_summary.append({"index": index, "key_bone": key_bone, "flags": flags,
                             "parent": parent, "submesh": submesh, "name_crc": name_crc,
                             "transform_key_counts": [sum(track["sequence_key_counts"])
                                                       for track in tracks]})

    animation_count, animation_offset = counts["animations"]
    animation = bounded_slice(core, animation_offset, animation_count, 64, "retail sequence")
    anim_id, variation_id, duration_ms = struct.unpack_from("<HHI", animation, 0)
    anim_flags = struct.unpack_from("<I", animation, 12)[0]

    skin = payloads["skin-1693139.skin"]
    require(len(skin) == 17808 and skin[:4] == b"SKIN", "Unsupported external skin file")
    skin_arrays = [struct.unpack_from("<II", skin, offset) for offset in (4, 12, 20, 28, 36)]
    (lookup_count, lookup_offset), (triangle_count, triangle_offset), \
        (property_count, property_offset), (section_count, section_offset), \
        (batch_count, batch_offset) = skin_arrays
    skin_bone_count = struct.unpack_from("<I", skin, 44)[0]
    require((lookup_count, triangle_count, property_count, section_count, batch_count, skin_bone_count)
            == (1712, 3684, 1712, 1, 1, 0), "Unsupported skin array topology")
    lookup_blob = bounded_slice(skin, lookup_offset, lookup_count, 2, "skin vertex lookup")
    triangle_blob = bounded_slice(skin, triangle_offset, triangle_count, 2, "skin triangles")
    property_blob = bounded_slice(skin, property_offset, property_count, 4, "skin properties")
    section_blob = bounded_slice(skin, section_offset, section_count, 48, "skin section")
    batch_blob = bounded_slice(skin, batch_offset, batch_count, 24, "skin batch")
    lookup = struct.unpack(f"<{lookup_count}H", lookup_blob)
    triangles = struct.unpack(f"<{triangle_count}H", triangle_blob)
    require(all(value < vertex_count for value in lookup), "Skin vertex lookup escapes the vertex array")
    require(triangle_count % 3 == 0 and all(value < lookup_count for value in triangles),
            "Skin triangle indices are malformed or out of range")
    source_section = struct.unpack_from("<8H", section_blob, 0)
    require(source_section == (0, 0, 0, 1712, 0, 3684, 1, 0),
            f"Unsupported source section layout: {source_section}")
    source_batch = struct.unpack_from("<12H", batch_blob, 0)
    require(source_batch[2] == 0 and source_batch[5] == 0 and source_batch[7] == 2,
            f"Unsupported source batch layout: {source_batch}")

    texture_count, texture_offset = counts["textures"]
    texture_blob = bounded_slice(core, texture_offset, texture_count, 16, "retail texture records")
    texture_types = [struct.unpack_from("<I", texture_blob, i * 16)[0] for i in range(texture_count)]
    texture_flags = [struct.unpack_from("<I", texture_blob, i * 16 + 4)[0] for i in range(texture_count)]
    require(texture_types == [2, 0], f"Unsupported source texture type layout: {texture_types}")
    source_render = bounded_slice(core, counts["render_flags"][1], 2, 4, "retail render flags")
    source_render_values = [struct.unpack_from("<HH", source_render, i * 4) for i in range(2)]

    # Validate the nested per-sequence timestamp and value arrays before asserting this is static.
    weight_count, weight_offset = counts["texture_weights"]
    weight_blob = bounded_slice(core, weight_offset, weight_count, 28, "retail transparency track")
    weight_track = parse_modern_track(core, weight_offset, "texture_weight[0]", value_stride=2,
                                      record_size=28, expected_sequences=animation_count)
    require(weight_track["outer_sequence_count"] == animation_count and
            weight_track["sequence_key_counts"] == [1] and weight_track["global_sequence"] == -1,
            "Unsupported non-static source transparency track; expected one inner key in the only sequence")
    weight_raw_by_sequence = [
        list(struct.unpack(f"<{len(data) // 2}H", data)) if data else []
        for data in weight_track["sequence_value_arrays"]
    ]
    require(weight_raw_by_sequence == [[32767]],
            f"Unsupported source transparency value(s): {weight_raw_by_sequence}")
    weight_fixed_by_sequence = [[value / 32767.0 for value in values]
                                 for values in weight_raw_by_sequence]
    require(all(math.isfinite(value) for values in weight_fixed_by_sequence for value in values),
            "Source transparency track has a nonfinite fixed-point value")
    require(weight_blob[20:28] == b"\0" * 8, "Unsupported trailing texture-weight record bytes")

    attachment_count, attachment_offset = counts["attachments"]
    attachments = bounded_slice(core, attachment_offset, attachment_count, 40, "retail attachments")
    attachment_summary = []
    for index in range(attachment_count):
        base = attachment_offset + index * 40
        attach_id, bone, unknown = struct.unpack_from("<IHH", core, base)
        position = struct.unpack_from("<3f", core, base + 8)
        require(bone < bone_count, f"Attachment {index} references missing source bone {bone}")
        require(all(math.isfinite(value) for value in position),
                f"Attachment {index} has a nonfinite position")
        track = parse_modern_track(core, base + 20, f"attachment[{index}].animate_attached",
                                   expected_sequences=animation_count)
        require(sum(track["sequence_key_counts"]) == 0,
                f"Attachment {index} has an active attached-model track")
        attachment_summary.append({"id": attach_id, "bone": bone, "unknown": unknown,
                                   "position": position,
                                   "animate_attached_key_count": sum(track["sequence_key_counts"])})
    require([item["id"] for item in attachment_summary] == list(range(5)),
            "Unexpected source attachment IDs")

    event_count, event_offset = counts["events"]
    events = bounded_slice(core, event_offset, event_count, 36, "retail events")
    event_summary = []
    for index in range(event_count):
        base = event_offset + index * 36
        identifier = core[base : base + 4].decode("ascii", errors="strict")
        data, bone = struct.unpack_from("<II", core, base + 4)
        position = struct.unpack_from("<3f", core, base + 12)
        require(all(math.isfinite(value) for value in position),
                f"Event {index} has a nonfinite position")
        timestamp_track = parse_modern_event_timestamps(
            core, base + 24, f"event[{index}] timestamps", animation_count)
        event_summary.append({"identifier": identifier, "data": data, "bone": bone,
                              "position": position,
                              "timestamp_interpolation": timestamp_track["interpolation"],
                              "timestamp_global_sequence": timestamp_track["global_sequence"],
                              "timestamp_outer_sequence_count": timestamp_track["outer_sequence_count"],
                              "timestamp_key_counts_by_sequence": timestamp_track["sequence_key_counts"],
                              "timestamps_by_sequence": timestamp_track["timestamps_by_sequence"]})
    require([event["identifier"] for event in event_summary] == ["$WTB", "$WTT"],
            "Unexpected source event identifiers")
    require(all(event["timestamp_outer_sequence_count"] == animation_count and
                event["timestamp_key_counts_by_sequence"] == [0] and
                event["timestamps_by_sequence"] == [[]] for event in event_summary),
            "Source event timestamp arrays changed; expected empty inner arrays for the sole sequence")

    blp = payloads["texture-1733940.blp"]
    require(blp[:4] == b"BLP2" and struct.unpack_from("<II", blp, 12) == (256, 256),
            "Selected silver texture is no longer the verified 256x256 BLP2")
    aux_blp = payloads["texture-144333.blp"]
    require(aux_blp[:4] == b"BLP2", "Direct auxiliary BLP is not BLP2")

    mins = tuple(min(point[axis] for point in positions) for axis in range(3))
    maxs = tuple(max(point[axis] for point in positions) for axis in range(3))
    center = tuple(sum(point[axis] for point in positions) / len(positions) for axis in range(3))
    radius = max(math.dist(point, center) for point in positions)
    geometry = {"positions": positions, "mins": mins, "maxs": maxs, "center": center, "radius": radius,
                "vertices_raw": vertex_blob, "vertex_lookup_raw": lookup_blob,
                "triangles_raw": triangle_blob, "properties_raw": property_blob}

    summary = {
        "source_provenance": source_provenance,
        "model_file_data_id": MODEL_ID,
        "skin_file_data_id": SKIN_ID,
        "auxiliary_texture_file_data_id": AUX_TEXTURE_ID,
        "selected_silver_texture_file_data_id": SILVER_TEXTURE_ID,
        "input_sha256": {name: EXPECTED_SHA256[name] for name in sorted(EXPECTED_SHA256)},
        "modern_shell": chunk_summary,
        "source_counts": {name: pair[0] for name, pair in counts.items()},
        "source_sequence": {"id": anim_id, "variation": variation_id,
                            "duration_ms": duration_ms, "flags": anim_flags},
        "source_bones": bone_summary,
        "empty_skeletal_transform_tracks": bone_tracks,
        "validated_vertex_attributes": ["finite positions", "finite normals", "finite UV0", "finite UV1"],
        "vertices": vertex_count,
        "triangle_indices": triangle_count,
        "triangles": triangle_count // 3,
        "skin": {"vertex_lookup_count": lookup_count, "property_count": property_count,
                 "section_count": section_count, "batch_count": batch_count,
                 "section_record_bytes": 48, "source_batch_words": source_batch},
        "source_texture_types": texture_types,
        "source_texture_flags": texture_flags,
        "source_render_flags": source_render_values,
        "source_transparency_track": {
            "interpolation": weight_track["interpolation"],
            "global_sequence": weight_track["global_sequence"],
            "outer_sequence_count": weight_track["outer_sequence_count"],
            "key_counts_by_sequence": weight_track["sequence_key_counts"],
            "timestamps_ms_by_sequence": weight_track["timestamps_by_sequence"],
            "fixed16_raw_by_sequence": weight_raw_by_sequence,
            "fixed16_value_by_sequence": weight_fixed_by_sequence,
            "constant_single_key_verified": True,
        },
        "source_attachments": attachment_summary,
        "source_events": event_summary,
        "zero_source_effect_counts": {name: counts[name][0] for name in
                                      ("lights", "ribbons", "particles", "texture_animations")},
        "geometry_bounds": {"mins": mins, "maxs": maxs, "center": center, "radius": radius},
        "preserved_features": [
            "Five static source attachment anchors (IDs 0-4) are preserved in legacy 48-byte records, remapped to identity bone 0, and indexed by an attachment lookup.",
            "All source vertex positions, normals, UV0, and UV1 are finite and retained byte-for-byte.",
        ],
        "omitted_features": [
            "Seven unused source bones are collapsed to one identity bone; all source vertices use only bone 0 at weight 255, and all 24 bone transform tracks have zero keys.",
            "The source animation sequence is emitted as a neutral 1,000 ms sequence with no transform channels; original timing/flags are not renderer-equivalent.",
            "Two source event records ($WTB/$WTT) are omitted. Their nested timestamp arrays contain zero inner keys in the sole source sequence, and their weapon-trail start/end marker semantics are not reproduced.",
            "The two-layer modern batch is reduced to one opaque native diffuse layer; auxiliary type-0 BLP 144333 (ArmorReflect_Hard) and the modern shader/material behavior are omitted.",
            "The selected silver BLP2 is copied byte-for-byte and used as a type-0 filename texture; BLP channel behavior is not reproduced by the opaque material.",
            "The source texture-weight track is verified as one inner key (timestamp 0 ms, fixed16 raw 32767 / value 1.0) and omitted; the output material is explicitly opaque.",
            "The TXAC chunk is recognized as an extended texture-animation count and validated as zero; SFID/TXID metadata are consumed for source selection and are not emitted in legacy MD20.",
        ],
        "preview_limit": "Static visual approximation only; no retail item-ID/DB2 mapping or exact renderer equivalence is claimed.",
    }
    if source_dir is None:
        summary["run_id"] = RUN_ID
    return core, skin, blp, {"summary": summary, "geometry": geometry}


def append_payload(model: bytearray, data: bytes, alignment: int = 4) -> int:
    padding = (-len(model)) % alignment
    if padding:
        model.extend(b"\0" * padding)
    offset = len(model)
    model.extend(data)
    return offset


def set_array(model: bytearray, header_offset: int, data: bytes, count: int, alignment: int = 4) -> tuple[int, int]:
    if count == 0:
        struct.pack_into("<II", model, header_offset, 0, 0)
        return 0, 0
    offset = append_payload(model, data, alignment)
    struct.pack_into("<II", model, header_offset, count, offset)
    return count, offset


def empty_bone_v256() -> bytes:
    bone = bytearray(108)
    struct.pack_into("<Ii h H", bone, 0, 0xFFFFFFFF, 0, -1, 0)
    for offset in (12, 40, 68):
        struct.pack_into("<Hh", bone, offset, 0, -1)
    struct.pack_into("<3f", bone, 96, 0.0, 0.0, 0.0)
    return bytes(bone)


def neutral_sequence_v256(bounds: dict[str, Any]) -> bytes:
    record = bytearray(68)
    struct.pack_into("<HHII f I h H II I", record, 0,
                     0, 0, 0, 1000, 0.0, 0, 0x7FFF, 0, 0, 0, 0)
    struct.pack_into("<3f", record, 36, *bounds["mins"])
    struct.pack_into("<3f", record, 48, *bounds["maxs"])
    struct.pack_into("<f", record, 60, bounds["radius"])
    struct.pack_into("<hH", record, 64, -1, 0xFFFF)
    return bytes(record)


def attachment_payload_v256(source_attachments: list[dict[str, Any]]) -> tuple[bytes, bytes]:
    ids = [item["id"] for item in source_attachments]
    require(ids == list(range(5)), f"Unexpected attachment IDs for legacy lookup: {ids}")
    records = bytearray()
    lookup = [0xFFFF] * (max(ids) + 1)
    for record_index, item in enumerate(source_attachments):
        attach_id = item["id"]
        position = item["position"]
        require(all(math.isfinite(value) for value in position),
                f"Attachment {attach_id} has a nonfinite output position")
        record = bytearray(48)
        struct.pack_into("<II3f", record, 0, attach_id, 0, *position)
        records.extend(record)
        lookup[attach_id] = record_index
    require(all(index != 0xFFFF for index in lookup), "Attachment lookup has an unfilled source ID")
    return bytes(records), struct.pack(f"<{len(lookup)}H", *lookup)


def build_v256(core: bytes, skin: bytes, inspected: dict[str, Any]) -> bytes:
    summary = inspected["summary"]
    geometry = inspected["geometry"]
    model = bytearray(0x144)
    struct.pack_into("<4sI", model, 0, b"MD20", 256)
    struct.pack_into("<I", model, 0x10, 0)  # minimal native model flags

    name_data = b"KultirasQuestB01_Silver_Static\0"
    set_array(model, V256_ARRAY_OFFSETS["name"], name_data, len(name_data), 1)
    set_array(model, V256_ARRAY_OFFSETS["animations"], neutral_sequence_v256(geometry), 1)
    set_array(model, V256_ARRAY_OFFSETS["bones"], empty_bone_v256(), 1)
    attachment_records, attachment_lookup = attachment_payload_v256(summary["source_attachments"])
    set_array(model, V256_ARRAY_OFFSETS["attachments"], attachment_records,
              len(summary["source_attachments"]))
    set_array(model, V256_ARRAY_OFFSETS["attachment_lookup"], attachment_lookup,
              len(summary["source_attachments"]), 2)

    source_vertices = geometry["vertices_raw"]
    output_vertices = bytearray(source_vertices)
    for index in range(summary["vertices"]):
        base = index * 48
        output_vertices[base + 12 : base + 16] = b"\xff\0\0\0"
        output_vertices[base + 16 : base + 20] = b"\0\0\0\0"
    set_array(model, V256_ARRAY_OFFSETS["vertices"], bytes(output_vertices), summary["vertices"])

    view_offset = append_payload(model, b"\0" * 44)
    struct.pack_into("<II", model, V256_ARRAY_OFFSETS["views"], 1, view_offset)
    view = bytearray(44)
    view_arrays = (
        (geometry["vertex_lookup_raw"], summary["vertices"], 2),
        (geometry["triangles_raw"], summary["triangle_indices"], 2),
        (geometry["properties_raw"], summary["vertices"], 4),
    )
    for index, (data, count, stride) in enumerate(view_arrays):
        payload_offset = append_payload(model, data)
        struct.pack_into("<II", view, index * 8, count, payload_offset)

    center = geometry["center"]
    section = struct.pack("<8H4f", 0, 0, 0, summary["vertices"], 0,
                          summary["triangle_indices"], 1, 0,
                          *center, geometry["radius"])
    require(len(section) == 32, "Internal v256 section stride mismatch")
    section_offset = append_payload(model, section)
    struct.pack_into("<II", view, 24, 1, section_offset)

    batch = struct.pack("<12H", 0, 0, 0, 0, 0xFFFF, 0, 0, 1,
                        0, 0, 0xFFFF, 0xFFFF)
    require(len(batch) == 24, "Internal v256 batch stride mismatch")
    batch_offset = append_payload(model, batch)
    struct.pack_into("<II", view, 32, 1, batch_offset)
    struct.pack_into("<I", view, 40, 1)  # one identity bone in this embedded view
    model[view_offset : view_offset + 44] = view

    texture_name = TEXTURE_INTERNAL.encode("ascii") + b"\0"
    filename_offset = append_payload(model, texture_name, 1)
    texture_record = struct.pack("<II II", 0, 0, len(texture_name), filename_offset)
    set_array(model, V256_ARRAY_OFFSETS["textures"], texture_record, 1)
    set_array(model, V256_ARRAY_OFFSETS["render_flags"], struct.pack("<HH", 0, 0), 1)
    set_array(model, V256_ARRAY_OFFSETS["bone_lookup"], struct.pack("<H", 0), 1, 2)
    set_array(model, V256_ARRAY_OFFSETS["texture_lookup"], struct.pack("<H", 0), 1, 2)
    set_array(model, V256_ARRAY_OFFSETS["texture_unit_lookup"], struct.pack("<H", 0), 1, 2)

    mins, maxs, radius = geometry["mins"], geometry["maxs"], max(
        math.dist(point, (0.0, 0.0, 0.0)) for point in geometry["positions"]
    )
    struct.pack_into("<3f", model, 0xB4, *mins)
    struct.pack_into("<3f", model, 0xC0, *maxs)
    struct.pack_into("<f", model, 0xCC, radius)
    struct.pack_into("<3f", model, 0xD0, *mins)
    struct.pack_into("<3f", model, 0xDC, *maxs)
    struct.pack_into("<f", model, 0xE8, radius)

    result = bytes(model)
    validate_v256(result, geometry, summary)
    return result


def validate_v256(model: bytes, geometry: dict[str, Any], summary: dict[str, Any]) -> None:
    require(model[:4] == b"MD20" and struct.unpack_from("<I", model, 4)[0] == 256,
            "Output is not MD20 v256")
    arrays = {name: m2_array(model, offset, name) for name, offset in V256_ARRAY_OFFSETS.items()}
    require(arrays["animations"][0] == 1 and arrays["bones"][0] == 1 and
            arrays["vertices"][0] == summary["vertices"] and arrays["views"][0] == 1,
            "Output header counts are wrong")
    source_attachments = summary["source_attachments"]
    require(arrays["attachments"][0] == len(source_attachments) and
            arrays["attachment_lookup"][0] == len(source_attachments),
            "Output attachment arrays have the wrong counts")
    attachment_blob = bounded_slice(model, arrays["attachments"][1], arrays["attachments"][0],
                                    48, "output attachments")
    lookup_blob = bounded_slice(model, arrays["attachment_lookup"][1],
                                arrays["attachment_lookup"][0], 2, "output attachment lookup")
    lookup = struct.unpack(f"<{arrays['attachment_lookup'][0]}H", lookup_blob)
    require(lookup == tuple(range(len(source_attachments))),
            f"Output attachment lookup is not an ID-to-record identity map: {lookup}")
    for index, source in enumerate(source_attachments):
        record = attachment_blob[index * 48 : (index + 1) * 48]
        expected_head = struct.pack("<II3f", source["id"], 0, *source["position"])
        require(record[:20] == expected_head and record[20:] == b"\0" * 28,
                f"Attachment {source['id']} was not preserved at its source position on identity bone 0")
    vertex_data = bounded_slice(model, arrays["vertices"][1], arrays["vertices"][0], 48, "output vertices")
    for index in range(summary["vertices"]):
        original = geometry["vertices_raw"][index * 48 : (index + 1) * 48]
        actual = vertex_data[index * 48 : (index + 1) * 48]
        require(actual[:12] == original[:12] and actual[20:48] == original[20:48],
                f"Vertex {index} position/normal/UV bytes changed")
        require(actual[12:16] == b"\xff\0\0\0" and actual[16:20] == b"\0\0\0\0",
                f"Vertex {index} did not collapse to identity bone 0")
    view_count, view_offset = arrays["views"]
    view = bounded_slice(model, view_offset, view_count, 44, "output view")
    for index, (expected_data, expected_count, stride) in enumerate((
        (geometry["vertex_lookup_raw"], summary["vertices"], 2),
        (geometry["triangles_raw"], summary["triangle_indices"], 2),
        (geometry["properties_raw"], summary["vertices"], 4),
    )):
        count, offset = struct.unpack_from("<II", view, index * 8)
        actual = bounded_slice(model, offset, count, stride, f"output view array {index}")
        require(count == expected_count and actual == expected_data,
                f"Output view array {index} did not preserve source bytes")


TOOL_TIMEOUT_SECONDS = 30
_CONVERSION_JOB_HANDLE = None


def arm_conversion_job() -> None:
    """Keep Windows packer descendants tied to this converter's lifetime.

    Joining before any child is spawned gives descendants job membership at
    creation. Only this process retains the non-inheritable job handle; Windows
    closes it on normal exit or termination and kills remaining job members.
    """
    global _CONVERSION_JOB_HANDLE
    if os.name != "nt" or _CONVERSION_JOB_HANDLE is not None:
        return
    import ctypes
    from ctypes import wintypes

    class BasicLimits(ctypes.Structure):
        _fields_ = [
            ("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
            ("flags", wintypes.DWORD), ("min_working_set", ctypes.c_size_t),
            ("max_working_set", ctypes.c_size_t), ("active_processes", wintypes.DWORD),
            ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
            ("scheduling", wintypes.DWORD),
        ]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("basic", BasicLimits), ("io_counters", ctypes.c_uint64 * 6),
            ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
            ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                               ctypes.c_void_p, wintypes.DWORD]
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateJobObjectW(None, None)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    limits = ExtendedLimits()
    limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
        error = ctypes.get_last_error()
        kernel.CloseHandle(handle)
        raise ctypes.WinError(error)
    if not kernel.AssignProcessToJobObject(handle, kernel.GetCurrentProcess()):
        error = ctypes.get_last_error()
        kernel.CloseHandle(handle)
        raise ctypes.WinError(error)
    # Deliberately retained until OS process teardown, after CLI output flushes.
    _CONVERSION_JOB_HANDLE = handle


def run_tool(command: list[str], log_path: Path) -> str:
    completed = subprocess.run(command, cwd=log_path.parent, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", check=False, timeout=TOOL_TIMEOUT_SECONDS)
    output = completed.stdout + completed.stderr
    log_path.write_text(output, encoding="utf-8", newline="\n")
    if completed.returncode != 0:
        raise RuntimeError(f"Command failed ({completed.returncode}): {' '.join(command)}; see {log_path}")
    return output


def resolve_packer(explicit_path: Path | None) -> Path:
    if explicit_path is not None:
        packer = explicit_path.resolve(strict=True)
        require(packer.is_file() and packer.name.casefold() == "pack-mpq.py",
                "Packer path must name the first-party pack-mpq.py script")
        return packer
    module_dir = Path(__file__).resolve().parent
    for candidate in (module_dir / "pack-mpq.py", module_dir.parent / "pack-mpq.py"):
        if candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError("pack-mpq.py was not found beside the builder or in its parent directory; pass --packer")


def validate_mpq_header(archive: bytes) -> dict[str, int]:
    require(len(archive) >= 32, "Generated MPQ is shorter than its v1 header")
    magic, header_size, archive_size, format_version, sector_shift, hash_offset, block_offset, \
        hash_count, block_count = struct.unpack_from("<4sIIHHIIII", archive, 0)
    require(magic == b"MPQ\x1a" and header_size == 32 and archive_size == len(archive) and
            format_version == 0 and sector_shift == 3,
            "Generated archive does not have the expected MPQ v1 header")
    require(hash_count >= 4 and hash_count & (hash_count - 1) == 0 and block_count == 3,
            "Generated MPQ table counts are not the expected two payloads plus listfile")
    require(hash_offset >= header_size and hash_offset + hash_count * 16 == block_offset and
            block_offset + block_count * 16 == archive_size,
            "Generated MPQ table offsets do not fit the archive")
    return {"header_bytes": header_size, "archive_bytes": archive_size,
            "format_version": format_version, "sector_shift": sector_shift,
            "hash_entry_count": hash_count, "block_entry_count_including_listfile": block_count}


def build(args: argparse.Namespace) -> dict[str, Any]:
    source_dir_arg = getattr(args, "source_dir", None)
    if source_dir_arg is None:
        input_dir = (ROOT / "analysis" / "casc-reader-runtime" / "extracted" / RUN_ID).resolve()
        inspected_source_dir = None
    else:
        input_dir = Path(source_dir_arg).resolve(strict=True)
        inspected_source_dir = input_dir

    default_output = ROOT / "analysis" / "retail-rigid-preview" / "output-attachments"
    output_arg = getattr(args, "output_dir", None)
    output_dir = Path(output_arg).resolve() if output_arg is not None else default_output.resolve()
    require(output_dir != input_dir and output_dir not in input_dir.parents and input_dir not in output_dir.parents,
            "Output directory must be separate from and outside the source directory")
    if output_dir.exists() or output_dir.is_symlink():
        raise FileExistsError(f"Refusing to overwrite existing output directory: {output_dir}")

    _core, _skin, silver_blp, inspected = inspect_inputs(inspected_source_dir)
    md20 = build_v256(_core, _skin, inspected)
    model_digest = sha256(md20)
    require(model_digest == EXPECTED_PREVERIFIED_MODEL_SHA256,
            f"Generated MD20 SHA-256 {model_digest} does not match the accepted preverified model hash")
    report = inspected["summary"]
    output_dir.mkdir(parents=True)
    payload_dir = output_dir / "payload"
    payload_model = payload_dir / MODEL_REL
    payload_texture = payload_dir / TEXTURE_REL
    payload_model.parent.mkdir(parents=True, exist_ok=True)
    payload_texture.parent.mkdir(parents=True, exist_ok=True)
    payload_model.write_bytes(md20)
    payload_texture.write_bytes(silver_blp)
    require(sha256(payload_model.read_bytes()) == model_digest and
            sha256(payload_texture.read_bytes()) == EXPECTED_SHA256["texture-1733940.blp"],
            "Written loose payload hashes differ from the validated conversion inputs")

    packer = resolve_packer(getattr(args, "packer", None))
    archive = output_dir / "KultirasQuestB01_Silver_Static.mpq"
    pack_output = run_tool([sys.executable, str(packer), str(payload_dir), str(archive)],
                           output_dir / "pack-mpq.txt")
    require("(2 source files," in pack_output,
            "First-party packer did not report exactly the two expected payload files")
    mpq_details = validate_mpq_header(archive.read_bytes())

    report["output"] = {
        "directory": str(output_dir),
        "format": "MD20 v256, no MD21 wrapper",
        "model_path": MODEL_INTERNAL,
        "texture_path": TEXTURE_INTERNAL,
        "model_bytes": len(md20),
        "model_sha256": model_digest,
        "verification_mode": "preverified-output-hash",
        "accepted_preverified_model_sha256": EXPECTED_PREVERIFIED_MODEL_SHA256,
        "native_parser_status": "not_run",
        "native_parser_note": "The matching MD20 hash was previously native-parser verified; this portable build does not run or bundle that parser.",
        "texture_bytes": len(silver_blp),
        "texture_sha256": sha256(silver_blp),
        "mpq_bytes": archive.stat().st_size,
        "mpq_sha256": sha256(archive.read_bytes()),
        "mpq_header_validation": mpq_details,
        "mpq_readback_status": "not_run",
        "packer": packer.name,
        "packed_payload_file_count": 2,
        "packed_payload_paths": [MODEL_INTERNAL, TEXTURE_INTERNAL],
        "loose_payload_hashes_match": True,
        "preserved_source_vertex_bytes": ["position", "normal", "UV0", "UV1"],
        "validated_finite_source_vertex_attributes": ["position", "normal", "UV0", "UV1"],
        "preserved_source_skin_bytes": ["vertex lookup", "triangle indices", "four-byte per-vertex property records"],
        "preserved_topology": {"vertices": 1712, "triangle_indices": 3684, "triangles": 1228,
                                "sections": 1, "batches": 1},
        "preserved_attachment_ids": [item["id"] for item in report["source_attachments"]],
        "preserved_attachment_count": len(report["source_attachments"]),
        "output_event_count": 0,
    }
    report_path = output_dir / "conversion-report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the pinned static silver mace MD20 v256 preview.")
    parser.add_argument("--source-dir", type=Path, default=None,
                        help="portable export directory containing the four pinned files and export-report.json; omitted uses the internal pinned project closure")
    parser.add_argument("--output-dir", type=Path, default=None,
                        help="new output directory, outside the source directory; existing paths are never overwritten")
    parser.add_argument("--packer", type=Path, default=None,
                        help="path to the first-party pack-mpq.py when it is not beside the builder or in its parent directory")
    parser.add_argument("--preflight", action="store_true",
                        help="inspect and validate pinned inputs without writing output")
    args = parser.parse_args()
    try:
        if args.preflight:
            _, _, _, inspected = inspect_inputs(args.source_dir)
            print(json.dumps(inspected["summary"], indent=2, sort_keys=True))
        else:
            arm_conversion_job()
            report = build(args)
            print(json.dumps({"status": "success", "output": report["output"]}, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        print(f"retail-rigid-preview: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
