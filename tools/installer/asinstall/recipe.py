"""Recipe schema ``as-recipe/1``: strict loading and validation. See RECIPES.md.

A recipe is declarative data. It never contains executable code: converters are
named tools from ``tools/registry.json`` (hash pinned), everything else is a
built-in step op. No Blizzard bytes belong in a recipe; it carries only public
identifiers, sizes, hashes and small authored patch payloads.
"""
from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from typing import Any

from .util import InstallerError, lower_hex, read_json, safe_rel

SCHEMA = "as-recipe/1"
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
SLOT_RE = re.compile(r"^patch(-\d+)?\.MPQ$")
STATUSES = ("ready", "experimental", "unavailable")
SOURCE_KINDS = ("casc", "game-chain", "user-supplied")
STEP_OPS = ("casc-export", "game-extract", "user-source", "run-tool", "dbc-edit", "binary-edit")
LOCALE_MASKS = {"enUS": 0x2, "koKR": 0x4, "frFR": 0x10, "deDE": 0x20, "zhCN": 0x40, "esES": 0x80,
                "zhTW": 0x100, "enGB": 0x200, "esMX": 0x1000, "ruRU": 0x2000, "ptBR": 0x4000,
                "itIT": 0x8000, "ptPT": 0x10000}
# Slots the installer may write. patch-0/1 sit below the official patch-2..N, as
# the existing project overlays do; patch.MPQ and numbers >= 2 are never written.
WRITABLE_SLOTS = ("patch-0.MPQ", "patch-1.MPQ")
EXE_NAME = "AzerothSurvivors.exe"
MAX_EDIT_BYTES = 1024 * 1024
TOP_KEYS = {"schema", "id", "version", "title", "description", "status", "default", "unavailable_reason",
            "requires", "sources", "steps", "outputs", "acceptance", "notes", "provenance", "limits"}


def _only(obj: Any, allowed: set[str], required: set[str], label: str) -> dict[str, Any]:
    if not isinstance(obj, dict):
        raise InstallerError(f"{label} must be an object", code="recipe-invalid")
    extra = set(obj) - allowed
    if extra:
        raise InstallerError(f"{label} has unknown keys: {sorted(extra)}", code="recipe-invalid")
    missing = required - set(obj)
    if missing:
        raise InstallerError(f"{label} is missing keys: {sorted(missing)}", code="recipe-invalid")
    return obj


def _int(value: Any, label: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise InstallerError(f"{label} must be an integer >= {minimum}", code="recipe-invalid")
    return value


def _str(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise InstallerError(f"{label} must be a non-empty string", code="recipe-invalid")
    return value


def _hash_list(value: Any, label: str) -> list[str]:
    items = value if isinstance(value, list) else [value]
    if not items:
        raise InstallerError(f"{label} must not be empty", code="recipe-invalid")
    return [lower_hex(v, 64, label) for v in items]


def internal_path(value: Any, label: str) -> str:
    """MPQ internal path, backslash separated, ASCII, no traversal."""
    text = _str(value, label)
    if "/" in text or ":" in text or "\x00" in text or text.startswith("\\"):
        raise InstallerError(f"{label} must be a backslash-separated relative MPQ path: {text!r}", code="recipe-invalid")
    try:
        text.encode("ascii")
    except UnicodeEncodeError:
        raise InstallerError(f"{label} must be ASCII: {text!r}", code="recipe-invalid") from None
    if any(p in ("", ".", "..") for p in text.split("\\")):
        raise InstallerError(f"{label} has an unsafe segment: {text!r}", code="recipe-invalid")
    if text.casefold() == "(listfile)":
        raise InstallerError(f"{label} must not be (listfile)", code="recipe-invalid")
    return text


def parse_ref(value: Any, label: str, step_ids: set[str]) -> tuple[str, str]:
    """``<step-id>:<relative path>`` (path may be empty for a whole step directory)."""
    text = _str(value, label)
    step, _, rel = text.partition(":")
    if step not in step_ids:
        raise InstallerError(f"{label}: {text!r} names no earlier step", code="recipe-invalid")
    return step, (safe_rel(rel, label) if rel else "")


@dataclass
class Recipe:
    raw: dict[str, Any]
    path: Path | None = None
    slots: list[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        return self.raw["id"]

    @property
    def version(self) -> str:
        return self.raw["version"]

    @property
    def status(self) -> str:
        return self.raw["status"]

    @property
    def title(self) -> str:
        return self.raw["title"]

    @property
    def available(self) -> bool:
        return self.status != "unavailable"

    @property
    def sources(self) -> dict[str, Any]:
        return self.raw.get("sources", {})

    @property
    def steps(self) -> list[dict[str, Any]]:
        return self.raw.get("steps", [])

    @property
    def mpq_files(self) -> list[dict[str, Any]]:
        return self.raw.get("outputs", {}).get("mpq_files", [])

    @property
    def exe_patches(self) -> list[dict[str, Any]]:
        return self.raw.get("outputs", {}).get("exe_patches", [])

    def accepted_exe_hashes(self) -> list[str] | None:
        game = self.raw.get("requires", {}).get("game", {})
        return game.get("exe_sha256")


def _validate_source(sid: str, src: Any) -> dict[str, Any]:
    label = f"sources.{sid}"
    if not ID_RE.match(sid.replace("_", "-")) and not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", sid):
        raise InstallerError(f"{label}: invalid source id", code="recipe-invalid")
    if not isinstance(src, dict) or src.get("kind") not in SOURCE_KINDS:
        raise InstallerError(f"{label}.kind must be one of {SOURCE_KINDS}", code="recipe-invalid")
    kind = src["kind"]
    if kind == "casc":
        _only(src, {"kind", "product", "version", "build_number", "build_key", "cdn_key", "locale",
                    "build_config", "cdn_config", "files", "note"},
              {"kind", "product", "version", "build_number", "build_key", "cdn_key", "locale",
               "build_config", "cdn_config", "files"}, label)
        if src["product"] not in ("wow", "wowf", "wow_classic", "wow_classic_era"):
            raise InstallerError(f"{label}.product is not a known product code", code="recipe-invalid")
        _str(src["version"], f"{label}.version")
        _int(src["build_number"], f"{label}.build_number", 1)
        lower_hex(src["build_key"], 32, f"{label}.build_key")
        lower_hex(src["cdn_key"], 32, f"{label}.cdn_key")
        if src["locale"] not in LOCALE_MASKS:
            raise InstallerError(f"{label}.locale must be one of {sorted(LOCALE_MASKS)}", code="recipe-invalid")
        for cfg in ("build_config", "cdn_config"):
            c = _only(src[cfg], {"bytes", "sha256"}, {"bytes", "sha256"}, f"{label}.{cfg}")
            _int(c["bytes"], f"{label}.{cfg}.bytes", 1)
            lower_hex(c["sha256"], 64, f"{label}.{cfg}.sha256")
        names: set[str] = set()
        ids: set[int] = set()
        files = src["files"]
        if not isinstance(files, list) or not files:
            raise InstallerError(f"{label}.files must be a non-empty list", code="recipe-invalid")
        for i, f in enumerate(files):
            f = _only(f, {"file_data_id", "name", "bytes", "sha256", "role"},
                      {"file_data_id", "name", "bytes", "sha256"}, f"{label}.files[{i}]")
            _int(f["file_data_id"], f"{label}.files[{i}].file_data_id", 1)
            name = _str(f["name"], f"{label}.files[{i}].name")
            if name != Path(name).name or "/" in name or "\\" in name or name in (".", ".."):
                raise InstallerError(f"{label}.files[{i}].name must be a bare file name", code="recipe-invalid")
            _int(f["bytes"], f"{label}.files[{i}].bytes", 1)
            lower_hex(f["sha256"], 64, f"{label}.files[{i}].sha256")
            if name.casefold() in names or f["file_data_id"] in ids:
                raise InstallerError(f"{label}.files[{i}] duplicates a name or FileDataID", code="recipe-invalid")
            names.add(name.casefold())
            ids.add(f["file_data_id"])
    elif kind == "game-chain":
        _only(src, {"kind", "exclude_slots", "files", "note"}, {"kind", "files"}, label)
        for i, f in enumerate(src["files"]):
            f = _only(f, {"path", "sha256", "role"}, {"path", "sha256"}, f"{label}.files[{i}]")
            internal_path(f["path"], f"{label}.files[{i}].path")
            _hash_list(f["sha256"], f"{label}.files[{i}].sha256")
        for slot in src.get("exclude_slots", []):
            if not SLOT_RE.match(slot):
                raise InstallerError(f"{label}.exclude_slots has a bad slot name {slot!r}", code="recipe-invalid")
    else:  # user-supplied
        _only(src, {"kind", "files", "instructions"}, {"kind", "files", "instructions"}, label)
        _str(src["instructions"], f"{label}.instructions")
        names = set()
        for i, f in enumerate(src["files"]):
            f = _only(f, {"name", "bytes", "sha256", "origin"}, {"name", "bytes", "sha256"}, f"{label}.files[{i}]")
            rel = safe_rel(f["name"], f"{label}.files[{i}].name")
            _int(f["bytes"], f"{label}.files[{i}].bytes", 1)
            lower_hex(f["sha256"], 64, f"{label}.files[{i}].sha256")
            if rel.casefold() in names:
                raise InstallerError(f"{label}.files[{i}] duplicates a name", code="recipe-invalid")
            names.add(rel.casefold())
    return src


def _validate_byte_edits(edits: Any, label: str) -> None:
    """Same-length, sorted, non-overlapping byte edits, each guarded by the hash of the bytes it replaces."""
    if not isinstance(edits, list) or not edits:
        raise InstallerError(f"{label} must be a non-empty list", code="recipe-invalid")
    last_end = -1
    for j, e in enumerate(edits):
        e = _only(e, {"offset", "length", "expect_sha256", "new_b64"},
                  {"offset", "length", "expect_sha256", "new_b64"}, f"{label}[{j}]")
        off = _int(e["offset"], f"{label}[{j}].offset")
        ln = _int(e["length"], f"{label}[{j}].length", 1)
        if off < last_end:
            raise InstallerError(f"{label} must be sorted and non-overlapping", code="recipe-invalid")
        last_end = off + ln
        lower_hex(e["expect_sha256"], 64, f"{label}[{j}].expect_sha256")
        new = _b64(e["new_b64"], f"{label}[{j}].new_b64")
        if len(new) != ln:
            raise InstallerError(f"{label}[{j}] must keep the length unchanged ({len(new)} != {ln})", code="recipe-invalid")
        if ln > MAX_EDIT_BYTES:
            raise InstallerError(f"{label}[{j}] is too large", code="recipe-invalid")


def _b64(value: Any, label: str) -> bytes:
    try:
        data = base64.b64decode(_str(value, label), validate=True)
    except InstallerError:
        raise
    except Exception:
        raise InstallerError(f"{label} is not valid base64", code="recipe-invalid") from None
    if len(data) > MAX_EDIT_BYTES:
        raise InstallerError(f"{label} is too large", code="recipe-invalid")
    return data


def _validate_steps(recipe: dict[str, Any], tools: dict[str, Any] | None) -> None:
    sources = recipe.get("sources", {})
    seen: set[str] = set()
    for i, step in enumerate(recipe.get("steps", [])):
        label = f"steps[{i}]"
        if not isinstance(step, dict) or step.get("op") not in STEP_OPS:
            raise InstallerError(f"{label}.op must be one of {STEP_OPS}", code="recipe-invalid")
        op = step["op"]
        sid = _str(step.get("id"), f"{label}.id")
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", sid) or sid in seen:
            raise InstallerError(f"{label}.id {sid!r} is invalid or duplicated", code="recipe-invalid")
        if op in ("casc-export", "game-extract", "user-source"):
            _only(step, {"id", "op", "source"}, {"id", "op", "source"}, label)
            src = sources.get(step["source"])
            want = {"casc-export": "casc", "game-extract": "game-chain", "user-source": "user-supplied"}[op]
            if not src or src["kind"] != want:
                raise InstallerError(f"{label}.source must name a {want!r} source", code="recipe-invalid")
        elif op == "run-tool":
            _only(step, {"id", "op", "tool", "inputs", "expect_outputs"}, {"id", "op", "tool", "inputs", "expect_outputs"}, label)
            if tools is not None and step["tool"] not in tools:
                raise InstallerError(f"{label}.tool {step['tool']!r} is not in tools/registry.json", code="recipe-invalid")
            inputs = step["inputs"]
            if not isinstance(inputs, dict):
                raise InstallerError(f"{label}.inputs must be an object of name -> step id", code="recipe-invalid")
            for name, ref in inputs.items():
                if ref not in seen:
                    raise InstallerError(f"{label}.inputs.{name} names no earlier step", code="recipe-invalid")
            outs = step["expect_outputs"]
            if not isinstance(outs, list) or not outs:
                raise InstallerError(f"{label}.expect_outputs must list the files the tool must produce", code="recipe-invalid")
            for j, o in enumerate(outs):
                o = _only(o, {"path", "sha256"}, {"path", "sha256"}, f"{label}.expect_outputs[{j}]")
                safe_rel(o["path"], f"{label}.expect_outputs[{j}].path")
                lower_hex(o["sha256"], 64, f"{label}.expect_outputs[{j}].sha256")
        elif op == "binary-edit":
            _only(step, {"id", "op", "input", "output", "expect_input_sha256", "edits", "append_b64", "expect_output_sha256"},
                  {"id", "op", "input", "output", "expect_input_sha256", "expect_output_sha256"}, label)
            parse_ref(step["input"], f"{label}.input", seen)
            safe_rel(step["output"], f"{label}.output")
            _hash_list(step["expect_input_sha256"], f"{label}.expect_input_sha256")
            lower_hex(step["expect_output_sha256"], 64, f"{label}.expect_output_sha256")
            if "edits" in step:
                _validate_byte_edits(step["edits"], f"{label}.edits")
            if "append_b64" in step:
                _b64(step["append_b64"], f"{label}.append_b64")
            if "edits" not in step and "append_b64" not in step:
                raise InstallerError(f"{label} needs edits and/or append_b64", code="recipe-invalid")
        else:  # dbc-edit
            _only(step, {"id", "op", "input", "output", "expect_input_sha256", "edits", "expect_output_sha256"},
                  {"id", "op", "input", "output", "expect_input_sha256", "edits", "expect_output_sha256"}, label)
            parse_ref(step["input"], f"{label}.input", seen)
            safe_rel(step["output"], f"{label}.output")
            _hash_list(step["expect_input_sha256"], f"{label}.expect_input_sha256")
            lower_hex(step["expect_output_sha256"], 64, f"{label}.expect_output_sha256")
            if not isinstance(step["edits"], list) or not step["edits"]:
                raise InstallerError(f"{label}.edits must be a non-empty list", code="recipe-invalid")
            for j, e in enumerate(step["edits"]):
                e = _only(e, {"row_id", "column", "type", "expect_before", "value"},
                          {"row_id", "column", "type", "expect_before", "value"}, f"{label}.edits[{j}]")
                _int(e["row_id"], f"{label}.edits[{j}].row_id")
                _int(e["column"], f"{label}.edits[{j}].column")
                if e["type"] not in ("string", "uint32"):
                    raise InstallerError(f"{label}.edits[{j}].type must be string or uint32", code="recipe-invalid")
                for key in ("expect_before", "value"):
                    if e["type"] == "string" and not isinstance(e[key], str):
                        raise InstallerError(f"{label}.edits[{j}].{key} must be a string", code="recipe-invalid")
                    if e["type"] == "uint32":
                        _int(e[key], f"{label}.edits[{j}].{key}")
        seen.add(sid)


def _validate_outputs(recipe: dict[str, Any]) -> list[str]:
    outputs = _only(recipe.get("outputs", {}), {"mpq_files", "exe_patches"}, set(), "outputs")
    steps = {s["id"] for s in recipe.get("steps", [])}
    slots: list[str] = []
    seen: set[tuple[str, str]] = set()
    for i, o in enumerate(outputs.get("mpq_files", [])):
        label = f"outputs.mpq_files[{i}]"
        o = _only(o, {"slot", "path", "from", "sha256"}, {"slot", "path", "from", "sha256"}, label)
        if o["slot"] not in WRITABLE_SLOTS:
            raise InstallerError(f"{label}.slot must be one of {WRITABLE_SLOTS}", code="recipe-invalid")
        path = internal_path(o["path"], f"{label}.path")
        step, rel = parse_ref(o["from"], f"{label}.from", steps)
        if not rel:
            raise InstallerError(f"{label}.from must name a file", code="recipe-invalid")
        lower_hex(o["sha256"], 64, f"{label}.sha256")
        key = (o["slot"], path.casefold())
        if key in seen:
            raise InstallerError(f"{label} duplicates {path} in {o['slot']}", code="recipe-invalid")
        seen.add(key)
        if o["slot"] not in slots:
            slots.append(o["slot"])
    for i, p in enumerate(outputs.get("exe_patches", [])):
        label = f"outputs.exe_patches[{i}]"
        p = _only(p, {"target", "expect_exe_sha256", "result_exe_sha256", "edits", "append_b64"},
                  {"target", "expect_exe_sha256", "result_exe_sha256", "edits"}, label)
        if p["target"] != EXE_NAME:
            raise InstallerError(f"{label}.target must be {EXE_NAME}", code="recipe-invalid")
        lower_hex(p["expect_exe_sha256"], 64, f"{label}.expect_exe_sha256")
        lower_hex(p["result_exe_sha256"], 64, f"{label}.result_exe_sha256")
        _validate_byte_edits(p["edits"], f"{label}.edits")
        if "append_b64" in p:
            _b64(p["append_b64"], f"{label}.append_b64")
    return slots


def _validate_acceptance(recipe: dict[str, Any]) -> None:
    for i, a in enumerate(recipe.get("acceptance", [])):
        label = f"acceptance[{i}]"
        if not isinstance(a, dict) or a.get("check") not in ("mpq-has", "dbc-cell", "exe-sha256", "slot-sha256"):
            raise InstallerError(f"{label}.check must be mpq-has, dbc-cell, slot-sha256 or exe-sha256", code="recipe-invalid")
        if a["check"] == "mpq-has":
            _only(a, {"check", "slot", "path", "sha256"}, {"check", "slot", "path", "sha256"}, label)
            internal_path(a["path"], label + ".path")
            lower_hex(a["sha256"], 64, label + ".sha256")
        elif a["check"] == "dbc-cell":
            _only(a, {"check", "slot", "path", "row_id", "column", "type", "equals"},
                  {"check", "slot", "path", "row_id", "column", "type", "equals"}, label)
            internal_path(a["path"], label + ".path")
        elif a["check"] == "slot-sha256":
            _only(a, {"check", "slot", "equals"}, {"check", "slot", "equals"}, label)
            lower_hex(a["equals"], 64, label + ".equals")
        else:
            _only(a, {"check", "equals"}, {"check", "equals"}, label)
            lower_hex(a["equals"], 64, label + ".equals")
        if a["check"] != "exe-sha256" and a["slot"] not in WRITABLE_SLOTS:
            raise InstallerError(f"{label}.slot must be one of {WRITABLE_SLOTS}", code="recipe-invalid")


def validate_recipe(raw: Any, path: Path | None = None, tools: dict[str, Any] | None = None) -> Recipe:
    where = f" ({path.name})" if path else ""
    try:
        recipe = _only(raw, TOP_KEYS, {"schema", "id", "version", "title", "description", "status"}, "recipe")
        if recipe["schema"] != SCHEMA:
            raise InstallerError(f"Unsupported recipe schema {recipe['schema']!r}; this installer reads {SCHEMA}",
                                 "Update the installer or the recipe.", "recipe-schema")
        if not ID_RE.match(recipe["id"]):
            raise InstallerError("recipe id must be 3-64 characters of a-z, 0-9 and '-'", code="recipe-invalid")
        if not VERSION_RE.match(recipe["version"]):
            raise InstallerError("recipe version must look like 1.0.0", code="recipe-invalid")
        _str(recipe["title"], "title")
        _str(recipe["description"], "description")
        if recipe["status"] not in STATUSES:
            raise InstallerError(f"status must be one of {STATUSES}", code="recipe-invalid")
        if recipe["status"] == "unavailable":
            _str(recipe.get("unavailable_reason"), "unavailable_reason")
            if recipe.get("default"):
                raise InstallerError("an unavailable recipe cannot be default", code="recipe-invalid")
        elif recipe.get("unavailable_reason"):
            raise InstallerError("unavailable_reason is only valid with status 'unavailable'", code="recipe-invalid")
        if "default" in recipe and not isinstance(recipe["default"], bool):
            raise InstallerError("default must be true or false", code="recipe-invalid")
        requires = _only(recipe.get("requires", {}), {"game", "tools", "disk_mb"}, set(), "requires")
        if "game" in requires:
            game = _only(requires["game"], {"exe_sha256", "versions"}, {"exe_sha256"}, "requires.game")
            game["exe_sha256"] = _hash_list(game["exe_sha256"], "requires.game.exe_sha256")
        for name, src in recipe.get("sources", {}).items():
            _validate_source(name, src)
        slots: list[str] = []
        if recipe["status"] != "unavailable":
            if not recipe.get("steps") and not recipe.get("outputs"):
                raise InstallerError("a recipe that is not unavailable needs steps/outputs", code="recipe-invalid")
            _validate_steps(recipe, tools)
            slots = _validate_outputs(recipe)
            if not recipe.get("outputs", {}).get("mpq_files") and not recipe.get("outputs", {}).get("exe_patches"):
                raise InstallerError("outputs must declare at least one mpq file or exe patch", code="recipe-invalid")
            _validate_acceptance(recipe)
        return Recipe(raw=recipe, path=path, slots=slots)
    except InstallerError as exc:
        exc.message = f"Recipe{where}: {exc.message}"
        raise


def load_recipe(path: Path, tools: dict[str, Any] | None = None) -> Recipe:
    return validate_recipe(read_json(path, 4 * 1024 * 1024), path, tools)


def load_recipes(folder: Path, tools: dict[str, Any] | None = None) -> dict[str, Recipe]:
    recipes: dict[str, Recipe] = {}
    for file in sorted(folder.glob("*.json")):
        recipe = load_recipe(file, tools)
        if recipe.id in recipes:
            raise InstallerError(f"Two recipes share the id {recipe.id}: {file.name}", code="recipe-invalid")
        recipes[recipe.id] = recipe
    return recipes


def casc_catalog(source: dict[str, Any]) -> dict[str, Any]:
    """The exporter catalog (``retail-mace-silver.json`` shape) for one casc source."""
    return {
        "schema_version": 1, "product": source["product"], "version": source["version"],
        "build_key": source["build_key"], "cdn_key": source["cdn_key"], "build_number": source["build_number"],
        "locale_mask": LOCALE_MASKS[source["locale"]], "locale": source["locale"],
        "variant": "installer recipe",
        "assets": [{"file_data_id": f["file_data_id"], "output": f["name"], "role": f.get("role", "file"),
                    "bytes": f["bytes"], "sha256": f["sha256"]} for f in source["files"]],
        "build_config": source["build_config"], "cdn_config": source["cdn_config"],
    }
