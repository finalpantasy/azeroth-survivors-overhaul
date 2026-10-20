"""Step executors for recipes. Each step writes into ``work/<recipe-id>/<step-id>/``."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import dbc as dbcmod
from . import mpq
from .recipe import Recipe, casc_catalog, parse_ref
from .util import InstallerError, Progress, is_within, safe_rel, sha256_file, read_json

HERE = Path(__file__).resolve().parent.parent
CASC_DLL_SHA256 = "221833e9afa0d534a4e0e5679cabb24a827a8ac1560b6722acd6d500bfcc28b3"
CHILD_TIMEOUT = 300


@dataclass
class Context:
    work: Path
    game_root: Path | None = None
    client_root: Path | None = None
    casc_dll: Path | None = None
    user_sources: dict[str, Path] = field(default_factory=dict)
    progress: Progress = field(default_factory=lambda: Progress(False))
    tools_registry: dict[str, Any] = field(default_factory=dict)
    slots_excluded_default: tuple[str, ...] = ("patch-0.MPQ", "patch-1.MPQ")

    def step_dir(self, recipe: Recipe, step_id: str) -> Path:
        return self.work / recipe.id / step_id


def load_registry() -> dict[str, Any]:
    return read_json(HERE / "tools" / "registry.json")["tools"]


def resolve_tool(registry: dict[str, Any], name: str) -> Path:
    entry = registry.get(name)
    if entry is None:
        raise InstallerError(f"Tool {name!r} is not in tools/registry.json", code="tool-missing")
    tried = []
    for folder in entry["search"]:
        candidate = (HERE / folder / entry["script"]).resolve()
        tried.append(str(candidate))
        if candidate.is_file():
            if sha256_file(candidate) != entry["sha256"]:
                raise InstallerError(f"Tool {name} ({candidate.name}) does not match its pinned SHA-256",
                                     "The tool changed after review. Re-audit it, then refresh tools/registry.json.", "tool-hash")
            return candidate
    raise InstallerError(f"Tool {name} ({entry['script']}) was not found (looked in: {', '.join(tried)})",
                         "Use the complete installer bundle; do not move files out of it.", "tool-missing")


def ref_path(ctx: Context, recipe: Recipe, ref: str, label: str) -> Path:
    steps = {s["id"] for s in recipe.steps}
    step, rel = parse_ref(ref, label, steps)
    base = ctx.step_dir(recipe, step)
    path = (base / rel) if rel else base
    if not is_within(path, base) or not path.exists():
        raise InstallerError(f"{label}: {ref!r} does not exist (did step {step!r} run?)", code="step-output-missing")
    return path


def _json_from(stdout: str) -> dict[str, Any]:
    start = stdout.find("{")
    if start < 0:
        return {}
    try:
        value = json.loads(stdout[start:])
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _run(argv: list[str], timeout: int, what: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise InstallerError(f"{what} exceeded {timeout} seconds and was stopped.",
                             "Retry with a quieter machine; check antivirus exclusions for the work folder.", "step-timeout") from None
    except OSError as exc:
        raise InstallerError(f"{what} could not start: {exc}", code="step-failed") from exc


def check_casc_dll(path: Path | None) -> Path:
    if path is None:
        raise InstallerError("This recipe needs the audited offline CascLib.dll.",
                             "Pass --casc-dll <path> or set AS_CASC_DLL. Build it with tools/casc-offline/build.ps1 "
                             "(pinned CascLib source) or use the audited DLL described in the README.", "casc-dll-missing")
    path = Path(path)
    if not path.is_file():
        raise InstallerError(f"CascLib runtime not found: {path}", code="casc-dll-missing")
    digest = sha256_file(path)
    if digest != CASC_DLL_SHA256 and not _built_from_pinned_source(path, digest):
        raise InstallerError("CascLib runtime is not the audited offline build.",
                             f"Expected SHA-256 {CASC_DLL_SHA256}, or a DLL produced by tools/casc-offline/build.ps1 from this "
                             "repository (its build-logs record must match). Do not use another CascLib.",
                             "casc-dll-hash")
    return path.resolve()


def _built_from_pinned_source(path: Path, digest: str) -> bool:
    """True when `path` is <casc-offline>/build-x64/Release/CascLib.dll and a build record next to it proves that
    build_runner.py built exactly this file from the source pinned in source-manifest.json (MSVC output is not
    byte-reproducible, so the pinned hash alone would reject every rebuild)."""
    try:
        tool = path.resolve().parents[2]
        manifest = tool / "source-manifest.json"
        logs = tool / "build-logs"
        if path.resolve().parent.name != "Release" or path.resolve().parents[1].name != "build-x64" or not manifest.is_file():
            return False
        manifest_hash = sha256_file(manifest)
        for record_path in sorted(logs.glob("build-record-*.json")):
            rec = json.loads(record_path.read_text(encoding="utf-8"))
            if (rec.get("status") == "build-success" and rec.get("dll_sha256") == digest
                    and rec.get("source_manifest_sha256") == manifest_hash):
                return True
    except (OSError, ValueError, IndexError):
        return False
    return False


def casc_preflight(ctx: Context, recipe: Recipe, source_id: str) -> dict[str, Any]:
    """Metadata-only check that the owner's client matches the recipe's pinned build (no payload reads)."""
    if ctx.client_root is None:
        raise InstallerError("--client-root is required for recipes with a CASC source", code="client-missing")
    source = recipe.sources[source_id]
    work = ctx.work / recipe.id
    work.mkdir(parents=True, exist_ok=True)
    catalog = work / f"{source_id}.catalog.json"
    catalog.write_text(json.dumps(casc_catalog(source), indent=2), encoding="utf-8")
    exporter = resolve_tool(ctx.tools_registry, "casc-export")
    resolve_tool(ctx.tools_registry, "casc-adapter")
    probe_out = work / f"{source_id}.preflight-output"
    done = _run([sys.executable, "-I", str(exporter), "--catalog", str(catalog), "--client-root", str(ctx.client_root),
                 "--output", str(probe_out)], 120, "CASC metadata preflight")
    payload = _json_from(done.stdout)
    if done.returncode != 0 or payload.get("status") != "preflight-passed":
        raise InstallerError(
            "Your client does not match the recipe's pinned build: " + (done.stderr.strip().splitlines() or ["preflight failed"])[-1],
            f"This recipe needs {source['product']} {source['version']} ({source['locale']}). Let the client update/downgrade is not "
            "supported; use a recipe for your build or mark it unsupported.", "client-build-mismatch")
    if probe_out.exists():
        raise InstallerError("CASC preflight unexpectedly wrote output", code="step-failed")
    return payload


def step_casc_export(ctx: Context, recipe: Recipe, step: dict[str, Any]) -> Path:
    source = recipe.sources[step["source"]]
    out = ctx.step_dir(recipe, step["id"])
    dll = check_casc_dll(ctx.casc_dll)
    if ctx.client_root is None:
        raise InstallerError("--client-root is required for recipes with a CASC source", code="client-missing")
    catalog = ctx.work / recipe.id / f"{step['source']}.catalog.json"
    catalog.parent.mkdir(parents=True, exist_ok=True)
    catalog.write_text(json.dumps(casc_catalog(source), indent=2), encoding="utf-8")
    exporter = resolve_tool(ctx.tools_registry, "casc-export")
    resolve_tool(ctx.tools_registry, "casc-adapter")
    ctx.progress.say(f"  exporting {len(source['files'])} file(s) from the local client (offline, read-only)")
    done = _run([sys.executable, "-I", str(exporter), "--catalog", str(catalog), "--client-root", str(ctx.client_root),
                 "--output", str(out), "--library", str(dll), "--extract"], CHILD_TIMEOUT, "CASC export")
    payload = _json_from(done.stdout)
    if done.returncode != 0 or payload.get("status") != "success" or payload.get("metadata_unchanged") is not True:
        detail = payload.get("error") or done.stderr.strip()[-600:] or "export failed"
        raise InstallerError(f"Offline CASC export failed: {detail}",
                             "Close the Blizzard launcher/game (they can lock CASC data), and make sure the client is fully updated.",
                             "casc-export-failed")
    if payload.get("progress_download_events"):
        raise InstallerError("The CASC reader reported a download event; refusing.", code="casc-export-failed")
    for f in source["files"]:
        path = out / f["name"]
        if not path.is_file() or path.stat().st_size != f["bytes"] or sha256_file(path) != f["sha256"]:
            raise InstallerError(f"Exported {f['name']} does not match the recipe's pinned size/hash",
                                 "Your client's file differs from the one the recipe was written for.", "casc-hash")
    return out


def step_game_extract(ctx: Context, recipe: Recipe, step: dict[str, Any]) -> Path:
    if ctx.game_root is None:
        raise InstallerError("--game-root is required for this recipe", code="game-missing")
    source = recipe.sources[step["source"]]
    exclude = source.get("exclude_slots", list(ctx.slots_excluded_default))
    chain = mpq.official_chain(ctx.game_root / "Data", exclude)
    if not chain:
        raise InstallerError("No official patch archives found in the game's Data folder", code="game-missing")
    out = ctx.step_dir(recipe, step["id"])
    reader = mpq.ChainReader(chain)
    try:
        for f in source["files"]:
            try:
                data, origin = reader.read(f["path"])
            except KeyError:
                raise InstallerError(f"{f['path']} is not in the game's official archives", code="game-data-missing") from None
            import hashlib
            digest = hashlib.sha256(data).hexdigest()
            if digest not in f["sha256"]:
                raise InstallerError(f"{f['path']} in {origin.name} has an unexpected hash ({digest[:12]}...)",
                                     "The game has been updated or modified since this recipe was written; "
                                     "check `detect` and use a recipe for your game version.", "game-data-mismatch")
            dest = out.joinpath(*f["path"].split("\\"))
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
    finally:
        reader.close()
    return out


def step_user_source(ctx: Context, recipe: Recipe, step: dict[str, Any]) -> Path:
    source = recipe.sources[step["source"]]
    folder = ctx.user_sources.get(step["source"])
    if folder is None:
        raise InstallerError(f"Recipe {recipe.id} needs files you must obtain yourself (source {step['source']!r}).",
                             source["instructions"] + f"  Then pass --source {step['source']}=<folder>.", "user-source-missing")
    out = ctx.step_dir(recipe, step["id"])
    out.mkdir(parents=True, exist_ok=True)
    for f in source["files"]:
        rel = safe_rel(f["name"], "user source file")
        src = (Path(folder) / rel)
        if not src.is_file() or not is_within(src, Path(folder)):
            raise InstallerError(f"Missing {rel} in {folder}", source["instructions"], "user-source-missing")
        if src.stat().st_size != f["bytes"] or sha256_file(src) != f["sha256"]:
            raise InstallerError(f"{rel} does not match the pinned size/hash", "Obtain exactly the pinned files.", "user-source-hash")
        dest = out / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
    return out


def step_run_tool(ctx: Context, recipe: Recipe, step: dict[str, Any]) -> Path:
    entry = ctx.tools_registry[step["tool"]]
    script = resolve_tool(ctx.tools_registry, step["tool"])
    out = ctx.step_dir(recipe, step["id"])
    argv = [sys.executable, "-I", str(script)]
    for token in entry.get("argv", []):
        while "{" in token:
            start = token.index("{")
            end = token.index("}", start)
            key = token[start + 1:end]
            kind, _, name = key.partition(":")
            if kind == "out":
                value = str(out)
            elif kind == "in":
                value = str(ctx.step_dir(recipe, step["inputs"][name]))
            elif kind == "tool":
                value = str(resolve_tool(ctx.tools_registry, name))
            else:
                raise InstallerError(f"Unknown placeholder {{{key}}} in tool {step['tool']}", code="tool-invalid")
            token = token[:start] + value + token[end + 1:]
        argv.append(token)
    ctx.progress.say(f"  running tool {step['tool']}")
    done = _run(argv, int(entry.get("timeout", 120)), f"Tool {step['tool']}")
    if done.returncode != 0:
        raise InstallerError(f"Tool {step['tool']} failed: {(done.stderr or done.stdout).strip()[-600:]}", code="tool-failed")
    for exp in step["expect_outputs"]:
        path = out / safe_rel(exp["path"], "expected output")
        if not path.is_file():
            raise InstallerError(f"Tool {step['tool']} did not produce {exp['path']}", code="tool-output-missing")
        if sha256_file(path) != exp["sha256"]:
            raise InstallerError(f"Tool {step['tool']} produced {exp['path']} with an unexpected hash",
                                 "The converter or its input changed; the recipe pins the exact reviewed output.", "tool-output-hash")
    return out


def step_dbc_edit(ctx: Context, recipe: Recipe, step: dict[str, Any]) -> Path:
    src = ref_path(ctx, recipe, step["input"], f"{step['id']}.input")
    data = src.read_bytes()
    import hashlib
    if hashlib.sha256(data).hexdigest() not in step["expect_input_sha256"]:
        raise InstallerError(f"{src.name} is not the DBC this recipe edits", "Game data differs from the recipe's expectation.",
                             "dbc-input-hash")
    new = dbcmod.apply_edits(data, step["edits"], src.name)
    if hashlib.sha256(new).hexdigest() != step["expect_output_sha256"]:
        raise InstallerError(f"Edited {src.name} does not match the recipe's expected output hash", code="dbc-output-hash")
    out = ctx.step_dir(recipe, step["id"])
    dest = out / safe_rel(step["output"], "dbc output")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(new)
    return out


def apply_byte_edits(data: bytes, edits: list[dict[str, Any]], what: str) -> bytes:
    """Apply same-length edits, each guarded by the hash of the bytes it replaces."""
    import base64
    import hashlib
    buf = bytearray(data)
    for e in edits:
        region = bytes(buf[e["offset"]:e["offset"] + e["length"]])
        if len(region) != e["length"] or hashlib.sha256(region).hexdigest() != e["expect_sha256"]:
            raise InstallerError(f"{what}: bytes at offset {e['offset']} are not the ones this recipe edits",
                                 "The input file differs from the version the recipe was written for.", "binary-region")
        buf[e["offset"]:e["offset"] + e["length"]] = base64.b64decode(e["new_b64"])
    return bytes(buf)


def step_binary_edit(ctx: Context, recipe: Recipe, step: dict[str, Any]) -> Path:
    import base64
    import hashlib
    src = ref_path(ctx, recipe, step["input"], f"{step['id']}.input")
    data = src.read_bytes()
    if hashlib.sha256(data).hexdigest() not in step["expect_input_sha256"]:
        raise InstallerError(f"{src.name} is not the file this recipe edits", "Obtain exactly the pinned source file.", "binary-input-hash")
    new = apply_byte_edits(data, step.get("edits", []), src.name)
    if "append_b64" in step:
        new += base64.b64decode(step["append_b64"])
    if hashlib.sha256(new).hexdigest() != step["expect_output_sha256"]:
        raise InstallerError(f"Edited {src.name} does not match the recipe's expected output hash", code="binary-output-hash")
    out = ctx.step_dir(recipe, step["id"])
    dest = out / safe_rel(step["output"], "binary output")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(new)
    return out


EXECUTORS = {
    "casc-export": step_casc_export,
    "game-extract": step_game_extract,
    "user-source": step_user_source,
    "run-tool": step_run_tool,
    "dbc-edit": step_dbc_edit,
    "binary-edit": step_binary_edit,
}


def run_recipe_steps(ctx: Context, recipe: Recipe) -> None:
    total = len(recipe.steps)
    for index, step in enumerate(recipe.steps, 1):
        out = ctx.step_dir(recipe, step["id"])
        if out.exists():
            raise InstallerError(f"Work folder already contains {out}", "Use a new --work folder.", "work-exists")
        out.parent.mkdir(parents=True, exist_ok=True)
        ctx.progress.step(index, total, f"{recipe.id}: {step['op']} ({step['id']})")
        EXECUTORS[step["op"]](ctx, recipe, step)
