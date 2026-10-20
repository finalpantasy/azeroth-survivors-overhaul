"""Command line: detect | recipes | check | plan | build | apply | rollback | status."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from . import __version__, detect, engine, mpq, profile as profmod, status as statmod, txn
from .steps import check_casc_dll, load_registry, resolve_tool
from .util import InstallerError, Progress, sha256_file

DEFAULT_LOCK = None


def _common(p: argparse.ArgumentParser, *, game: bool = True, client: bool = False, recipes: bool = False,
            build_opts: bool = False) -> None:
    if game:
        p.add_argument("--game-root", type=Path, required=True, help="folder containing AzerothSurvivors.exe")
    if client:
        p.add_argument("--client-root", type=Path, help="your World of Warcraft folder (contains .build.info)")
        p.add_argument("--casc-dll", type=Path, default=os.environ.get("AS_CASC_DLL"),
                       help="audited offline CascLib.dll (or set AS_CASC_DLL)")
    if recipes:
        p.add_argument("--recipe", action="append", default=[], metavar="ID", help="recipe id (repeatable); default: recipes marked default")
        p.add_argument("--profile", metavar="ID", help="use a profile (ordered recipe list) instead of --recipe")
        p.add_argument("--profiles-dir", action="append", type=Path, default=[], help="extra folder of profile JSON files")
        p.add_argument("--recipes-dir", action="append", type=Path, default=[], help="extra folder of recipe JSON files")
        p.add_argument("--allow-experimental", action="store_true", help="allow recipes with status 'experimental'")
        p.add_argument("--source", action="append", default=[], metavar="ID=DIR",
                       help="folder holding a user-supplied source's files (never downloaded by the installer)")
    if build_opts:
        p.add_argument("--foreign-slots", choices=engine.FOREIGN_POLICIES, default="refuse",
                       help="what to do with patch-0/1.MPQ files the installer did not create (default refuse)")
        p.add_argument("--heavy-lock", type=Path, help="coordination heavy.lock path to hold during CASC export")
        p.add_argument("--heavy-lock-wait", type=float, default=0.0, metavar="SECONDS",
                       help="wait up to this long for the heavy lock instead of failing at once")
        p.add_argument("--native-inspector", type=Path, help="optional mpq-inspect.exe for an extra readback check")
    p.add_argument("--json", action="store_true", help="machine-readable JSON on stdout")
    p.add_argument("--quiet", action="store_true", help="no progress on stderr")


def _sources(items: list[str]) -> dict[str, Path]:
    out = {}
    for item in items:
        key, sep, value = item.partition("=")
        if not sep or not key or not value:
            raise InstallerError(f"--source expects ID=DIR, got {item!r}", code="usage")
        out[key] = Path(value)
    return out


def _saves_default() -> Path | None:
    appdata = os.environ.get("APPDATA")
    if appdata and (Path(appdata) / "wenilla-rogue").is_dir():
        return Path(appdata) / "wenilla-rogue"
    return None


def _emit(args: argparse.Namespace, value: Any, text: str) -> None:
    print(json.dumps(value, indent=2, sort_keys=True) if args.json else text)


def _selected(args: argparse.Namespace):
    catalog = engine.load_catalog(args.recipes_dir)
    if getattr(args, "profile", None):
        if args.recipe:
            raise InstallerError("Use either --profile or --recipe, not both.", code="usage")
        profiles = profmod.load_profiles(args.profiles_dir)
        if args.profile not in profiles:
            raise InstallerError(f"Unknown profile {args.profile!r}", f"Available: {', '.join(sorted(profiles)) or 'none'}", "profile-unknown")
        return catalog, profmod.resolve_profile(profiles[args.profile], catalog, args.allow_experimental)
    return catalog, engine.select_recipes(catalog, args.recipe, args.allow_experimental)


def _profile_obj(args: argparse.Namespace):
    if getattr(args, "profile", None):
        return profmod.load_profiles(args.profiles_dir)[args.profile]
    return None


# ---- renderers ---------------------------------------------------------------

def _render_detect(d: dict[str, Any]) -> str:
    lines = [f"Game: {d['label']}  (exe sha256 {d['exe_sha256'][:16]}..., {d['exe_bytes']:,} bytes)"]
    if not d["known"]:
        lines.append("  This build is not in known_builds.json; recipes verify the data they need by hash.")
    lines.append("Archives in Data/: " + ", ".join(f"{a['name']} ({a['bytes'] >> 20} MiB)" for a in d["archives"]))
    for c in d.get("clients", []):
        lines.append(f"Client: {c['flavor']} {c['version']}  product={c['product']} locales={','.join(c['locales'])}")
    if d.get("official_archive_problems"):
        lines.append("Archives that differ from the official release: " + ", ".join(d["official_archive_problems"]))
    return "\n".join(lines)


def _render_plan(plan: dict[str, Any]) -> str:
    lines = ["Plan (dry run, nothing written):"]
    g = plan.get("game")
    if g:
        lines.append(f"  game: {g['label']}")
    for r in plan["recipes"]:
        lines.append(f"  recipe {r['id']} {r['version']} [{r['status']}]: {r['title']}")
    for t in plan["targets"]:
        extra = ""
        if t["kind"] == "slot":
            extra = f"{t['files_from_recipes']} file(s) from recipes; {t['current']} -> mode {t['mode']}"
            if t.get("overrides"):
                extra += f"; overrides {len(t['overrides'])} existing"
        else:
            extra = f"{t['edits']} byte-range edit(s) applied to a copy, then swapped in"
        lines.append(f"  change {t['rel']}: {extra}")
    lines.append("  backs up: " + (", ".join(plan.get("backups", [])) or "nothing"))
    if "disk" in plan:
        lines.append(f"  disk: {plan['disk']['free_mib']} MiB free, ~{plan['disk']['needed_estimate_mib']} MiB needed")
    for w in plan["warnings"]:
        lines.append(f"  warning: {w}")
    if plan["blockers"]:
        lines.append("BLOCKED:")
        for b in plan["blockers"]:
            lines.append(f"  - {b['message']}" + (f"\n    fix: {b['hint']}" if b.get("hint") else ""))
    else:
        lines.append("OK: no blockers. Run `apply` to build, verify and install.")
    return "\n".join(lines)


# ---- commands -----------------------------------------------------------------

def cmd_detect(args: argparse.Namespace) -> int:
    d = detect.detect_game(args.game_root, deep=args.deep)
    if args.client_root:
        d["clients"] = detect.detect_clients(args.client_root)
    _emit(args, d, _render_detect(d))
    return 0


def cmd_recipes(args: argparse.Namespace) -> int:
    catalog = engine.load_catalog(args.recipes_dir)
    rows = [{"id": r.id, "version": r.version, "status": r.status, "default": bool(r.raw.get("default")),
             "title": r.title, "slots": r.slots, "exe_patch": bool(r.exe_patches),
             "unavailable_reason": r.raw.get("unavailable_reason")} for r in catalog.values()]
    text = "\n".join(f"{r['id']:34} {r['version']:7} {r['status']:12} {r['title']}" +
                     (f"\n    unavailable: {r['unavailable_reason']}" if r["unavailable_reason"] else "") for r in rows)
    _emit(args, rows, text)
    return 0


def cmd_profiles(args: argparse.Namespace) -> int:
    profiles = profmod.load_profiles(args.profiles_dir)
    rows = [{"id": p.id, "title": p.title, "recipes": p.recipe_ids, "allow_experimental": p.allow_experimental} for p in profiles.values()]
    _emit(args, rows, "\n".join(f"{r['id']:28} {r['title']}\n    recipes: {', '.join(r['recipes'])}" for r in rows) or "no profiles")
    return 0


def cmd_integrate(args: argparse.Namespace) -> int:
    """Profile-driven install into a real game folder. Without --apply it is a plan only (nothing written)."""
    if not args.profile:
        raise InstallerError("integrate needs --profile <id>", "See `profiles`.", "usage")
    if not args.apply:
        _, recipes = _selected(args)
        plan = engine.build_plan(game_root=args.game_root, client_root=args.client_root, recipes=recipes, foreign=args.foreign_slots,
                                 casc_dll=args.casc_dll, user_sources=_sources(args.source),
                                 priority=_profile_obj(args).priority)
        saves = None if args.saves_dir == "none" else (Path(args.saves_dir) if args.saves_dir else _saves_default())
        plan["integrate"] = {
            "profile": args.profile, "mode": "plan-only",
            "saves_dir": str(saves) if saves else None,
            "saves_dir_found": bool(saves and saves.is_dir()),
            "config_dir": str(args.game_root / "benilla-config"), "config_dir_found": (args.game_root / "benilla-config").is_dir(),
            "will_copy_read_only_snapshot_to": str(txn.state_dir(args.game_root) / "transactions" / "<new id>" / "saves-snapshot"),
            "rollback_command": f'python as_installer.py rollback --game-root "{args.game_root}" --yes',
            "state": statmod.recipe_status(args.game_root, recipes),
        }
        states = [r["state"] for r in plan["integrate"]["state"]]
        if states and all(x == "installed" for x in states):
            plan["integrate"]["verdict"] = "already-installed: every recipe in this profile is present byte-exact; applying would change nothing"
        elif any(x in ("installed", "partial") for x in states):
            plan["integrate"]["verdict"] = "partly-installed: use --foreign-slots merge so existing slot files are kept and missing ones added"
        else:
            plan["integrate"]["verdict"] = "not-installed"
        if saves is None:
            plan["warnings"].append("No saves folder found (the default %APPDATA%\\wenilla-rogue is absent here): pass --saves-dir so "
                                    "characters.txt is hash-proved unchanged and copied; --saves-dir none skips both.")
        text = _render_plan(plan) + "\nVerdict: " + plan["integrate"]["verdict"] + "\nState by content (already installed vs this profile):\n" + "\n".join(
            f"  {r['id']}: {r['state']} ({r['matched']}/{r['of']})" for r in plan["integrate"]["state"])
        _emit(args, plan, text)
        return 0 if plan["ok"] else 1
    if not args.yes:
        raise InstallerError("integrate --apply needs --yes", "Review the plan first (run without --apply).", "confirm")
    return cmd_apply(args, snapshot_copy=True)


def cmd_check(args: argparse.Namespace) -> int:
    checks: list[dict[str, Any]] = []

    def add(name: str, status: str, detail: str, hint: str | None = None) -> None:
        checks.append({"name": name, "status": status, "detail": detail, "hint": hint})

    add("python", "ok" if sys.version_info >= (3, 11) else "fail", sys.version.split()[0], "Python 3.11+ is required")
    add("platform", "ok" if os.name == "nt" else "fail", sys.platform, "The game and CascLib runtime are Windows-only")
    reg = load_registry()
    for name in reg:
        try:
            add(f"tool:{name}", "ok", str(resolve_tool(reg, name)))
        except InstallerError as exc:
            add(f"tool:{name}", "fail", exc.message, exc.hint)
    try:
        catalog, recipes = _selected(args)
        add("recipes", "ok", f"{len(catalog)} loaded, {len(recipes)} selected")
    except InstallerError as exc:
        add("recipes", "fail", exc.message, exc.hint)
        recipes = []
    try:
        game = detect.detect_game(args.game_root)
        add("game", "ok" if game["known"] else "warn", game["label"],
            None if game["known"] else "Unknown build; recipes verify the data they use by hash")
    except InstallerError as exc:
        add("game", "fail", exc.message, exc.hint)
    needs_client = any(s["kind"] == "casc" for r in recipes for s in r.sources.values())
    if needs_client:
        try:
            check_casc_dll(args.casc_dll)
            add("casc-dll", "ok", str(args.casc_dll))
        except InstallerError as exc:
            add("casc-dll", "fail", exc.message, exc.hint)
        if args.client_root is None:
            add("client", "fail", "--client-root not given", "Pass your World of Warcraft folder")
        else:
            try:
                clients = detect.detect_clients(args.client_root)
                add("client", "ok", ", ".join(f"{c['flavor']} {c['version']}" for c in clients))
                for r in recipes:
                    for sid, s in r.sources.items():
                        if s["kind"] == "casc":
                            m = detect.client_matches_source(clients, s)
                            add(f"build:{r.id}/{sid}", "ok" if m else "fail", f"needs {s['product']} {s['version']} {s['locale']}",
                                None if m else "Your client build differs; this recipe fails closed")
            except InstallerError as exc:
                add("client", "fail", exc.message, exc.hint)
    for r in recipes:
        for sid, s in r.sources.items():
            if s["kind"] == "user-supplied":
                ok = sid in _sources(args.source)
                add(f"source:{r.id}/{sid}", "ok" if ok else "fail", "provided" if ok else "missing", None if ok else s["instructions"])
    if args.game_root.is_dir():
        running = detect.game_running(args.game_root)
        add("game-not-running", "fail" if running else "ok", "running from this folder" if running else "not running",
            "Exit the game first" if running else None)
        for rel in txn.ALLOWED_TARGETS:
            p = args.game_root / rel
            reason = detect.file_lock_state(p)
            if reason:
                add(f"lock:{rel}", "fail", reason, "Close whatever holds the file")
        free = detect.free_bytes(args.game_root) >> 20
        add("disk-space", "ok" if free > 2048 else "warn", f"{free} MiB free on the game's drive",
            None if free > 2048 else "Large recipes (terrain) need several GiB")
    lock = args.heavy_lock if hasattr(args, "heavy_lock") else None
    if lock and Path(lock).exists():
        add("heavy-lock", "warn", f"{lock} exists", "Another worker may be running heavy work")
    ok = all(c["status"] != "fail" for c in checks)
    text = "\n".join(f"[{c['status'].upper():4}] {c['name']}: {c['detail']}" + (f"\n         fix: {c['hint']}" if c.get("hint") and c["status"] != "ok" else "")
                     for c in checks) + f"\n{'READY' if ok else 'NOT READY'}"
    _emit(args, {"ok": ok, "checks": checks}, text)
    return 0 if ok else 1


def cmd_plan(args: argparse.Namespace) -> int:
    _, recipes = _selected(args)
    plan = engine.build_plan(game_root=args.game_root, client_root=args.client_root, recipes=recipes, foreign=args.foreign_slots,
                             casc_dll=args.casc_dll, user_sources=_sources(args.source))
    _emit(args, plan, _render_plan(plan))
    return 0 if plan.get("ok") else 1


def _build(args: argparse.Namespace, progress: Progress) -> dict[str, Any]:
    _, recipes = _selected(args)
    if not recipes:
        raise InstallerError("No recipes selected.", "Use --recipe <id> (see `recipes`).", "usage")
    prof = _profile_obj(args)
    return engine.build(game_root=args.game_root, client_root=args.client_root, recipes=recipes, work=args.work,
                        foreign=args.foreign_slots, casc_dll=args.casc_dll, user_sources=_sources(args.source),
                        heavy_lock_path=args.heavy_lock, heavy_lock_wait=args.heavy_lock_wait, native_inspector=args.native_inspector,
                        progress=progress, expect_slot_sha256=prof.expect_slot_sha256 if prof else None,
                        profile=prof.raw if prof else None, priority=prof.priority if prof else None)


def _render_build(b: dict[str, Any], work: Path) -> str:
    lines = [f"Build {b['build_id']} verified in {work}"]
    for t in b["targets"]:
        lines.append(f"  {t['rel']}: {t['bytes']:,} bytes sha256 {t['sha256'][:16]}...")
    lines.append(f"  acceptance checks passed: {len(b['acceptance'])}")
    return "\n".join(lines)


def cmd_build(args: argparse.Namespace) -> int:
    progress = Progress(not args.quiet)
    b = _build(args, progress)
    _emit(args, b, _render_build(b, args.work) + "\nNothing in the game folder was changed. Run `apply --use-build` to install it.")
    return 0


def cmd_apply(args: argparse.Namespace, *, snapshot_copy: bool = False) -> int:
    progress = Progress(not args.quiet)
    if args.use_build:
        build = engine.load_build(args.use_build)
        build_dir = args.use_build
    else:
        if args.work is None:
            raise InstallerError("--work <new folder> is required", code="usage")
        _, recipes = _selected(args)
        plan = engine.build_plan(game_root=args.game_root, client_root=args.client_root, recipes=recipes, foreign=args.foreign_slots,
                                 casc_dll=args.casc_dll, user_sources=_sources(args.source))
        if not args.json:
            print(_render_plan(plan), file=sys.stderr)
        if not plan["ok"]:
            return 1
    if not args.yes:
        if sys.stdin.isatty():
            if input("Proceed with build and install? Type 'yes': ").strip().lower() != "yes":
                print("Cancelled; nothing changed.", file=sys.stderr)
                return 1
        else:
            raise InstallerError("Refusing to install without confirmation.", "Re-run with --yes after reading the plan.", "confirm")
    if not args.use_build:
        build = _build(args, progress)
        build_dir = args.work
    saves = None if args.saves_dir == "none" else (Path(args.saves_dir) if args.saves_dir else _saves_default())
    receipt = txn.apply_build(build, build_dir, args.game_root, saves_dir=saves, progress=progress, snapshot_copy=snapshot_copy)
    text = (f"Installed and verified. Transaction {receipt['id']}\n"
            f"  receipt: {txn.state_dir(args.game_root) / 'transactions' / receipt['id'] / 'receipt.json'}\n"
            f"  undo with: rollback --game-root \"{args.game_root}\" --transaction {receipt['id']} --yes")
    _emit(args, receipt, text)
    return 0


def cmd_rollback(args: argparse.Namespace) -> int:
    progress = Progress(not args.quiet)
    saves = None if args.saves_dir == "none" else (Path(args.saves_dir) if args.saves_dir else _saves_default())
    res = txn.rollback(args.game_root, args.transaction, apply=args.yes, saves_dir=saves, progress=progress)
    verb = "Rolled back" if res["applied"] else "Rollback dry run (add --yes to perform)"
    text = f"{verb}: transaction {res['transaction']}\n" + "\n".join(f"  {p['rel']}: {p['action']}" for p in res["plan"])
    _emit(args, res, text)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    s = txn.status(args.game_root)
    catalog = engine.load_catalog(args.recipes_dir)
    s["recipes"] = statmod.recipe_status(args.game_root, [r for r in catalog.values() if r.available])
    s["running"] = bool(detect.game_running(args.game_root))
    lines = [f"Game folder: {s['game_root']}" + ("   (game is RUNNING)" if s["running"] else "")]
    lines.append("Recipes (checked by content, whether installed by hand or by this installer):")
    for r in s["recipes"]:
        lines.append(f"  {r['state']:10} {r['id']} {r['version']} [{r['status']}] {r['matched']}/{r['of']} -> {', '.join(r['targets'])}")
    for u in (r for r in catalog.values() if not r.available):
        lines.append(f"  unavailable {u.id}: {u.raw['unavailable_reason'][:100].rstrip()}...")
    for t in s["installed_targets"]:
        lines.append(f"  installer-managed {t['rel']}: {'intact' if t['intact'] else 'CHANGED since install'} "
                     f"(recipes: {', '.join(f'{k} {v}' for k, v in t['recipes'].items()) or '-'}; transaction {t['transaction']})")
    if not s["installed_targets"]:
        lines.append("  installer transactions: none")
    for t in s["transactions"]:
        lines.append(f"  transaction {t['id']}: {t['status']} [{', '.join(t['recipes'])}]")
    _emit(args, s, "\n".join(lines))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="as_installer", description=__doc__)
    p.add_argument("--version", action="version", version=f"as_installer {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    d = sub.add_parser("detect", help="identify the game build and your WoW client builds")
    _common(d, client=False)
    d.add_argument("--client-root", type=Path)
    d.add_argument("--deep", action="store_true", help="also hash the official archives (slow)")
    d.set_defaults(func=cmd_detect)

    r = sub.add_parser("recipes", help="list available recipes")
    r.add_argument("--recipes-dir", action="append", type=Path, default=[])
    r.add_argument("--json", action="store_true")
    r.set_defaults(func=cmd_recipes)

    c = sub.add_parser("check", help="dependencies, disk space, locks, running game")
    _common(c, client=True, recipes=True)
    c.add_argument("--heavy-lock", type=Path)
    c.set_defaults(func=cmd_check)

    pl = sub.add_parser("plan", help="dry run: what would change and what is backed up")
    _common(pl, client=True, recipes=True, build_opts=True)
    pl.set_defaults(func=cmd_plan)

    b = sub.add_parser("build", help="export, convert, pack and verify into a new work folder (game untouched)")
    _common(b, client=True, recipes=True, build_opts=True)
    b.add_argument("--work", type=Path, required=True, help="NEW folder for the build (outside game and client)")
    b.set_defaults(func=cmd_build)

    a = sub.add_parser("apply", help="build (or reuse a build), verify, back up and install transactionally")
    _common(a, client=True, recipes=True, build_opts=True)
    a.add_argument("--work", type=Path, help="NEW folder for the build")
    a.add_argument("--use-build", type=Path, help="install an already verified build folder instead of building")
    a.add_argument("--saves-dir", help="save folder to prove unchanged (default %%APPDATA%%/wenilla-rogue; 'none' to skip)")
    a.add_argument("--yes", action="store_true", help="confirm (required when not interactive)")
    a.set_defaults(func=cmd_apply)

    rb = sub.add_parser("rollback", help="restore a transaction's backups (refuses if files changed since)")
    _common(rb)
    rb.add_argument("--transaction", default="latest", help="transaction id (default: latest installed)")
    rb.add_argument("--saves-dir")
    rb.add_argument("--yes", action="store_true", help="actually roll back (otherwise dry run)")
    rb.set_defaults(func=cmd_rollback)

    s = sub.add_parser("status", help="which recipes are installed in a game folder, plus installer transactions")
    _common(s)
    s.add_argument("--recipes-dir", action="append", type=Path, default=[], help="extra folder of recipe JSON files")
    s.set_defaults(func=cmd_status)

    pr = sub.add_parser("profiles", help="list profiles (ordered recipe sets)")
    pr.add_argument("--profiles-dir", action="append", type=Path, default=[])
    pr.add_argument("--json", action="store_true")
    pr.set_defaults(func=cmd_profiles)

    ig = sub.add_parser("integrate", help="install a profile into a real game folder (plan only unless --apply --yes)")
    _common(ig, client=True, recipes=True, build_opts=True)
    ig.add_argument("--apply", action="store_true", help="build, back up everything, copy saves/config read-only, install")
    ig.add_argument("--work", type=Path, help="NEW folder for the build (needed with --apply)")
    ig.add_argument("--use-build", type=Path, help="install an already verified build folder")
    ig.add_argument("--saves-dir", help="save folder to prove unchanged and snapshot (default %%APPDATA%%/wenilla-rogue; 'none' to skip)")
    ig.add_argument("--yes", action="store_true")
    ig.set_defaults(func=cmd_integrate)
    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except InstallerError as exc:
        if getattr(args, "json", False):
            print(json.dumps({"ok": False, "code": exc.code, "error": exc.message, "hint": exc.hint}))
        else:
            print(f"error [{exc.code}]: {exc.message}", file=sys.stderr)
            if exc.hint:
                print(f"  fix: {exc.hint}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted. If an install was running it restored its backups; check `status`.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
