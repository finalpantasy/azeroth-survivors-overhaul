#!/usr/bin/env python3
"""Packet-2 proof on the disposable copy (never main): build the whole current upgrade from the profile.

1. Rebuild the game copy (EXE + config copied, official archives hardlinked) and the synthetic saves.
2. Materialize the recipes' user-supplied source folders from files already on this machine, matched by SHA-256
   (never downloaded). Anything with no local match is reported as a gap.
3. `integrate --profile current-upgrade --apply --yes` -> patch-0/patch-1 must equal the live hashes the profile pins.
4. `status` must show the recipes installed; `rollback --yes` must restore the original tree.
5. `integrate --profile current-upgrade-renderer` (water + sharpen EXE patches combined) -> record the EXE hash, roll back.

Usage: python e2e_packet2.py --client-root "C:/World of Warcraft" --casc-dll <dll> [--heavy-lock <path>]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
INSTALLER = HERE.parent
CLI = INSTALLER / "as_installer.py"
sys.path.insert(0, str(INSTALLER))
from asinstall import mpq  # noqa: E402
from asinstall.util import heavy_lock, lower_priority  # noqa: E402

MAIN = Path(".")
ROOT = Path("previews/installer-test")
GAME, SAVES, WORK, SOURCES = ROOT / "game", ROOT / "saves", ROOT / "work2", ROOT / "sources"
REPORT = MAIN / "analysis/installer/e2e-packet2-report.json"
LIVE = {"Data/patch-0.MPQ": "144d9b4274086657f0e0e6f3307956237d7c7c97c0c71a53e30c914c82dc2e95"}
EXE_ORIG = "6382e9ae654295293b82d48fb07a1aa999132775bda10c6c6f80d973d2c1ddfb"
POOLS = [MAIN / "mod-source/terrain-hd.MPQ", MAIN / "mod-source/spell-hd-integrated/spell-hd.MPQ", MAIN / "Data/patch-0.MPQ"]
POOL_DIRS = [MAIN / "analysis/spell-hd-phase2/source", MAIN / "mod-source/spell-hd-phase2", MAIN / "mod-source/spell-hd"]

steps: list[dict] = []
args: argparse.Namespace


def sha_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha(p: Path) -> str | None:
    if not p.is_file():
        return None
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def tree(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): sha(p) for p in sorted(root.rglob("*")) if p.is_file()}


def record(name: str, ok: bool, **info) -> bool:
    steps.append({"step": name, "ok": ok, **info})
    print(("PASS " if ok else "FAIL ") + name + (" " + json.dumps(info)[:400] if info else ""), flush=True)
    return ok


def cli(*argv: str, timeout: int = 540) -> tuple[int, dict | list | None, str]:
    p = subprocess.run([sys.executable, str(CLI), *argv, "--json"], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout)
    payload = None
    try:
        payload = json.loads(p.stdout) if p.stdout.strip() else None
    except ValueError:
        pass
    return p.returncode, payload, p.stderr


def setup() -> None:
    if ROOT.exists():
        for child in ROOT.iterdir():
            assert child.resolve().is_relative_to(Path("previews").resolve())
        if SOURCES.exists():
            shutil.rmtree(SOURCES)
        if WORK.exists():
            shutil.rmtree(WORK)
    if GAME.exists():
        shutil.rmtree(GAME)
    (GAME / "Data").mkdir(parents=True)
    SAVES.mkdir(exist_ok=True)
    WORK.mkdir()
    SOURCES.mkdir()
    shutil.copy2(MAIN / "AzerothSurvivors.exe", GAME / "AzerothSurvivors.exe")
    shutil.copytree(MAIN / "benilla-config", GAME / "benilla-config")
    for name in ["patch.MPQ"] + [f"patch-{i}.MPQ" for i in range(2, 9)]:
        os.link(MAIN / "Data" / name, GAME / "Data" / name)
    (SAVES / "characters.txt").write_text("synthetic hero\n", encoding="utf-8")


def materialize(recipe_ids: list[str]) -> dict:
    """Fill SOURCES/<source id>/ from local files matched by hash."""
    needs = {}
    for rid in recipe_ids:
        recipe = json.loads((INSTALLER / "recipes" / f"{rid}.json").read_text("utf-8"))
        for sid, src in recipe["sources"].items():
            if src.get("kind") == "user-supplied":
                for f in src["files"]:
                    needs[(sid, f["name"])] = f
    pool: dict[str, bytes] = {}
    for path in POOLS:
        if path.is_file():
            for _, data in mpq.read_all(path).items():
                pool.setdefault(sha_bytes(data), data)
    for d in POOL_DIRS:
        if d.is_dir():
            for p in d.rglob("*"):
                if p.is_file() and p.stat().st_size < 64 << 20:
                    data = p.read_bytes()
                    pool.setdefault(sha_bytes(data), data)
    found, missing = 0, []
    for (sid, name), f in sorted(needs.items()):
        data = pool.get(f["sha256"])
        if data is None:
            missing.append({"source": sid, "name": name, "sha256": f["sha256"]})
            continue
        dest = SOURCES / sid / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        found += 1
    return {"needed": len(needs), "found": found, "missing": missing}


def common(profile: str, *extra: str) -> list[str]:
    return ["--game-root", str(GAME), "--client-root", args.client_root, "--casc-dll", args.casc_dll, "--profile", profile,
            "--saves-dir", str(SAVES), "--source", f"vanillahd={SOURCES / 'vanillahd'}", "--source", f"community={SOURCES / 'community'}", *extra]


def run() -> int:
    lower_priority()
    main_before = {n: sha(MAIN / n) for n in ("AzerothSurvivors.exe", "Data/patch-0.MPQ", "Data/patch-1.MPQ")}
    t0 = time.monotonic()
    setup()
    base = tree(GAME)
    saves0 = tree(SAVES)
    ok = record("setup", base["AzerothSurvivors.exe"] == EXE_ORIG and "Data/patch-0.MPQ" not in base)
    mat = materialize(["hd-terrain-community", "hd-spells-community"])
    record("sources materialized from local files by hash", mat["found"] == mat["needed"], needed=mat["needed"], found=mat["found"],
           missing=mat["missing"][:10], missing_count=len(mat["missing"]))
    if mat["missing"]:
        report(False, main_before, mat)
        return 1

    rc, plan, err = cli("integrate", *common("current-upgrade"))
    ok &= record("integrate plan-only on copy", rc == 0 and tree(GAME) == base, rc=rc,
                 verdict=(plan or {}).get("integrate", {}).get("verdict"), err=err.strip().splitlines()[-3:])

    rc, res, err = cli("integrate", *common("current-upgrade", "--apply", "--yes", "--work", str(WORK / "up")), timeout=580)
    built = {r: sha(GAME / r) for r in LIVE}
    ok &= record("integrate apply: patch-0 equals the live archive byte for byte", rc == 0 and built == LIVE, rc=rc, built=built,
                 err=err.strip().splitlines()[-4:] if rc else None)
    same = False
    if (GAME / "Data/patch-1.MPQ").is_file():
        mine = mpq.read_all(GAME / "Data/patch-1.MPQ")
        live1 = mpq.read_all(MAIN / "Data/patch-1.MPQ")
        same = mine == live1
        record("patch-1 holds the same files as the live HD terrain archive", same, files=len(mine), live_files=len(live1),
               note="archive bytes differ: the live one is stored uncompressed")
    ok &= same
    rc, st, _ = cli("status", "--game-root", str(GAME))
    states = {r["id"]: r["state"] for r in (st or {}).get("recipes", [])}
    ok &= record("status shows the profile installed", all(states.get(i) == "installed" for i in
                 ("hd-terrain-community", "hd-spells-community", "retail-mace-kultiras-silver")), states={k: v for k, v in states.items() if not k.startswith("hd-player")})
    receipts = sorted((GAME / "mod-backups/installer/transactions").glob("*/receipt.json"), key=lambda p: p.stat().st_mtime)
    r1 = json.loads(receipts[-1].read_text("utf-8")) if receipts else {}
    snap = receipts[-1].parent / "saves-snapshot" / "saves" / "characters.txt" if receipts else None
    ok &= record("receipt + read-only saves snapshot", r1.get("status") == "installed-and-verified" and bool(snap and snap.is_file())
                 and not os.access(snap, os.W_OK), snapshot=r1.get("snapshot_copy"))
    rc, _, err = cli("rollback", "--game-root", str(GAME), "--saves-dir", str(SAVES), "--yes")
    ok &= record("rollback restores the original tree", rc == 0 and {k: v for k, v in tree(GAME).items() if not k.startswith("mod-backups/")} == base, rc=rc)

    rc, res, err = cli("integrate", *common("current-upgrade-renderer", "--apply", "--yes", "--work", str(WORK / "ren")), timeout=580)
    exe = sha(GAME / "AzerothSurvivors.exe")
    ok &= record("renderer profile: water + sharpen combined in one EXE", rc == 0 and exe != EXE_ORIG, rc=rc, exe_sha256=exe,
                 err=err.strip().splitlines()[-4:] if rc else None)
    rc, st, _ = cli("status", "--game-root", str(GAME))
    states = {r["id"]: r["state"] for r in (st or {}).get("recipes", [])}
    ok &= record("status shows water + sharpen installed", states.get("water-shader-adt-liquid") == "installed"
                 and states.get("renderer-v3-post") == "installed", states={k: v for k, v in states.items() if not k.startswith("hd-player")})
    rc, _, err = cli("rollback", "--game-root", str(GAME), "--saves-dir", str(SAVES), "--yes")
    ok &= record("renderer rollback restores the EXE", rc == 0 and sha(GAME / "AzerothSurvivors.exe") == EXE_ORIG, rc=rc)
    ok &= record("synthetic saves untouched", tree(SAVES) == saves0)
    ok &= record("main install untouched", {n: sha(MAIN / n) for n in main_before} == main_before)
    steps.append({"seconds": round(time.monotonic() - t0, 1)})
    report(ok, main_before, mat)
    return 0 if ok else 1


def report(ok: bool, main_before: dict, mat: dict) -> None:
    REPORT.write_text(json.dumps({"schema": "as-e2e/2", "passed": ok, "when": time.strftime("%Y-%m-%dT%H:%M:%S"), "game_copy": str(GAME),
                                  "sources": {"needed": mat["needed"], "found": mat["found"], "missing": mat["missing"]},
                                  "main_hashes": main_before, "steps": steps}, indent=2) + "\n", encoding="utf-8")
    print("report:", REPORT, "PASSED" if ok else "FAILED")


def main() -> int:
    global args
    ap = argparse.ArgumentParser()
    ap.add_argument("--client-root", required=True)
    ap.add_argument("--casc-dll", required=True)
    ap.add_argument("--heavy-lock", help="coordination heavy lock; pass the free slot, waits up to 8 minutes")
    args = ap.parse_args()
    excl = MAIN.parent / "coordination/locks/exclusive.lock"
    if excl.exists():
        print("exclusive.lock exists; not starting", file=sys.stderr)
        return 2
    with heavy_lock(Path(args.heavy_lock) if args.heavy_lock else None, "installer E2E packet 2 on previews/installer-test copy", wait=480):
        return run()


if __name__ == "__main__":
    raise SystemExit(main())
