"""Shared helpers: hashing, errors, progress, atomic JSON, process priority, heavy lock."""
from __future__ import annotations

import contextlib
import ctypes
import hashlib
import json
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

HEX64 = re.compile(r"^[0-9a-f]{64}$")
HEX32 = re.compile(r"^[0-9a-f]{32}$")


class InstallerError(Exception):
    """Failure with an actionable hint. ``code`` is a stable machine-readable token."""

    def __init__(self, message: str, hint: str | None = None, code: str = "error") -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.code = code

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.message + (f" (hint: {self.hint})" if self.hint else "")


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path, chunk: int = 4 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def file_record(path: Path) -> dict[str, Any]:
    return {"bytes": path.stat().st_size, "sha256": sha256_file(path)}


class Progress:
    """Plain-text progress to stderr (stdout is reserved for results)."""

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self._start = time.monotonic()

    def say(self, message: str) -> None:
        if self.enabled:
            print(f"[{time.monotonic() - self._start:6.1f}s] {message}", file=sys.stderr, flush=True)

    def step(self, index: int, total: int, message: str) -> None:
        self.say(f"step {index}/{total}: {message}")


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex[:8])
    try:
        with open(temp, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            with contextlib.suppress(OSError):
                temp.unlink()


def read_json(path: Path, limit: int = 8 * 1024 * 1024) -> Any:
    if path.stat().st_size > limit:
        raise InstallerError(f"JSON file is unexpectedly large: {path}")
    def no_dupes(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, val in pairs:
            if key in out:
                raise InstallerError(f"Duplicate key {key!r} in {path}")
            out[key] = val
        return out
    with open(path, "r", encoding="utf-8-sig") as stream:
        return json.load(stream, object_pairs_hook=no_dupes)


def is_reparse_point(path: Path) -> bool:
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return bool(getattr(st, "st_file_attributes", 0) & 0x400) or path.is_symlink()


def is_within(child: Path, parent: Path) -> bool:
    try:
        Path(os.path.realpath(child)).relative_to(Path(os.path.realpath(parent)))
        return True
    except ValueError:
        return False


def lower_hex(value: Any, length: int, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{%d}" % length, value):
        raise InstallerError(f"{label} must be {length} hex characters")
    return value.lower()


def lower_priority() -> None:
    """Start heavy work at BelowNormal priority (children inherit it)."""
    if os.name != "nt":
        return
    with contextlib.suppress(Exception):
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), 0x00004000)  # BELOW_NORMAL_PRIORITY_CLASS


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            kernel32.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code))
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(ctypes.c_void_p(handle))
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


@contextlib.contextmanager
def heavy_lock(path: Path | None, what: str, owner: str = "installer", wait: float = 0.0) -> Iterator[None]:
    """Hold one heavy slot (heavy.lock, else heavy-2.lock beside it) while heavy work runs.

    Waits up to `wait` seconds for a free slot and for locks/exclusive.lock to clear.
    """
    if path is None:
        yield
        return
    slots = [path] + ([path.with_name("heavy-2.lock")] if path.name == "heavy.lock" else [])
    exclusive = path.with_name("exclusive.lock")
    deadline = time.monotonic() + max(0.0, wait)
    while True:
        if not exclusive.exists():
            for slot in slots:
                stack = contextlib.ExitStack()
                try:
                    stack.enter_context(_heavy_lock_one(slot, what, owner, 0.0))
                except InstallerError:
                    continue
                with stack:
                    yield
                return
        if time.monotonic() >= deadline:
            raise InstallerError("No heavy-work slot is free" if not exclusive.exists() else "An exclusive lock is held",
                                 "Wait for the other worker or pass --heavy-lock-wait SECONDS; locks are in " + str(path.parent),
                                 "heavy-lock-held")
        time.sleep(1.0)


@contextlib.contextmanager
def _heavy_lock_one(path: Path | None, what: str, owner: str = "installer", wait: float = 0.0) -> Iterator[None]:
    if path is None:
        yield
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"owner": owner, "pid": os.getpid(), "what": what, "started": utc_now()}
    deadline = time.monotonic() + max(0.0, wait)
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                other = json.loads(path.read_text(encoding="utf-8"))
                other_pid = int(other.get("pid", 0))
            except (OSError, ValueError, TypeError):
                other, other_pid = {}, 0
            if other_pid and pid_alive(other_pid):
                if time.monotonic() < deadline:
                    time.sleep(0.5)
                    continue
                raise InstallerError(
                    f"The heavy-work lock is held by {other.get('owner')!r} (pid {other_pid}: {other.get('what')}).",
                    "Wait for it to finish or do non-heavy work; the lock file is " + str(path),
                    "heavy-lock-held")
            with contextlib.suppress(OSError):
                path.unlink()  # stale lock from a dead process
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream)
        break
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            path.unlink()


def safe_rel(value: str, label: str) -> str:
    """Validate a relative path used inside work dirs or recipes (forward or back slashes)."""
    if not isinstance(value, str) or not value:
        raise InstallerError(f"{label} must be a non-empty path")
    norm = value.replace("\\", "/")
    if norm.startswith("/") or re.match(r"^[A-Za-z]:", norm) or "\x00" in norm:
        raise InstallerError(f"{label} must be a relative path: {value!r}")
    parts = norm.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise InstallerError(f"{label} contains an unsafe segment: {value!r}")
    return "/".join(parts)
