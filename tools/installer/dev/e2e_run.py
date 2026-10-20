#!/usr/bin/env python3
"""End-to-end proof on a disposable COPY of the game (never the main install, never real saves).

Builds previews/installer-test/{game,saves,work}: the EXE and config are copied, the official
read-only archives are hardlinked (the installer opens them read-only). Then drives the real CLI through:

  S1  fresh owner: apply mace (needs the owner's Retail client + audited CascLib.dll) -> verify -> rollback -> verify
  S1n tamper: rollback must refuse after the installed file is changed, then succeed once restored
  S2  foreign patch-0.MPQ (community spell MPQ): refuse by default; --foreign-slots merge; compare with the live
      patch-0 hash; rollback restores the foreign file byte-for-byte
  S3  experimental EXE patch (water shader): apply -> verify exe hash -> rollback -> original exe hash

Usage: python e2e_run.py --client-root "C:/World of Warcraft" --casc-dll <CascLib.dll> [--heavy-lock <path>]
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
from asinstall.util import heavy_lock, lower_priority  # noqa: E402

MAIN = Path(".")
ROOT = Path("previews/installer-test")
GAME, SAVES, WORK = ROOT / "game", ROOT / "saves", ROOT / "work"
REPORT = MAIN / "analysis/installer/e2e-report.json"
SPELL_MPQ = MAIN / "mod-source/spell-hd-integrated/spell-hd.MPQ"
EXE_ORIG = "6382e9ae654295293b82d48fb07a1aa999132775bda10c6c6f80d973d2c1ddfb"
EXE_WATER = "524e83499dd8f216fadddf75c57346d03dcecb0b456b1f07f87eb6b109c35a1b"
SPELL_SHA = "d63657b18e25363a5067f9dbdc1291aa0a2ec78f02f053a0f0cf955b575add25"
LIVE_PATCH0 = "144d9b4274086657f0e0e6f3307956237d7c7c97c0c71a53e30c914c82dc2e95"

steps: list[dict] = []
args: argparse.Namespace


def sha(p: Path) -> str | None:
    if not p.is_file():
        return None
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def tree_hashes(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): sha(p) for p in sorted(root.rglob("*")) if p.is_file()}


def record(name: str, ok: bool, **info) -> bool:
    steps.append({"step": name, "ok": ok, **info})
    print(("PASS " if ok else "FAIL ") + name + (" " + json.dumps(info)[:300] if info else ""), flush=True)
    return ok


def cli(*argv: str, expect_rc: int = 0, timeout: int = 540) -> tuple[int, dict | None, str]:
    cmd = [sys.executable, str(CLI), *argv, "--json"]
    t0 = time.monotonic()
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    payload = None
    try:
        payload = json.loads(p.stdout) if p.stdout.strip() else None
    except ValueError:
        pass
    steps.append({"cli": " ".join(argv), "rc": p.returncode, "seconds": round(time.monotonic() - t0, 1),
                  "stderr_tail": p.stderr.strip().splitlines()[-6:]})
    return p.returncode, payload, p.stderr


def setup() -> dict:
    if ROOT.exists():
        assert ROOT.resolve().is_relative_to(Path("previews").resolve()), "refusing to delete outside previews"
        shutil.rmtree(ROOT)
    (GAME / "Data").mkdir(parents=True)
    SAVES.mkdir()
    WORK.mkdir()
    shutil.copy2(MAIN / "AzerothSurvivors.exe", GAME / "AzerothSurvivors.exe")
    shutil.copytree(MAIN / "benilla-config", GAME / "benilla-config")
    linked = []
    for name in ["patch.MPQ"] + [f"patch-{i}.MPQ" for i in range(2, 9)]:
        os.link(MAIN / "Data" / name, GAME / "Data" / name)   # same volume; archives are read-only inputs
        linked.append(name)
    (SAVES / "characters.json").write_text('{"synthetic": true}\n', encoding="utf-8")
    (SAVES / "settings.json").write_text('{"fake": 1}\n', encoding="utf-8")
    return {"hardlinked": linked}


def common(*extra: str, saves: bool = True) -> list[str]:
    return ["--game-root", str(GAME), "--client-root", args.client_root, "--casc-dll", args.casc_dll,
            *(["--saves-dir", str(SAVES)] if saves else []), *extra]


def lock_args() -> list[str]:
    return []   # this script holds the heavy lock for the whole run; the CLI must not take it again


def last_receipt() -> dict:
    rs = sorted((GAME / "mod-backups/installer/transactions").glob("*/receipt.json"), key=lambda p: p.stat().st_mtime)
    return json.loads(rs[-1].read_text("utf-8")) if rs else {}


def run() -> int:
    global args
    ap = argparse.ArgumentParser()
    ap.add_argument("--client-root", required=True)
    ap.add_argument("--casc-dll", required=True)
    ap.add_argument("--heavy-lock")
    args = ap.parse_args()
    lower_priority()
    main_before = {n: sha(MAIN / n) for n in ("AzerothSurvivors.exe", "Data/patch-0.MPQ", "Data/patch-1.MPQ")}
    info = setup()
    base = tree_hashes(GAME)
    saves0 = tree_hashes(SAVES)
    record("setup disposable copy", True, game=str(GAME), **info, exe=base["AzerothSurvivors.exe"][:16])
    ok = base["AzerothSurvivors.exe"] == EXE_ORIG and "Data/patch-0.MPQ" not in base

    # ---- S1: fresh owner ------------------------------------------------------------------
    rc, plan, _ = cli("plan", *common(*lock_args(), saves=False))
    ok &= record("S1 plan ok, nothing written", rc == 0 and tree_hashes(GAME) == base, rc=rc)
    rc, res, err = cli("apply", *common(*lock_args(), "--work", str(WORK / "s1"), "--yes"))
    r1 = last_receipt()
    p0 = sha(GAME / "Data/patch-0.MPQ")
    ok &= record("S1 apply", rc == 0 and r1.get("status") == "installed-and-verified" and p0 is not None,
                 rc=rc, patch0=p0, receipt=r1.get("id"), status=r1.get("status"), err=err.strip().splitlines()[-3:] if rc else None)
    rc, st, _ = cli("status", "--game-root", str(GAME))
    ok &= record("S1 status shows intact target", rc == 0 and json.dumps(st).count("true") > 0, rc=rc)
    ok &= record("S1 saves unchanged by apply", tree_hashes(SAVES) == saves0)
    rc, dry, _ = cli("rollback", *["--game-root", str(GAME), "--saves-dir", str(SAVES)])
    ok &= record("S1 rollback dry run changes nothing", rc == 0 and sha(GAME / "Data/patch-0.MPQ") == p0, rc=rc)
    # tamper -> refusal -> restore -> real rollback
    good = (GAME / "Data/patch-0.MPQ").read_bytes()
    (GAME / "Data/patch-0.MPQ").write_bytes(good + b"tamper")
    rc, _, err = cli("rollback", "--game-root", str(GAME), "--saves-dir", str(SAVES), "--yes")
    ok &= record("S1n rollback refuses after external change", rc == 1 and (GAME / "Data/patch-0.MPQ").read_bytes() == good + b"tamper", rc=rc)
    (GAME / "Data/patch-0.MPQ").write_bytes(good)
    rc, res, err = cli("rollback", "--game-root", str(GAME), "--saves-dir", str(SAVES), "--yes")
    now = tree_hashes(GAME)
    state_free = {k: v for k, v in now.items() if not k.startswith("mod-backups/")}
    ok &= record("S1 rollback restores original tree", rc == 0 and state_free == base, rc=rc,
                 patch0_present=(GAME / "Data/patch-0.MPQ").exists())
    ok &= record("S1 saves unchanged by rollback", tree_hashes(SAVES) == saves0)
    shutil.copytree(GAME / "mod-backups/installer", REPORT.parent / "e2e-receipts-s1", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("backup"))

    # ---- S2: foreign patch-0 ----------------------------------------------------------------
    shutil.copy2(SPELL_MPQ, GAME / "Data/patch-0.MPQ")
    ok &= record("S2 foreign slot is the community spell MPQ", sha(GAME / "Data/patch-0.MPQ") == SPELL_SHA)
    base2 = tree_hashes(GAME)
    rc, plan, _ = cli("plan", *common(*lock_args(), saves=False))
    blockers = json.dumps(plan or {})
    ok &= record("S2 plan blocks on foreign slot", rc != 0 and "foreign-slot" in blockers, rc=rc)
    rc, _, _ = cli("apply", *common(*lock_args(), "--work", str(WORK / "s2-refuse"), "--yes"))
    ok &= record("S2 apply refused by default, nothing changed", rc != 0 and
                 {k: v for k, v in tree_hashes(GAME).items() if not k.startswith("mod-backups/")} == {k: v for k, v in base2.items() if not k.startswith("mod-backups/")}, rc=rc)
    rc, res, err = cli("apply", *common(*lock_args(), "--work", str(WORK / "s2"), "--foreign-slots", "merge", "--yes"))
    r2 = last_receipt()
    p0b = sha(GAME / "Data/patch-0.MPQ")
    ok &= record("S2 merge apply", rc == 0 and r2.get("status") == "installed-and-verified", rc=rc, patch0=p0b, matches_live_patch0=(p0b == LIVE_PATCH0),
                 err=err.strip().splitlines()[-3:] if rc else None)
    rc, res, err = cli("rollback", "--game-root", str(GAME), "--saves-dir", str(SAVES), "--yes")
    ok &= record("S2 rollback restores the foreign file byte-for-byte", rc == 0 and sha(GAME / "Data/patch-0.MPQ") == SPELL_SHA, rc=rc)
    shutil.copytree(GAME / "mod-backups/installer", REPORT.parent / "e2e-receipts-s2", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("backup"))
    (GAME / "Data/patch-0.MPQ").unlink()

    # ---- S3: EXE patch ----------------------------------------------------------------------
    rc, res, err = cli("apply", "--game-root", str(GAME), "--saves-dir", str(SAVES), "--recipe", "water-shader-adt-liquid",
                       "--allow-experimental", "--work", str(WORK / "s3"), "--yes")
    exe_now = sha(GAME / "AzerothSurvivors.exe")
    ok &= record("S3 water EXE patch applied", rc == 0 and exe_now == EXE_WATER, rc=rc, exe=exe_now, err=err.strip().splitlines()[-3:] if rc else None)
    rc, res, err = cli("rollback", "--game-root", str(GAME), "--saves-dir", str(SAVES), "--yes")
    ok &= record("S3 rollback restores original EXE", rc == 0 and sha(GAME / "AzerothSurvivors.exe") == EXE_ORIG, rc=rc)
    shutil.copytree(GAME / "mod-backups/installer", REPORT.parent / "e2e-receipts-s3", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("backup"))

    # ---- main install untouched -----------------------------------------------------------
    main_after = {n: sha(MAIN / n) for n in main_before}
    ok &= record("main install files unchanged", main_before == main_after)
    final = tree_hashes(GAME)
    ok &= record("final copy tree equals starting tree", {k: v for k, v in final.items() if not k.startswith("mod-backups/")} == base)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps({"schema": "as-e2e/1", "passed": bool(ok), "when": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                  "game_copy": str(GAME), "steps": steps}, indent=2) + "\n", encoding="utf-8")
    print("E2E", "PASS" if ok else "FAIL", "->", REPORT)
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--heavy-lock", help="coordination heavy.lock; held for the whole run, waits up to 8 minutes")
    known, rest = ap.parse_known_args()
    sys.argv = [sys.argv[0], *rest, *(["--heavy-lock", known.heavy_lock] if known.heavy_lock else [])]
    with heavy_lock(Path(known.heavy_lock) if known.heavy_lock else None, "installer E2E on previews/installer-test copy", wait=480):
        return run()


if __name__ == "__main__":
    raise SystemExit(main())
