"""check / plan / build orchestration. ``build`` never touches the game folder."""
from __future__ import annotations

import base64
import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from . import detect, mpq, txn
from .dbc import Dbc
from .recipe import Recipe, WRITABLE_SLOTS, load_recipes, internal_path
from .steps import (Context, load_registry, resolve_tool, run_recipe_steps, casc_preflight, check_casc_dll, ref_path)
from .util import (InstallerError, Progress, file_record, heavy_lock, lower_priority, new_id, read_json,
                   sha256_bytes, sha256_file, utc_now, write_json_atomic)

HERE = Path(__file__).resolve().parent.parent
BUILD_SCHEMA = "as-build/1"
SLOT_REL = {"patch-0.MPQ": "Data/patch-0.MPQ", "patch-1.MPQ": "Data/patch-1.MPQ"}
EXE_REL = "AzerothSurvivors.exe"
FOREIGN_POLICIES = ("refuse", "merge", "replace")


def recipes_dir() -> Path:
    return HERE / "recipes"


def load_catalog(extra_dirs: list[Path] | None = None) -> dict[str, Recipe]:
    tools = load_registry()
    out = load_recipes(recipes_dir(), tools)
    for folder in extra_dirs or []:
        for rid, rec in load_recipes(Path(folder), tools).items():
            if rid in out:
                raise InstallerError(f"Recipe id {rid} exists in both the built-in set and {folder}", code="recipe-invalid")
            out[rid] = rec
    return out


def select_recipes(catalog: dict[str, Recipe], ids: list[str] | None, include_experimental: bool) -> list[Recipe]:
    if ids:
        chosen = []
        for rid in ids:
            if rid not in catalog:
                raise InstallerError(f"Unknown recipe {rid!r}", f"Available: {', '.join(sorted(catalog))}", "recipe-unknown")
            rec = catalog[rid]
            if not rec.available:
                raise InstallerError(f"Recipe {rid} is unavailable: {rec.raw['unavailable_reason']}", code="recipe-unavailable")
            if rec.status == "experimental" and not include_experimental:
                raise InstallerError(f"Recipe {rid} is experimental.", "Add --allow-experimental to include it.", "recipe-experimental")
            chosen.append(rec)
        return chosen
    return [r for r in catalog.values() if r.available and r.raw.get("default")]


def _foreign_check(policy: str) -> None:
    if policy not in FOREIGN_POLICIES:
        raise InstallerError(f"--foreign-slots must be one of {FOREIGN_POLICIES}", code="usage")


def _slot_files(recipes: list[Recipe], priority: list[str] | None = None,
                resolved: list[dict[str, Any]] | None = None) -> dict[str, dict[str, tuple[Recipe, dict[str, Any]]]]:
    """Merge every recipe's mpq_files per slot.

    Two recipes writing the same path with identical bytes are fine. With different bytes it is an error, unless both
    recipes appear in ``priority`` (highest first): then the higher one wins and the choice is appended to ``resolved``.
    """
    rank = {rid: i for i, rid in enumerate(priority or [])}
    slots: dict[str, dict[str, tuple[Recipe, dict[str, Any]]]] = {}
    for rec in recipes:
        for f in rec.mpq_files:
            files = slots.setdefault(f["slot"], {})
            key = f["path"].casefold()
            if key in files and files[key][1]["sha256"] != f["sha256"]:
                old_rec, old_f = files[key]
                if old_rec.id in rank and rec.id in rank:
                    keep, drop = (old_rec, rec) if rank[old_rec.id] < rank[rec.id] else (rec, old_rec)
                    if resolved is not None:
                        resolved.append({"slot": f["slot"], "path": f["path"], "winner": keep.id, "loser": drop.id,
                                         "winner_sha256": (old_f if keep is old_rec else f)["sha256"],
                                         "loser_sha256": (f if keep is old_rec else old_f)["sha256"]})
                    if keep is old_rec:
                        continue
                else:
                    raise InstallerError(
                        f"Recipes {old_rec.id} and {rec.id} both write {f['path']} to {f['slot']} with different content "
                        f"({old_f['sha256'][:10]}... vs {f['sha256'][:10]}...)",
                        "Choose one of them, or list both in the profile's `priority` (highest first) to pick a winner.", "recipe-conflict")
            files[key] = (rec, f)
    return slots


def build_plan(*, game_root: Path, client_root: Path | None, recipes: list[Recipe], foreign: str = "refuse",
               casc_dll: Path | None = None, user_sources: dict[str, Path] | None = None,
               preflight_client: bool = True, work: Path | None = None, priority: list[str] | None = None) -> dict[str, Any]:
    """Dry run: nothing is written. ``blockers`` must be empty before build/apply."""
    _foreign_check(foreign)
    plan: dict[str, Any] = {"schema": "as-plan/1", "blockers": [], "warnings": [], "recipes": [], "targets": [], "writes": False}
    blockers, warnings = plan["blockers"], plan["warnings"]

    def block(code: str, message: str, hint: str | None = None) -> None:
        blockers.append({"code": code, "message": message, "hint": hint})

    try:
        game = detect.detect_game(game_root)
    except InstallerError as exc:
        block(exc.code, exc.message, exc.hint)
        return plan
    plan["game"] = {k: game[k] for k in ("version", "label", "exe_sha256", "known", "modified_by_installer_recipe")}
    if not game["known"]:
        warnings.append("Game build is not in known_builds.json; recipes that pin game data will verify hashes themselves.")
    clients: list[dict[str, Any]] = []
    needs_client = any(s["kind"] == "casc" for r in recipes for s in r.sources.values())
    if needs_client:
        if client_root is None:
            block("client-missing", "A recipe needs your WoW client but --client-root was not given.",
                  "Pass --client-root <folder with .build.info>.")
        else:
            try:
                clients = detect.detect_clients(client_root)
            except InstallerError as exc:
                block(exc.code, exc.message, exc.hint)
        if casc_dll is not None or needs_client:
            try:
                check_casc_dll(casc_dll)
            except InstallerError as exc:
                block(exc.code, exc.message, exc.hint)
    state = txn.load_state(game_root)
    for rec in recipes:
        info: dict[str, Any] = {"id": rec.id, "version": rec.version, "status": rec.status, "title": rec.title, "checks": []}
        accepted = rec.accepted_exe_hashes()
        if accepted and game["exe_sha256"] not in accepted:
            block("game-build", f"{rec.id}: this game build ({game['label']}) is not one the recipe supports.",
                  "Update/downgrade to a supported build or use a newer recipe.")
        for sid, src in rec.sources.items():
            if src["kind"] == "casc":
                match = detect.client_matches_source(clients, src) if clients else None
                info["checks"].append({"source": sid, "kind": "casc", "need": f"{src['product']} {src['version']} {src['locale']}",
                                       "client_match": bool(match)})
                if clients and not match:
                    block("client-build-mismatch", f"{rec.id}: your client has no {src['product']} build {src['version']} ({src['locale']}).",
                          "Found: " + (", ".join(f"{c['flavor']} {c['version']}" for c in clients) or "nothing"))
            elif src["kind"] == "user-supplied":
                folder = (user_sources or {}).get(sid)
                ok = folder is not None
                info["checks"].append({"source": sid, "kind": "user-supplied", "provided": ok})
                if not ok:
                    block("user-source-missing", f"{rec.id}: needs files you obtain yourself ({sid}).", src["instructions"])
        for p in rec.exe_patches:
            if game["exe_sha256"] != p["expect_exe_sha256"]:
                block("exe-mismatch", f"{rec.id}: the executable is not the build this EXE patch expects.",
                      f"Expected {p['expect_exe_sha256'][:12]}..., found {game['exe_sha256'][:12]}...")
        plan["recipes"].append(info)
    # slot composition and ownership
    resolved: list[dict[str, Any]] = []
    try:
        slots = _slot_files(recipes, priority, resolved)
    except InstallerError as exc:
        block(exc.code, exc.message, exc.hint)
        slots = {}
    plan["conflicts_resolved"] = resolved
    for c in resolved:
        warnings.append(f"{c['path']} in {c['slot']}: {c['winner']} wins over {c['loser']} (profile priority)")
    for slot, files in slots.items():
        rel = SLOT_REL[slot]
        cls = txn.classify_target(game_root, rel, state)
        entry: dict[str, Any] = {"rel": rel, "kind": "slot", "current": cls["kind"], "current_sha256": cls["sha256"],
                                 "files_from_recipes": len(files), "backup": cls["kind"] != "absent"}
        if cls["kind"] == "absent":
            entry["mode"] = "create"
        elif cls["kind"] == "installer-owned":
            entry["mode"] = "merge-owned"
        else:
            entry["mode"] = foreign
            if foreign == "refuse":
                block("foreign-slot", f"{rel} already exists and was not installed by this installer "
                                      f"(sha256 {cls['sha256'][:12]}..., {cls['bytes']} bytes).",
                      "Re-run with --foreign-slots merge (keeps its files, adds ours) or replace (backed up, then overwritten).")
        if entry["mode"] in ("merge", "merge-owned"):
            try:
                with mpq.MpqArchive(Path(game_root) / rel) as arc:
                    names = arc.names()
                entry["existing_files"] = len(names)
                entry["overrides"] = sorted(n for n in names if n.casefold() in files)
            except InstallerError as exc:
                block("merge-unsupported", f"Cannot merge into {rel}: {exc.message}", "Use --foreign-slots replace, or keep it and skip these recipes.")
        plan["targets"].append(entry)
    exe_patches = [p for r in recipes for p in r.exe_patches]
    if exe_patches:
        plan["targets"].append({"rel": EXE_REL, "kind": "exe-patch", "current": "game-exe", "current_sha256": game["exe_sha256"],
                                "edits": sum(len(p["edits"]) for p in exe_patches), "backup": True, "mode": "patch-copy"})
        for problem in exe_composition_problems(exe_patches, [r.id for r in recipes for _ in r.exe_patches]):
            block("exe-conflict", problem, "Choose recipes whose EXE edits do not overlap, and at most one that extends the file.")
    if not plan["targets"]:
        block("nothing-to-do", "The selection changes nothing.", None)
    # disk and process state
    game_dir = Path(game_root)
    need = 64 << 20
    for t in plan["targets"]:
        live = game_dir / t["rel"]
        if live.exists():
            need += live.stat().st_size * 2
    if exe_patches:
        need += (game_dir / EXE_REL).stat().st_size * 3
    free = detect.free_bytes(game_dir)
    plan["disk"] = {"free_mib": free >> 20, "needed_estimate_mib": need >> 20}
    if free < need:
        block("disk-space", f"Need about {need >> 20} MiB free on the game's drive; {free >> 20} MiB available.", "Free up space.")
    running = detect.game_running(game_dir)
    plan["game_running"] = bool(running)
    if running:
        block("game-running", "AzerothSurvivors.exe is running from this folder.", "Exit the game before build/apply (plan itself is safe).")
    if work is not None:
        wfree = detect.free_bytes(Path(work))
        plan["work_free_mib"] = wfree >> 20
        if wfree < (512 << 20):
            warnings.append("Less than 512 MiB free where the work folder will be created.")
    plan["backups"] = [t["rel"] for t in plan["targets"] if t.get("backup")]
    plan["ok"] = not blockers
    return plan


def exe_composition_problems(patches: list[dict[str, Any]], names: list[str] | None = None) -> list[str]:
    """EXE patches from several recipes compose only if they share a base, never overlap and at most one appends."""
    problems: list[str] = []
    label = (lambda i: names[i]) if names and len(names) == len(patches) else (lambda i: f"EXE patch {i + 1}")
    spans: list[tuple[int, int, int]] = []
    appends = 0
    bases = {p["expect_exe_sha256"] for p in patches}
    if len(bases) > 1:
        problems.append("EXE patches in this selection were written for different base executables: "
                        + ", ".join(label(i) for i in range(len(patches))) + ".")
    for pi, patch in enumerate(patches):
        for e in patch["edits"]:
            spans.append((e["offset"], e["offset"] + e["length"], pi))
        if patch.get("append_b64"):
            appends += 1
    spans.sort()
    for (s0, e0, p0), (s1, e1, p1) in zip(spans, spans[1:]):
        if s1 < e0 and p0 != p1:
            problems.append(f"{label(p0)} and {label(p1)} both edit EXE bytes {s1}-{min(e0, e1)} (byte intervals cannot be resolved by priority).")
            break
    if appends > 1:
        problems.append("More than one selected recipe appends to the EXE (PE section changes cannot be combined): "
                        + ", ".join(label(i) for i, pt in enumerate(patches) if pt.get("append_b64")) + ".")
    return problems


def _apply_exe_patches(src_exe: Path, patches: list[dict[str, Any]], dest: Path) -> dict[str, Any]:
    original = src_exe.read_bytes()
    base = hashlib.sha256(original).hexdigest()
    problems = exe_composition_problems(patches)
    if problems:
        raise InstallerError(problems[0], code="exe-conflict")
    data = bytearray(original)
    changed = 0
    append = b""
    for pi, patch in enumerate(patches):
        if base != patch["expect_exe_sha256"]:
            raise InstallerError(f"EXE patch {pi + 1} expects {patch['expect_exe_sha256'][:12]}..., but the EXE is {base[:12]}...",
                                 code="exe-mismatch")
        for edit in patch["edits"]:
            region = bytes(original[edit["offset"]:edit["offset"] + edit["length"]])
            if len(region) != edit["length"] or sha256_bytes(region) != edit["expect_sha256"]:
                raise InstallerError(f"EXE bytes at {edit['offset']} are not the original this patch expects",
                                     "The executable differs from the recipe's base build.", "exe-region")
            new = base64.b64decode(edit["new_b64"])
            data[edit["offset"]:edit["offset"] + edit["length"]] = new
            changed += sum(1 for a, b in zip(region, new) if a != b)
        if patch.get("append_b64"):
            append = base64.b64decode(patch["append_b64"])
        if len(patches) == 1:
            check = bytearray(data) + append
            if hashlib.sha256(check).hexdigest() != patch["result_exe_sha256"]:
                raise InstallerError("Patched EXE does not match the recipe's expected result hash", code="exe-result")
    data += append
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(bytes(data))
    return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "bytes_changed": changed + len(append),
            "combined_patches": len(patches)}


def _native_readback(archive: Path, expected: dict[str, str], inspector: Path | None) -> dict[str, Any]:
    """Optional: ask the project's native MPQ reader to list the archive (skipped when absent)."""
    if inspector is None or not inspector.is_file():
        return {"status": "skipped", "reason": "mpq-inspect.exe not available"}
    done = subprocess.run([str(inspector), str(archive), "list"], capture_output=True, text=True, timeout=120, check=False)
    if done.returncode != 0:
        return {"status": "failed", "detail": (done.stderr or done.stdout)[-300:]}
    listed = {}
    for line in done.stdout.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and parts[0].isdigit():
            listed[parts[1].casefold()] = int(parts[0])
    missing = [p for p in expected if p.casefold() not in listed]
    return {"status": "passed" if not missing else "failed", "listed": len(listed), "missing": missing}


def _run_acceptance(rec: Recipe, slot_archives: dict[str, Path], exe_sha: str | None,
                    slot_info: dict[str, dict[str, Any]] | None = None, exe_combined: bool = False) -> list[dict[str, Any]]:
    results = []
    for a in rec.raw.get("acceptance", []):
        res = {"recipe": rec.id, "check": a["check"], "ok": False}
        try:
            if a["check"] == "mpq-has":
                with mpq.MpqArchive(slot_archives[a["slot"]]) as arc:
                    res["ok"] = sha256_bytes(arc.read(a["path"])) == a["sha256"]
            elif a["check"] == "dbc-cell":
                with mpq.MpqArchive(slot_archives[a["slot"]]) as arc:
                    dbc = Dbc(arc.read(a["path"]), a["path"])
                value = dbc.get(a["row_id"], a["column"], a["type"])
                res["ok"] = value == a["equals"]
                res["observed"] = value
            elif a["check"] == "slot-sha256":
                info = (slot_info or {}).get(a["slot"], {})
                if info.get("recipes") == {rec.id} and not info.get("merged"):
                    res["ok"] = sha256_file(slot_archives[a["slot"]]) == a["equals"]
                else:
                    res.update(ok=True, skipped="slot also holds files from other recipes or an existing archive")
            elif exe_combined:
                res.update(ok=True, skipped="EXE combines several recipes; the single-recipe hash does not apply")
            else:
                res["ok"] = exe_sha == a["equals"]
        except (KeyError, InstallerError) as exc:
            res["error"] = str(exc)
        results.append(res)
    return results


def build(*, game_root: Path, client_root: Path | None, recipes: list[Recipe], work: Path, foreign: str = "refuse",
          casc_dll: Path | None = None, user_sources: dict[str, Path] | None = None,
          heavy_lock_path: Path | None = None, heavy_lock_wait: float = 0.0, native_inspector: Path | None = None,
          progress: Progress | None = None, expect_slot_sha256: dict[str, str] | None = None,
          profile: dict[str, Any] | None = None, priority: list[str] | None = None) -> dict[str, Any]:
    """export -> convert -> pack -> verify into a NEW ``work`` folder. The game is only read."""
    progress = progress or Progress(False)
    work = Path(work).resolve()
    if work.exists():
        raise InstallerError(f"Work folder already exists: {work}", "Pass a new --work folder.", "work-exists")
    plan = build_plan(game_root=game_root, client_root=client_root, recipes=recipes, foreign=foreign, casc_dll=casc_dll,
                      user_sources=user_sources, work=work.parent if work.parent.exists() else None, priority=priority)
    if plan["blockers"]:
        first = plan["blockers"][0]
        raise InstallerError(f"Cannot build: {first['message']}", first.get("hint"), first["code"])
    for forbidden in (Path(game_root), Path(client_root) if client_root else None):
        if forbidden is not None and (work == forbidden.resolve() or forbidden.resolve() in work.parents):
            raise InstallerError("The work folder must be outside the game and client folders.", code="work-inside")
    game_root = Path(game_root)
    lower_priority()
    work.mkdir(parents=True)
    ctx = Context(work=work, game_root=game_root, client_root=Path(client_root) if client_root else None, casc_dll=casc_dll,
                  user_sources={k: Path(v) for k, v in (user_sources or {}).items()}, progress=progress,
                  tools_registry=load_registry())
    packer = resolve_tool(ctx.tools_registry, "mpq-packer")
    exe_before = sha256_file(game_root / EXE_REL)
    has_casc = any(s["kind"] == "casc" for r in recipes for s in r.sources.values())
    progress.say(f"building {', '.join(r.id for r in recipes)} in {work}")
    with heavy_lock(heavy_lock_path if has_casc else None, f"installer build: {', '.join(r.id for r in recipes)}", wait=heavy_lock_wait):
        if has_casc:
            for rec in recipes:
                for sid, src in rec.sources.items():
                    if src["kind"] == "casc":
                        progress.say(f"verifying client metadata for {rec.id}/{sid}")
                        casc_preflight(ctx, rec, sid)
        for rec in recipes:
            run_recipe_steps(ctx, rec)
    state = txn.load_state(game_root)
    artifacts = work / "artifacts"
    artifacts.mkdir()
    targets: list[dict[str, Any]] = []
    slot_archives: dict[str, Path] = {}
    slot_report: dict[str, Any] = {}
    slot_info: dict[str, dict[str, Any]] = {}
    resolved_conflicts: list[dict[str, Any]] = []
    for slot, files in _slot_files(recipes, priority, resolved_conflicts).items():
        rel = SLOT_REL[slot]
        tree = work / "_trees" / slot
        tree.mkdir(parents=True)
        cls = txn.classify_target(game_root, rel, state)
        merged_names: dict[str, str] = {}
        if cls["kind"] != "absent" and (cls["kind"] == "installer-owned" or foreign == "merge"):
            progress.say(f"merging the current {rel} into the new archive")
            for name, data in mpq.read_all(game_root / rel).items():
                if name.casefold() == "(listfile)":
                    continue
                dest = tree.joinpath(*name.split("\\"))
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
                merged_names[name.casefold()] = name
        for key, (rec, f) in files.items():
            step, rel_in = f["from"].split(":", 1)
            src = ref_path(ctx, rec, f["from"], f"{rec.id} output {f['path']}")
            if sha256_file(src) != f["sha256"]:
                raise InstallerError(f"{rec.id}: {f['path']} does not match its declared SHA-256", "The recipe's pinned output is stale.",
                                     "output-hash")
            existing = merged_names.get(key)
            dest = tree.joinpath(*(existing or f["path"]).split("\\"))
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)
        out = artifacts / slot
        progress.say(f"packing {slot}")
        mpq.pack_tree(packer, tree, out)
        expect: dict[str, str] = {}
        for p in tree.rglob("*"):
            if p.is_file():
                expect["\\".join(p.relative_to(tree).parts)] = sha256_file(p)
        with mpq.MpqArchive(out) as arc:
            names = set(arc.names())
            bad = [n for n, h in expect.items() if n not in names or sha256_bytes(arc.read(n)) != h]
            extra = sorted(names - set(expect))
        if bad or extra:
            raise InstallerError(f"Packed {slot} failed readback: mismatching={bad[:3]} unexpected={extra[:3]}", code="readback")
        rec_out = file_record(out)
        native = _native_readback(out, expect, native_inspector)
        if native["status"] == "failed":
            raise InstallerError(f"Native MPQ reader could not list the packed archive: {native}", code="native-readback")
        slot_archives[slot] = out
        slot_info[slot] = {"recipes": {rec.id for rec, _ in files.values()}, "merged": len(merged_names)}
        want = (expect_slot_sha256 or {}).get(slot)
        if want and not merged_names and rec_out["sha256"] != want:
            raise InstallerError(f"Packed {slot} is {rec_out['sha256'][:12]}..., the profile expects {want[:12]}...",
                                 "The sources or packer differ from the reviewed ones. Nothing was installed.", "profile-slot-hash")
        slot_report[slot] = {"recipes": sorted(r.id for r, _ in files.values()), "files": len(expect), "merged_existing": len(merged_names), "readback": "passed", "native_readback": native,
                             "contents": {k: v for k, v in sorted(expect.items())}}
        targets.append({"rel": rel, "artifact": f"artifacts/{slot}", "kind": "slot", **rec_out,
                        "expect_before": cls["sha256"], "before_kind": cls["kind"], "merge_base": cls["sha256"] if merged_names else None})
    exe_patches = [p for r in recipes for p in r.exe_patches]
    exe_sha: str | None = exe_before
    if exe_patches:
        progress.say("patching a copy of the executable")
        res = _apply_exe_patches(game_root / EXE_REL, exe_patches, artifacts / "AzerothSurvivors.exe")
        exe_sha = res["sha256"]
        targets.append({"rel": EXE_REL, "artifact": "artifacts/AzerothSurvivors.exe", "kind": "exe-patch", "bytes": res["bytes"],
                        "sha256": res["sha256"], "expect_before": exe_before, "before_kind": "game-exe",
                        "bytes_changed": res["bytes_changed"]})
    acceptance: list[dict[str, Any]] = []
    for rec in recipes:
        acceptance += _run_acceptance(rec, slot_archives, exe_sha, slot_info, exe_combined=len(exe_patches) > 1)
    failed = [a for a in acceptance if not a["ok"]]
    if failed:
        raise InstallerError(f"Acceptance checks failed: {failed[:3]}", "Nothing was installed.", "acceptance")
    shutil.rmtree(work / "_trees", ignore_errors=True)
    manifest = {
        "schema": BUILD_SCHEMA, "build_id": new_id(), "created": utc_now(), "game_exe_sha256": exe_before,
        "recipes": [{"id": r.id, "version": r.version, "recipe_sha256": sha256_file(r.path) if r.path else None} for r in recipes],
        "profile": profile["id"] if profile else None, "conflicts_resolved": resolved_conflicts, "foreign_policy": foreign, "targets": targets, "slots": slot_report, "acceptance": acceptance,
        "note": "Local build artifacts derived from the owner's own installs. Do not redistribute.",
    }
    write_json_atomic(work / "build.json", manifest)
    return manifest


def load_build(build_dir: Path) -> dict[str, Any]:
    path = Path(build_dir) / "build.json"
    if not path.is_file():
        raise InstallerError(f"{build_dir} has no build.json", "Run `build` first (or `apply` without --use-build).", "build-missing")
    data = read_json(path)
    if data.get("schema") != BUILD_SCHEMA:
        raise InstallerError("Unknown build manifest schema", code="build-invalid")
    return data
