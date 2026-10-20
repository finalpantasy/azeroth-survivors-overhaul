#!/usr/bin/env python3
"""Assemble and maintain the combined "next" build: previews/next-v0.103.1/ (official v0.103.1 base).

Reads the worker status files (coordination/workers/*.md), keeps every item whose verification rule holds (see
items.json), builds the profile `next` from those items' recipes and installs it with the installer's own `integrate`
flow into the next folder (never the main install, never previews/characters-v0.103.0). Re-run it whenever a status file
changes: it does nothing when the verified set and the installed state are unchanged, otherwise it rolls the previous
transaction back and rebuilds. Always refreshes analysis/installer/next-manifest.md.

    python next_build.py              # evaluate, rebuild if needed, refresh manifest + launcher
    python next_build.py --dry-run    # evaluate only, print what would be in the build
    python next_build.py --force      # rebuild even if nothing changed
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
INST = HERE.parent
sys.path.insert(0, str(INST))
from asinstall import engine, mpq, status as statmod, txn  # noqa: E402
from asinstall.util import heavy_lock, lower_priority  # noqa: E402

ROOT = Path(".")
MAIN = ROOT / "AzerothSurvivors"
WORKERS = ROOT / "coordination/workers"
LOCKS = ROOT / "coordination/locks"
NEXT = ROOT / "previews/next-v0.103.1"
OLD_NEXT = ROOT / "previews/next-v0.103.0"          # previous (v0.103.0) next folder: kept as is, only its saves are used as a seed
BASE = ROOT / "previews/base-v0.103.1"              # clean official v0.103.1 (EXE + official archives), never modified here
TARGET_EXE_SHA = "3b46bad92e7771afa61c93658fd850937834a40f3671af8b1a3e1d2aeb8b63c7"
CACHE = ROOT / "previews/next-cache"
GEN = MAIN / "analysis/installer/next"
MANIFEST = MAIN / "analysis/installer/next-manifest.md"
LAUNCHER = ROOT / "Play Azeroth Survivors - Next.cmd"
PROTECTED = [ROOT / "previews/characters-v0.103.0"]
CLIENT = "C:/World of Warcraft"
CASC_DLL = MAIN / "tools/casc-offline/build-x64/Release/CascLib.dll"
SAVES_SEED = ROOT / "previews/graphics-v0.103.0/saves"
CLI = INST / "as_installer.py"
RACES = {"human": "Human", "orc": "Orc", "dwarf": "Dwarf", "night elf": "NightElf", "nightelf": "NightElf", "undead": "Scourge",
         "scourge": "Scourge", "tauren": "Tauren", "gnome": "Gnome", "troll": "Troll"}
RUN_RE = re.compile(r"(?:analysis/runtime/)?lab-[a-z0-9-]+-\d{8}-\d{6}")


def sha_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def read_status(name: str) -> str:
    p = WORKERS / name
    return p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""


# ------------------------------------------------------------------ evaluation of the status files
def player_ids() -> dict[tuple[str, str], int]:
    sys.path.insert(0, str(MAIN / "tools/characters"))
    import importlib.util
    spec = importlib.util.spec_from_file_location("bc_ro", MAIN / "tools/characters/build_characters.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return {(label, "M" if sex == 0 else "F"): disp for _, label, sex, disp in mod.PLAYABLE}


def parse_characters(text: str) -> list[dict]:
    """Rows of the characters.md per-race table -> one entry per race/sex with verified flag + evidence."""
    ids = player_ids()
    out = []
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3 or not line.lstrip().startswith("|"):
            continue
        label = re.sub(r"\s+[MF](,\s*[MF])*\s*$", "", cells[0]).strip().lower()
        race = RACES.get(label)
        if race is None:
            continue
        sexes = re.findall(r"\b([MF])\b", cells[0])
        verified = cells[2].upper().startswith("VERIFIED")
        for sx in sexes or ["M", "F"]:
            disp = ids.get((race, sx))
            if disp is not None:
                out.append({"race": race, "sex": sx, "display": disp, "verified": verified, "row": cells[0] + " | " + cells[2][:140]})
    return out


def evaluate() -> dict:
    cat = engine.load_catalog()
    cfg = json.loads((HERE / "items.json").read_text("utf-8"))
    main_rows = {r["id"]: r for r in statmod.recipe_status(MAIN, [r for r in cat.values() if r.available])}
    items = []
    for it in cfg["items"]:
        rule, rid = it["verify"], it["recipes"][0]
        rec = cat.get(rid)
        entry = {"id": it["id"], "recipes": it["recipes"], "verified": False, "evidence": "", "rule": rule["kind"]}
        if rec is None or not rec.available:
            entry["evidence"] = "recipe missing or unavailable"
        elif TARGET_EXE_SHA not in rec.raw.get("requires", {}).get("game", {}).get("exe_sha256", []):
            entry["evidence"] = "NEEDS REBASE for v0.103.1: the recipe does not list the 0.103.1 EXE (see analysis/installer/rebase-v0.103.1.md)"
        elif rule["kind"] == "installed-in-main":
            row = main_rows.get(rid, {})
            entry["verified"] = row.get("state") == "installed"
            entry["evidence"] = f"installed in main, byte-exact by content ({row.get('matched')}/{row.get('of')})"
        elif rule["kind"] == "status-file":
            text = read_status(rule["file"])
            m = re.search(rule["pattern"], text, re.S)
            entry["verified"] = bool(m)
            if m:
                runs = sorted(set(RUN_RE.findall(m.group(0))))
                entry["evidence"] = f"{rule['file']}: " + " ".join(m.group(0).split())[:200] + (f" [runs: {', '.join(runs)}]" if runs else "")
            else:
                entry["evidence"] = f"no VERIFIED marker for this in {rule['file']}"
        elif rule["kind"] == "coordinator":
            entry["verified"] = True
            entry["evidence"] = rule["note"]
        items.append(entry)
    chars = parse_characters(read_status(cfg["characters"]["file"]))
    ok_ids = sorted(c["display"] for c in chars if c["verified"])
    char_item = {"id": "hd-characters", "recipes": [cfg["characters"]["recipe"]] if ok_ids else [], "verified": bool(ok_ids),
                 "display_ids": ok_ids, "rule": "status-file (characters.md table)",
                 "evidence": "; ".join(f"{c['race']} {c['sex']}" for c in chars if c["verified"]) or "no verified rows",
                 "excluded": [f"{c['race']} {c['sex']}: {c['row']}" for c in chars if not c["verified"]], "rows": chars}
    return {"items": items, "characters": char_item, "priority": cfg["priority"], "catalog": cat}


# ------------------------------------------------------------------ build
def local_sources(recipe_ids: list[str], cat) -> dict[str, Path]:
    """Fill CACHE/sources/<id> from files already on this machine, matched by SHA-256 (never downloaded)."""
    needs = {}
    for rid in recipe_ids:
        for sid, src in cat[rid].sources.items():
            if src["kind"] == "user-supplied" and sid != "chars":
                for f in src["files"]:
                    needs[(sid, f["name"])] = f
    out = {}
    if not needs:
        return out
    base = CACHE / "sources"
    missing = [(sid, n, f) for (sid, n), f in needs.items()
               if not (base / sid / n).is_file() or (base / sid / n).stat().st_size != f["bytes"]]
    if missing:
        pool: dict[str, bytes] = {}
        for path in (MAIN / "mod-source/terrain-hd.MPQ", MAIN / "mod-source/spell-hd-integrated/spell-hd.MPQ", MAIN / "Data/patch-0.MPQ"):
            if path.is_file():
                for _, data in mpq.read_all(path).items():
                    pool.setdefault(sha_bytes(data), data)
        for d in (MAIN / "analysis/spell-hd-phase2/source", MAIN / "mod-source/spell-hd-phase2", MAIN / "mod-source/spell-hd"):
            if d.is_dir():
                for p in d.rglob("*"):
                    if p.is_file() and p.stat().st_size < (64 << 20):
                        data = p.read_bytes()
                        pool.setdefault(sha_bytes(data), data)
        gaps = []
        for sid, name, f in missing:
            data = pool.get(f["sha256"])
            if data is None:
                gaps.append(f"{sid}/{name}")
                continue
            dest = base / sid / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
        if gaps:
            raise SystemExit(f"{len(gaps)} community source file(s) have no local copy by hash (e.g. {gaps[0]}); "
                             f"run tools/fetch_community_sources.py (explicit, needs the owner's yes).")
    for sid in {s for s, _ in needs}:
        out[sid] = base / sid
    return out


def ensure_next_folder() -> None:
    for p in PROTECTED:
        if NEXT.resolve() == p.resolve():
            raise SystemExit("refusing: protected folder")
    (NEXT / "Data").mkdir(parents=True, exist_ok=True)
    for sub in ("diagnostics",):
        (NEXT / sub).mkdir(exist_ok=True)
    if not (NEXT / "AzerothSurvivors.exe").is_file():
        if sha_file(BASE / "AzerothSurvivors.exe") != TARGET_EXE_SHA:
            raise SystemExit("base-v0.103.1 EXE is not the official 0.103.1 build")
        shutil.copy2(BASE / "AzerothSurvivors.exe", NEXT / "AzerothSurvivors.exe")
    if not (NEXT / "benilla-config").is_dir():
        shutil.copytree(MAIN / "benilla-config", NEXT / "benilla-config")
    seed = OLD_NEXT / "saves" if (OLD_NEXT / "saves").is_dir() else SAVES_SEED
    if not (NEXT / "saves").is_dir() and seed.is_dir():
        shutil.copytree(seed, NEXT / "saves")
    (NEXT / "saves").mkdir(exist_ok=True)
    for name in ["patch.MPQ"] + [f"patch-{i}.MPQ" for i in range(2, 9)]:
        dest = NEXT / "Data" / name
        if not dest.exists():
            os.link(BASE / "Data" / name, dest)   # read-only hardlink of the official archive (same volume)


def cli(*argv: str, timeout: int = 590) -> tuple[int, dict | list | None, str]:
    p = subprocess.run([sys.executable, str(CLI), *argv, "--json"], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout)
    payload = None
    try:
        payload = json.loads(p.stdout) if p.stdout.strip() else None
    except ValueError:
        pass
    return p.returncode, payload, p.stderr


def roll_back_all() -> int:
    n = 0
    while True:
        rc, st, err = cli("status", "--game-root", str(NEXT), "--recipes-dir", str(GEN / "recipes"), "--quiet")
        if rc != 0 or not st or not st.get("installed_targets"):
            return n
        rc, res, err = cli("rollback", "--game-root", str(NEXT), "--saves-dir", str(NEXT / "saves"), "--yes", "--quiet")
        if rc != 0:
            raise SystemExit("rollback of the previous next build failed: " + "\n".join(err.strip().splitlines()[-4:]))
        n += 1
        if n > 20:
            raise SystemExit("too many transactions to roll back")


def write_profile(recipe_ids: list[str], prio: list[str]) -> Path:
    prof = {"schema": "as-profile/1", "id": "next", "version": time.strftime("%Y.%m.%d").replace(".0", "."),
            "title": "Next build: every VERIFIED item combined",
            "description": "Generated by next/next_build.py from the worker status files. Priority (highest first) decides "
                           "same-path conflicts between recipes; EXE interval conflicts are always errors.",
            "recipes": recipe_ids, "allow_experimental": True,
            "priority": [r for r in prio if r in recipe_ids]}
    if len(prof["priority"]) < 2:
        prof.pop("priority")
    # version must match x.y.z
    prof["version"] = "1.0.0"
    path = GEN / "profiles" / "next.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(prof, indent=2) + "\n", encoding="utf-8")
    return path


def write_launcher() -> None:
    LAUNCHER.write_text(
        '@echo off\nsetlocal\n'
        'set "PREVIEW_ROOT=%~dp0previews\\next-v0.103.1\\"\n'
        'if not exist "%PREVIEW_ROOT%AzerothSurvivors.exe" (\n  echo The next build has not been assembled yet.\n  pause\n  exit /b 1\n)\n'
        'for %%V in (WOW_NOSOUND WOW_CAPTURE WOW_CAPTURE_UI WOW_CAM_DUMP WOW_ROGUE_ARMORY WOW_ROGUE_GLUE WOW_ROGUE_TAPS WOW_ROGUE_KEY '
        'WOW_ROGUE_AUTOPLAY WOW_ROGUE_AUTOSTART WOW_ROGUE_AUTOPICK WOW_ROGUE_SHOTS WOW_ROGUE_SHOWCASE WOW_ROGUE_DEBUG_LOOT '
        'WOW_ROGUE_UPDATE_URL WOW_ROGUE_SINK WOW_ROGUE_SINK_AT) do set "%%V="\n'
        'set "WOW_DATA=%PREVIEW_ROOT%Data"\nset "BENILLA_HOME=%PREVIEW_ROOT%benilla-config"\n'
        'set "WOW_ROGUE_SAVE=%PREVIEW_ROOT%saves\\characters.txt"\nset "WOW_ROGUE_UPDATE_AUTO=0"\n'
        'if not exist "%PREVIEW_ROOT%diagnostics" mkdir "%PREVIEW_ROOT%diagnostics"\n'
        ':choose_journal\nset "WOW_FPS_JOURNAL=%PREVIEW_ROOT%diagnostics\\frame-times-%RANDOM%-%RANDOM%.csv"\n'
        'if exist "%WOW_FPS_JOURNAL%" goto choose_journal\n'
        'cd /d "%PREVIEW_ROOT%"\nstart "" "AzerothSurvivors.exe"\nendlocal\n', encoding="utf-8", newline="\r\n")


def fingerprint(ev: dict, recipe_ids: list[str]) -> str:
    cat = ev["catalog"]
    blob = {"recipes": {rid: (sha_file(cat[rid].path) if cat[rid].path else None) for rid in recipe_ids if rid != "hd-characters-next"},
            "chars": ev["characters"]["display_ids"], "priority": ev["priority"],
            "chars_builder": sha_file(MAIN / "tools/characters/build_characters.py") if ev["characters"]["verified"] else None}
    return sha_bytes(json.dumps(blob, sort_keys=True).encode())


def installed_state(recipe_ids: list[str]) -> list[dict]:
    cat = engine.load_catalog([GEN / "recipes"])
    return statmod.recipe_status(NEXT, [cat[r] for r in recipe_ids if r in cat])


def run(args) -> int:
    ev = evaluate()
    included = [i for i in ev["items"] if i["verified"]]
    recipe_ids = [r for i in included for r in i["recipes"]]
    if ev["characters"]["verified"]:
        # characters go right after the baseline recipes so its DBCs stack on the spell/mace files
        recipe_ids.append("hd-characters-next")
    recipe_ids = list(dict.fromkeys(recipe_ids))
    print("verified items:", ", ".join(i["id"] for i in included) + (", hd-characters(" + str(len(ev['characters']['display_ids'])) + " models)"
                                                                  if ev["characters"]["verified"] else ""))
    fp = fingerprint(ev, recipe_ids)
    state_path = NEXT / "next-state.json"
    old = json.loads(state_path.read_text("utf-8")) if state_path.is_file() else {}
    up_to_date = False
    if old.get("fingerprint") == fp and not args.force and (NEXT / "AzerothSurvivors.exe").is_file():
        rows = installed_state(recipe_ids)
        up_to_date = all(r["state"] == "installed" for r in rows)
    if args.dry_run:
        print("dry run: up_to_date =", up_to_date)
        write_manifest(ev, included, recipe_ids, old, None, up_to_date)
        return 0
    if up_to_date:
        print("next build is up to date; refreshing the manifest only")
        write_manifest(ev, included, recipe_ids, old, None, True)
        return 0
    if (LOCKS / "exclusive.lock").exists():
        print("exclusive.lock exists (perf bench): not rebuilding now", file=sys.stderr)
        return 2
    lower_priority()
    with heavy_lock(LOCKS / "heavy.lock", "installer next build", wait=args.lock_wait):
        t0 = time.monotonic()
        GEN.joinpath("recipes").mkdir(parents=True, exist_ok=True)
        extra_sources: dict[str, Path] = {}
        if ev["characters"]["verified"]:
            from gen_characters import build_payload, write_recipe
            ids = ev["characters"]["display_ids"]
            payload = build_payload(ids, CACHE / "characters")
            info = write_recipe(payload, ids, GEN / "recipes" / "hd-characters-next.json")
            extra_sources["chars"] = payload
            print("characters payload:", info["files"], "files", info["bytes"] // (1 << 20), "MiB")
        elif (GEN / "recipes" / "hd-characters-next.json").exists():
            (GEN / "recipes" / "hd-characters-next.json").unlink()
        cat = engine.load_catalog([GEN / "recipes"])
        sources = local_sources(recipe_ids, cat)
        sources.update(extra_sources)
        profile = write_profile(recipe_ids, ev["priority"])
        ensure_next_folder()
        undone = roll_back_all()
        work = CACHE / "work" / time.strftime("%Y%m%d-%H%M%S")
        work.parent.mkdir(parents=True, exist_ok=True)
        argv = ["integrate", "--game-root", str(NEXT), "--client-root", CLIENT, "--casc-dll", str(CASC_DLL), "--profile", "next",
                "--recipes-dir", str(GEN / "recipes"), "--profiles-dir", str(GEN / "profiles"), "--saves-dir", str(NEXT / "saves"),
                "--allow-experimental", "--apply", "--yes", "--work", str(work), "--quiet",
                *[a for sid, d in sources.items() for a in ("--source", f"{sid}={d}")]]
        rc, res, err = cli(*argv)
        if rc != 0:
            print("integrate FAILED:\n" + "\n".join(err.strip().splitlines()[-8:]), file=sys.stderr)
            write_manifest(ev, included, recipe_ids, old, {"error": "\n".join(err.strip().splitlines()[-8:])}, False)
            return 1
        build_info = json.loads((work / "build.json").read_text("utf-8")) if (work / "build.json").is_file() else {}
        shutil.rmtree(work, ignore_errors=True)
        rows = installed_state(recipe_ids)
        receipts = sorted((NEXT / "mod-backups/installer/transactions").glob("*/receipt.json"), key=lambda p: p.stat().st_mtime)
        receipt = json.loads(receipts[-1].read_text("utf-8")) if receipts else {}
        hashes = {rel: sha_file(NEXT / rel) for rel in ("Data/patch-0.MPQ", "Data/patch-1.MPQ", "AzerothSurvivors.exe") if (NEXT / rel).is_file()}
        new_state = {"fingerprint": fp, "built": time.strftime("%Y-%m-%dT%H:%M:%S"), "recipes": recipe_ids, "hashes": hashes,
                     "receipt": receipt.get("id"), "rolled_back": undone, "seconds": round(time.monotonic() - t0, 1),
                     "conflicts_resolved": build_info.get("conflicts_resolved", []), "states": rows,
                     "acceptance_checks": len(build_info.get("acceptance", []))}
        state_path.write_text(json.dumps(new_state, indent=2) + "\n", encoding="utf-8")
        write_launcher()
        write_manifest(ev, included, recipe_ids, new_state, None, True)
        bad = [r for r in rows if r["state"] != "installed"]
        print("next build ready:", json.dumps(hashes), "| not installed:", [r["id"] for r in bad])
        return 0 if not bad else 1


def write_manifest(ev: dict, included: list[dict], recipe_ids: list[str], state: dict, failure: dict | None, current: bool) -> None:
    cat = ev["catalog"]
    lines = ["# Next build manifest", "",
             f"Generated {time.strftime('%Y-%m-%d %H:%M')} by `tools/installer/next/next_build.py` from `coordination/workers/*.md`.",
             f"Folder: `previews/next-v0.103.1/` (official v0.103.1 base) | launcher: `Play Azeroth Survivors - Next.cmd` | main install untouched.", ""]
    if failure:
        lines += ["**LAST REBUILD FAILED**: `" + failure["error"].replace("\n", " / ")[:400] + "`", ""]
    if state.get("built"):
        lines += [f"Built {state['built']} ({state.get('seconds')} s), installer transaction `{state.get('receipt')}`, "
                  f"{state.get('acceptance_checks')} acceptance checks passed. State: "
                  + ("current" if current else "STALE: the verified set changed, rebuild pending"), ""]
        for rel, h in state.get("hashes", {}).items():
            lines.append(f"- `{rel}` sha256 `{h}`")
        lines.append("")
    lines += ["## In the build", "", "| Item | Recipe | Verified by | Installed |", "|---|---|---|---|"]
    states = {r["id"]: r for r in state.get("states", [])}
    for it in included:
        for rid in it["recipes"]:
            st = states.get(rid, {})
            lines.append(f"| {it['id']} | `{rid}` | {it['evidence'].replace('|', '/')} | {st.get('state', '-')} {st.get('matched', '')}/{st.get('of', '')} |")
    ch = ev["characters"]
    if ch["verified"]:
        st = states.get("hd-characters-next", {})
        lines.append(f"| hd-characters ({len(ch['display_ids'])} models: {ch['evidence']}) | `hd-characters-next` (generated, display ids "
                     f"{','.join(map(str, ch['display_ids']))}) | characters.md table rows VERIFIED; lab sheets in `analysis/characters/lab/` | "
                     f"{st.get('state', '-')} {st.get('matched', '')}/{st.get('of', '')} |")
    lines += ["", "## Not in the build (not VERIFIED yet)", ""]
    for it in ev["items"]:
        if not it["verified"]:
            lines.append(f"- {it['id']} (`{it['recipes'][0]}`): {it['evidence']}")
    for x in ch["excluded"]:
        lines.append(f"- characters, {x}")
    lines += ["", "## Conflict rule", "",
              "- Same MPQ path from two recipes with identical bytes: fine (deduplicated).",
              "- Same path, different bytes: build stops with `recipe-conflict` naming both recipes, unless both are in the profile's "
              "`priority` list (highest first); then the higher one wins and the choice is recorded in `build.json` and below.",
              "- EXE byte intervals that overlap, different base EXEs, or two recipes appending a section: always an error.",
              "- Priority used: " + " > ".join(r for r in ev["priority"] if r in recipe_ids), ""]
    res = state.get("conflicts_resolved", [])
    lines.append("Conflicts resolved in the last build: " + (", ".join(f"{c['path']} -> {c['winner']} over {c['loser']}" for c in res) if res else "none"))
    sm = HERE.parent.parent.parent / "analysis" / "installer" / "next" / "smoke.json"
    if sm.exists():
        smoke = json.loads(sm.read_text(encoding="utf-8"))
        same = all(state.get("hashes", {}).get(k) == smoke.get(v) for k, v in
                   (("Data/patch-0.MPQ", "patch0_sha256"), ("Data/patch-1.MPQ", "patch1_sha256"), ("AzerothSurvivors.exe", "exe_sha256")))
        lines += ["", "## Lab smoke (inspection lab, hidden run, 1280x720)", "",
                  "Applies to the current build." if same else "**STALE: the build changed since this smoke; re-run the lab cases in `analysis/installer/next/cases/`.**", ""]
        for r in smoke["runs"]:
            lines.append(f"- {r['status']} `{r['run']}`: {r['what']}")
    lines += ["", "## Re-run", "", "```", "python tools/installer/next/next_build.py", "```",
              "Does nothing when the verified set and installed state are unchanged. Smoke evidence: see `coordination/workers/installer.md`.", ""]
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--lock-wait", type=float, default=480.0, help="seconds to wait for a heavy-work slot")
    args = ap.parse_args()
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
