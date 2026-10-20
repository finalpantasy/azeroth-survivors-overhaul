"""Transactional install, guarded rollback and status, with backups and receipts.

State lives under ``<game>/mod-backups/installer/``:
  state.json                       what the installer currently owns (slot/exe hashes)
  transactions/<id>/receipt.json   one receipt per install or rollback
  transactions/<id>/backup/...     copies of every file the transaction replaced
"""
from __future__ import annotations

import contextlib
import os
import shutil
import stat
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Iterator

from . import detect
from .util import (InstallerError, Progress, file_record, new_id, pid_alive, read_json, sha256_file,
                   utc_now, write_json_atomic, is_reparse_point)

STATE_DIR = Path("mod-backups") / "installer"
RECEIPT_SCHEMA = "as-receipt/1"
LOCK_WAIT_SECONDS = 15
ALLOWED_TARGETS = ("Data/patch-0.MPQ", "Data/patch-1.MPQ", "AzerothSurvivors.exe")


def state_dir(game_root: Path) -> Path:
    return Path(game_root) / STATE_DIR


def load_state(game_root: Path) -> dict[str, Any]:
    path = state_dir(game_root) / "state.json"
    if not path.is_file():
        return {"schema": "as-state/1", "targets": {}, "transactions": []}
    return read_json(path)


def classify_target(game_root: Path, rel: str, state: dict[str, Any]) -> dict[str, Any]:
    """absent | installer-owned | installer-owned-modified | foreign (with hashes)."""
    path = Path(game_root) / rel
    if not path.exists():
        return {"rel": rel, "kind": "absent", "sha256": None, "bytes": None}
    rec = file_record(path)
    owned = state.get("targets", {}).get(rel)
    if owned and owned["sha256"] == rec["sha256"]:
        kind = "installer-owned"
    elif owned:
        kind = "installer-owned-modified"
    else:
        kind = "foreign"
    return {"rel": rel, "kind": kind, **rec}


@contextlib.contextmanager
def transaction_lock(game_root: Path) -> Iterator[None]:
    folder = state_dir(game_root)
    folder.mkdir(parents=True, exist_ok=True)
    lock = folder / "lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            owner = int(lock.read_text(encoding="utf-8").split()[0])
        except (OSError, ValueError, IndexError):
            owner = 0
        if owner and pid_alive(owner):
            raise InstallerError(f"Another installer run (pid {owner}) is using this game folder.",
                                 "Wait for it to finish.", "install-locked")
        lock.unlink(missing_ok=True)  # stale lock from a dead run
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(f"{os.getpid()} {utc_now()}\n")
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


def assert_game_stopped(game_root: Path, targets: list[Path]) -> None:
    running = detect.game_running(game_root)
    if running:
        raise InstallerError("AzerothSurvivors.exe is running from this folder.",
                             "Exit the game completely, then retry. Nothing was changed.", "game-running")
    for target in targets:
        reason = detect.file_lock_state(target)
        if reason:
            raise InstallerError(f"{target.name} is locked: {reason}",
                                 "Close the game, launcher, antivirus scans or tools using it. Nothing was changed.", "file-locked")


def _wait_exclusive(path: Path) -> None:
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    while True:
        reason = detect.file_lock_state(path)
        if reason is None:
            return
        if time.monotonic() >= deadline:
            raise InstallerError(f"{path.name} stayed locked for {LOCK_WAIT_SECONDS} seconds: {reason}",
                                 "Close whatever holds it (game, Explorer preview, antivirus) and retry.", "file-locked")
        time.sleep(0.25)


def _copy_verified(src: Path, dest: Path, sha: str) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)
    if sha256_file(dest) != sha:
        dest.unlink(missing_ok=True)
        raise InstallerError(f"Copy of {src.name} did not verify (disk error or interference)", code="copy-verify")


def _replace_atomic(src: Path, dest: Path) -> None:
    """Move ``src`` over ``dest`` (atomic on one volume); retry briefly while scanners hold handles."""
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    while True:
        try:
            os.replace(src, dest)
            return
        except PermissionError as exc:
            if time.monotonic() >= deadline:
                raise InstallerError(f"Could not replace {dest.name}: {exc}",
                                     "Something has the file open; close it and retry.", "file-locked") from exc
            time.sleep(0.25)


def _snapshot(paths: list[Path]) -> dict[str, dict[str, Any]]:
    snap: dict[str, dict[str, Any]] = {}
    for root in paths:
        if not root.exists():
            continue
        for p in sorted(root.rglob("*")):
            if p.is_file():
                if is_reparse_point(p):
                    raise InstallerError(f"Refusing to snapshot a link: {p}", code="preserve-link")
                snap[str(p)] = file_record(p)
    return snap


def preserve_roots(game_root: Path, saves_dir: Path | None) -> list[Path]:
    roots = [Path(game_root) / "benilla-config"]
    if saves_dir is not None:
        roots.append(Path(saves_dir))
    return roots


SNAPSHOT_LIMIT = 512 << 20


def copy_snapshot(game_root: Path, saves_dir: Path | None, dest: Path) -> dict[str, Any]:
    """Read-only copy of the saves folder and benilla-config into the transaction folder (originals are only read)."""
    files = total = 0
    roots = []
    if saves_dir is not None and Path(saves_dir).is_dir():
        roots.append(("saves", Path(saves_dir)))
    if (Path(game_root) / "benilla-config").is_dir():
        roots.append(("benilla-config", Path(game_root) / "benilla-config"))
    for label, root in roots:
        for p in sorted(root.rglob("*")):
            if not p.is_file():
                continue
            if is_reparse_point(p):
                raise InstallerError(f"Refusing to copy a link: {p}", code="preserve-link")
            total += p.stat().st_size
            if total > SNAPSHOT_LIMIT:
                raise InstallerError("The saves/config folders are larger than the 512 MiB snapshot limit.",
                                     "Pass --saves-dir none to skip the copy (hash proof still applies), or clean the folder.", "snapshot-too-large")
            target = dest / label / p.relative_to(root)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, target)
            if sha256_file(target) != sha256_file(p):
                raise InstallerError(f"Snapshot copy of {p.name} did not verify", code="copy-verify")
            os.chmod(target, stat.S_IREAD)
            files += 1
    return {"path": str(dest), "files": files, "bytes": total, "roots": [str(r) for _, r in roots]}


def apply_build(build: dict[str, Any], build_dir: Path, game_root: Path, *, saves_dir: Path | None,
                progress: Progress, probe_running: bool = True, snapshot_copy: bool = False) -> dict[str, Any]:
    """Install the verified artifacts listed in ``build['targets']``; returns the receipt."""
    game_root = Path(game_root)
    targets = build["targets"]
    for t in targets:
        if t["rel"] not in ALLOWED_TARGETS:
            raise InstallerError(f"Refusing to write {t['rel']}", code="target-forbidden")
        art = build_dir / t["artifact"]
        if not art.is_file() or file_record(art) != {"bytes": t["bytes"], "sha256": t["sha256"]}:
            raise InstallerError(f"Build artifact {t['artifact']} is missing or changed since it was built",
                                 "Re-run `build` into a new folder.", "build-tampered")
    with transaction_lock(game_root):
        txn_id = new_id()
        tdir = state_dir(game_root) / "transactions" / txn_id
        backup_root = tdir / "backup"
        paths = [game_root / t["rel"] for t in targets]
        if probe_running:
            assert_game_stopped(game_root, paths)
        state = load_state(game_root)
        # pre-state must be exactly what the build was planned against
        for t in targets:
            now = classify_target(game_root, t["rel"], state)
            before = t["expect_before"]
            if (now["sha256"] if now["kind"] != "absent" else None) != before:
                raise InstallerError(f"{t['rel']} changed since the build was made",
                                     "Re-run `plan` and `build` against the current game folder. Nothing was changed.", "target-changed")
        # disk space: new files + backups + margin
        need = sum(t["bytes"] for t in targets) * 2 + sum((Path(game_root) / t["rel"]).stat().st_size
                                                          for t in targets if (Path(game_root) / t["rel"]).exists()) + (64 << 20)
        free = detect.free_bytes(game_root)
        if free < need:
            raise InstallerError(f"Not enough free disk space: need about {need >> 20} MiB, have {free >> 20} MiB.",
                                 "Free up space on the game's drive.", "disk-space")
        progress.say("snapshotting saves/config to prove they stay unchanged")
        preserved_before = _snapshot(preserve_roots(game_root, saves_dir))
        receipt: dict[str, Any] = {
            "schema": RECEIPT_SCHEMA, "id": txn_id, "kind": "install", "status": "pending", "created": utc_now(),
            "game_root": str(game_root), "build_id": build["build_id"], "recipes": build["recipes"],
            "targets": [], "preserved_files": len(preserved_before), "saves_dir": str(saves_dir) if saves_dir else None,
        }
        tdir.mkdir(parents=True)
        if snapshot_copy:
            progress.say("copying saves and config into the transaction (read-only snapshot)")
            receipt["snapshot_copy"] = copy_snapshot(game_root, saves_dir, tdir / "saves-snapshot")
        # ---- backup everything that will be replaced, verified, before any write
        for t in targets:
            live = game_root / t["rel"]
            entry: dict[str, Any] = {"rel": t["rel"], "existed_before": live.exists(), "before": None, "backup": None,
                                     "after": {"bytes": t["bytes"], "sha256": t["sha256"]},
                                     "owned_before": state.get("targets", {}).get(t["rel"])}
            if live.exists():
                rec = file_record(live)
                dest = backup_root / t["rel"]
                progress.say(f"backing up {t['rel']} ({rec['bytes'] >> 20} MiB)")
                _copy_verified(live, dest, rec["sha256"])
                entry["before"] = rec
                entry["backup"] = str(Path("backup") / t["rel"]).replace("\\", "/")
            receipt["targets"].append(entry)
        receipt["status"] = "backup-verified"
        write_json_atomic(tdir / "receipt.json", receipt)
        applied: list[dict[str, Any]] = []
        try:
            if probe_running:
                assert_game_stopped(game_root, paths)
            for t, entry in zip(targets, receipt["targets"]):
                live = game_root / t["rel"]
                temp = live.with_name(live.name + ".as-new-" + uuid.uuid4().hex[:8])
                progress.say(f"installing {t['rel']}")
                try:
                    _copy_verified(build_dir / t["artifact"], temp, t["sha256"])
                    if live.exists():
                        _wait_exclusive(live)
                    applied.append(entry)
                    _replace_atomic(temp, live)
                finally:
                    temp.unlink(missing_ok=True)
                if sha256_file(live) != t["sha256"]:
                    raise InstallerError(f"{t['rel']} did not verify after install", code="post-verify")
            after = _snapshot(preserve_roots(game_root, saves_dir))
            if after != preserved_before:
                raise InstallerError("Save/config files changed during installation (they must never)", code="preserve-changed")
            new_state = load_state(game_root)
            for t in targets:
                new_state["targets"][t["rel"]] = {"sha256": t["sha256"], "bytes": t["bytes"], "transaction": txn_id,
                                                  "recipes": {r["id"]: r["version"] for r in build["recipes"]}}
            new_state["transactions"] = list(new_state.get("transactions", [])) + [txn_id]
            write_json_atomic(state_dir(game_root) / "state.json", new_state)
            receipt.update(status="installed-and-verified", completed=utc_now(), saves_unchanged=True, config_unchanged=True)
            write_json_atomic(tdir / "receipt.json", receipt)
            return receipt
        except BaseException as exc:
            errors = _restore_entries(game_root, tdir, applied)
            receipt.update(status="failed-and-rolled-back" if not errors else "failed-rollback-incomplete",
                           failed=utc_now(), error=str(exc), rollback_errors=errors)
            with contextlib.suppress(Exception):
                write_json_atomic(tdir / "receipt.json", receipt)
            if errors:
                raise InstallerError(f"Install failed ({exc}) and restoring needs attention: {'; '.join(errors)}",
                                     f"Backups are preserved in {tdir}. Run `rollback --transaction {txn_id}` after closing the game.",
                                     "install-rollback-incomplete") from exc
            if isinstance(exc, InstallerError):
                exc.message = f"Install failed and was rolled back: {exc.message}"
                raise
            raise InstallerError(f"Install failed and was rolled back: {exc}", code="install-failed") from exc


def _restore_entries(game_root: Path, tdir: Path, entries: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    for entry in reversed(entries):
        live = Path(game_root) / entry["rel"]
        try:
            if entry["existed_before"]:
                backup = tdir / entry["backup"]
                temp = live.with_name(live.name + ".as-restore-" + uuid.uuid4().hex[:8])
                try:
                    _copy_verified(backup, temp, entry["before"]["sha256"])
                    _replace_atomic(temp, live)
                finally:
                    temp.unlink(missing_ok=True)
            elif live.exists():
                live.unlink()
        except Exception as exc:  # keep restoring the rest
            errors.append(f"{entry['rel']}: {exc}")
    return errors


def load_receipt(game_root: Path, txn: str) -> tuple[Path, dict[str, Any]]:
    root = state_dir(game_root) / "transactions"
    if txn == "latest":
        state = load_state(game_root)
        open_ids = []
        for t in state.get("transactions", []):
            rp = root / t / "receipt.json"
            if rp.is_file() and read_json(rp).get("status") == "installed-and-verified":
                open_ids.append(t)
        if not open_ids:
            raise InstallerError("No installed (not yet rolled back) installer transaction for this game folder.",
                                 code="no-transaction")
        txn = open_ids[-1]
    if not txn or "/" in txn or "\\" in txn or ".." in txn:
        raise InstallerError("Invalid transaction id", code="bad-transaction")
    tdir = root / txn
    path = tdir / "receipt.json"
    if not path.is_file():
        raise InstallerError(f"Transaction {txn} not found", f"Looked in {root}", "no-transaction")
    receipt = read_json(path)
    if receipt.get("schema") != RECEIPT_SCHEMA:
        raise InstallerError("Receipt has an unknown schema", code="bad-receipt")
    return tdir, receipt


def rollback(game_root: Path, txn: str, *, apply: bool, saves_dir: Path | None, progress: Progress,
             probe_running: bool = True) -> dict[str, Any]:
    """Restore a transaction's backups after proving the files are still exactly what it installed."""
    game_root = Path(game_root)
    tdir, receipt = load_receipt(game_root, txn)
    if receipt["status"] == "rolled-back":
        raise InstallerError(f"Transaction {receipt['id']} was already rolled back.", code="already-rolled-back")
    if receipt["status"] != "installed-and-verified":
        raise InstallerError(f"Transaction {receipt['id']} has status {receipt['status']!r}; only completed installs are rolled back.",
                             "Failed installs restore themselves; inspect the receipt if files look wrong.", "not-rollbackable")
    plan = []
    problems = []
    for entry in receipt["targets"]:
        live = game_root / entry["rel"]
        if entry["rel"] not in ALLOWED_TARGETS:
            raise InstallerError(f"Receipt names a forbidden target {entry['rel']}", code="target-forbidden")
        now = file_record(live) if live.exists() else None
        installed = entry["after"]["sha256"]
        before = entry["before"]["sha256"] if entry["before"] else None
        if now is not None and now["sha256"] == installed:
            action = "restore" if entry["existed_before"] else "delete"
        elif (now is None and not entry["existed_before"]) or (now is not None and before and now["sha256"] == before):
            action = "already-original"
        else:
            action = "refuse"
            problems.append(f"{entry['rel']} no longer matches what transaction {receipt['id']} installed "
                            f"(now {now['sha256'][:12] + '...' if now else 'missing'}, installed {installed[:12]}...)")
        plan.append({"rel": entry["rel"], "action": action})
    # a newer owned transaction must be rolled back first
    state = load_state(game_root)
    for entry in receipt["targets"]:
        owned = state.get("targets", {}).get(entry["rel"])
        if owned and owned.get("transaction") != receipt["id"] and not any(
                p["rel"] == entry["rel"] and p["action"] == "already-original" for p in plan):
            problems.append(f"{entry['rel']} is now owned by a newer transaction {owned.get('transaction')}; roll that back first")
    if problems:
        raise InstallerError("Refusing to roll back: " + "; ".join(problems),
                             "The files changed after this install (another install, an update or manual edits). "
                             "Nothing was touched. Roll back newer transactions first, or restore the backup manually from "
                             + str(tdir / "backup"), "rollback-refused")
    result = {"transaction": receipt["id"], "plan": plan, "applied": False}
    if not apply:
        return result
    with transaction_lock(game_root):
        paths = [game_root / e["rel"] for e in receipt["targets"]]
        if probe_running:
            assert_game_stopped(game_root, paths)
        preserved_before = _snapshot(preserve_roots(game_root, saves_dir))
        to_restore = [e for e, p in zip(receipt["targets"], plan) if p["action"] in ("restore", "delete")]
        for e in to_restore:  # verify backups exist and are intact before touching anything
            if e["existed_before"]:
                bk = tdir / e["backup"]
                if not bk.is_file() or sha256_file(bk) != e["before"]["sha256"]:
                    raise InstallerError(f"Backup of {e['rel']} is missing or damaged; nothing was changed",
                                         f"Expected {bk}", "backup-damaged")
        for e in to_restore:
            progress.say(f"restoring {e['rel']}")
            live = game_root / e["rel"]
            if e["existed_before"]:
                temp = live.with_name(live.name + ".as-restore-" + uuid.uuid4().hex[:8])
                try:
                    _copy_verified(tdir / e["backup"], temp, e["before"]["sha256"])
                    _wait_exclusive(live)
                    _replace_atomic(temp, live)
                finally:
                    temp.unlink(missing_ok=True)
                if sha256_file(live) != e["before"]["sha256"]:
                    raise InstallerError(f"{e['rel']} did not verify after restore", code="post-verify")
            else:
                _wait_exclusive(live)
                live.unlink()
        if _snapshot(preserve_roots(game_root, saves_dir)) != preserved_before:
            raise InstallerError("Save/config files changed during rollback", code="preserve-changed")
        state = load_state(game_root)
        for e in receipt["targets"]:
            if e.get("owned_before"):
                state["targets"][e["rel"]] = e["owned_before"]
            else:
                state["targets"].pop(e["rel"], None)
        write_json_atomic(state_dir(game_root) / "state.json", state)
        receipt.update(status="rolled-back", rolled_back=utc_now())
        write_json_atomic(tdir / "receipt.json", receipt)
    result["applied"] = True
    return result


def status(game_root: Path) -> dict[str, Any]:
    """Current owned targets vs their recorded hashes, and the transaction list."""
    game_root = Path(game_root)
    state = load_state(game_root)
    targets = []
    for rel, owned in sorted(state.get("targets", {}).items()):
        live = game_root / rel
        now = sha256_file(live) if live.exists() else None
        targets.append({"rel": rel, "recorded_sha256": owned["sha256"], "current_sha256": now,
                        "intact": now == owned["sha256"], "recipes": owned.get("recipes", {}),
                        "transaction": owned.get("transaction")})
    txns = []
    root = state_dir(game_root) / "transactions"
    if root.is_dir():
        for d in sorted(root.iterdir()):
            rp = d / "receipt.json"
            if rp.is_file():
                r = read_json(rp)
                txns.append({"id": r["id"], "status": r["status"], "created": r.get("created"),
                             "recipes": [x["id"] for x in r.get("recipes", [])]})
    return {"game_root": str(game_root), "installed_targets": targets, "transactions": txns}
