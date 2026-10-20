"""Detect the Azeroth Survivors build, the owner's WoW client builds, and running processes."""
from __future__ import annotations

import csv
import ctypes
import io
import os
import shutil
from pathlib import Path
from typing import Any

from .util import InstallerError, read_json, sha256_file

HERE = Path(__file__).resolve().parent.parent
EXE_NAME = "AzerothSurvivors.exe"


def known_builds() -> dict[str, Any]:
    return read_json(HERE / "known_builds.json")


def detect_game(game_root: Path, deep: bool = False) -> dict[str, Any]:
    """Identify the installed game by EXE SHA-256; ``deep`` also hashes official archives."""
    game_root = Path(game_root)
    exe = game_root / EXE_NAME
    data = game_root / "Data"
    if not exe.is_file():
        raise InstallerError(f"{EXE_NAME} not found in {game_root}",
                             "Point --game-root at the folder that contains AzerothSurvivors.exe.", "game-missing")
    if not data.is_dir():
        raise InstallerError(f"No Data folder under {game_root}", "Run the game once or re-extract the download.", "game-missing")
    kb = known_builds()
    exe_sha = sha256_file(exe)
    match = next((b for b in kb["game_builds"] if b["exe_sha256"] == exe_sha), None)
    result: dict[str, Any] = {
        "game_root": str(game_root), "exe_bytes": exe.stat().st_size, "exe_sha256": exe_sha,
        "known": match is not None,
        "version": match["version"] if match else None,
        "label": match["label"] if match else "unknown build",
        "modified_by_installer_recipe": bool(match and match["modified"]),
        "base_exe_sha256": (match.get("base_exe_sha256") if match else None) or exe_sha,
    }
    slots = []
    for p in sorted(data.glob("patch*.MPQ"), key=lambda q: q.name.casefold()):
        slots.append({"name": p.name, "bytes": p.stat().st_size})
    result["archives"] = slots
    if deep and match:
        official = {o["name"]: o for o in match.get("official_patches", [])}
        problems = []
        for s in slots:
            exp = official.get(s["name"])
            if exp:
                s["sha256"] = sha256_file(data / s["name"])
                s["matches_official"] = s["sha256"] == exp["sha256"] and s["bytes"] == exp["bytes"]
                if not s["matches_official"]:
                    problems.append(s["name"])
        result["official_archive_problems"] = problems
        result["missing_official_archives"] = sorted(set(official) - {s["name"] for s in slots})
    return result


def parse_build_info(text: str) -> list[dict[str, str]]:
    rows = list(csv.DictReader(io.StringIO(text.lstrip("﻿"), newline=""), delimiter="|"))
    return [{k.split("!", 1)[0]: v for k, v in row.items() if k is not None} for row in rows]


def detect_clients(client_root: Path) -> list[dict[str, Any]]:
    """One entry per .build.info row (Retail ``wow``, Forever ``wowf`` ...)."""
    client_root = Path(client_root)
    info = client_root / ".build.info"
    if not info.is_file():
        raise InstallerError(f"No .build.info in {client_root}",
                             "Point --client-root at your World of Warcraft folder (it contains .build.info).", "client-missing")
    if info.stat().st_size > 2 * 1024 * 1024:
        raise InstallerError(".build.info is unexpectedly large", code="client-invalid")
    products = known_builds()["client_products"]
    out = []
    for row in parse_build_info(info.read_text(encoding="utf-8-sig")):
        tags = row.get("Tags", "")
        product = row.get("Product", "")
        locales = sorted({t for t in tags.replace(":", " ").split() if len(t) == 4 and t[:2].islower() and t[2:].isupper()})
        out.append({
            "product": product, "flavor": products.get(product, product or "unknown"),
            "version": row.get("Version", ""), "build_key": row.get("Build Key", "").lower(),
            "cdn_key": row.get("CDN Key", "").lower(), "active": row.get("Active") == "1",
            "branch": row.get("Branch", ""), "locales": locales,
        })
    data = client_root / "Data" / "data"
    for entry in out:
        entry["casc_storage_present"] = data.is_dir()
    return out


def client_matches_source(clients: list[dict[str, Any]], source: dict[str, Any]) -> dict[str, Any] | None:
    for c in clients:
        if (c["product"] == source["product"] and c["version"] == source["version"]
                and c["build_key"] == source["build_key"] and c["cdn_key"] == source["cdn_key"]
                and source["locale"] in c["locales"]):
            return c
    return None


def free_bytes(path: Path) -> int:
    probe = Path(path)
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    return shutil.disk_usage(probe).free


# --- running-game detection -------------------------------------------------

def _running_exe_paths() -> list[str]:
    if os.name != "nt":
        return []
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class ENTRY(ctypes.Structure):
        _fields_ = [("dwSize", ctypes.c_ulong), ("cntUsage", ctypes.c_ulong), ("th32ProcessID", ctypes.c_ulong),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", ctypes.c_ulong),
                    ("cntThreads", ctypes.c_ulong), ("th32ParentProcessID", ctypes.c_ulong),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", ctypes.c_ulong),
                    ("szExeFile", ctypes.c_wchar * 260)]

    k32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    k32.OpenProcess.restype = ctypes.c_void_p
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)
    paths: list[str] = []
    if not snap or snap == ctypes.c_void_p(-1).value:
        return paths
    try:
        entry = ENTRY()
        entry.dwSize = ctypes.sizeof(ENTRY)
        ok = k32.Process32FirstW(ctypes.c_void_p(snap), ctypes.byref(entry))
        while ok:
            if entry.szExeFile.casefold() == EXE_NAME.casefold():
                h = k32.OpenProcess(0x1000, False, entry.th32ProcessID)
                full = ""
                if h:
                    buf = ctypes.create_unicode_buffer(1024)
                    size = ctypes.c_ulong(1024)
                    if k32.QueryFullProcessImageNameW(ctypes.c_void_p(h), 0, buf, ctypes.byref(size)):
                        full = buf.value
                    k32.CloseHandle(ctypes.c_void_p(h))
                paths.append(full or "<unreadable path>")
            ok = k32.Process32NextW(ctypes.c_void_p(snap), ctypes.byref(entry))
    finally:
        k32.CloseHandle(ctypes.c_void_p(snap))
    return paths


def game_running(game_root: Path) -> list[str]:
    """Paths of AzerothSurvivors.exe processes that run from ``game_root`` (or are unreadable)."""
    target = os.path.normcase(os.path.realpath(Path(game_root) / EXE_NAME))
    hits = []
    for p in _running_exe_paths():
        if p == "<unreadable path>" or os.path.normcase(os.path.realpath(p)) == target:
            hits.append(p)
    return hits


def file_lock_state(path: Path) -> str | None:
    """None when ``path`` can be opened exclusively; otherwise the reason."""
    if not path.exists():
        return None
    try:
        fd = os.open(path, os.O_RDWR | getattr(os, "O_BINARY", 0))
    except OSError as exc:
        return f"{type(exc).__name__}: {exc}"
    os.close(fd)
    if os.name == "nt":
        # Plain os.open shares read/write; probe true exclusivity with CreateFile share mode 0.
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateFileW.restype = ctypes.c_void_p
        h = k32.CreateFileW(str(path), 0xC0000000, 0, None, 3, 0x80, None)
        if h in (None, ctypes.c_void_p(-1).value):
            return f"in use by another process (error {ctypes.get_last_error()})"
        k32.CloseHandle(ctypes.c_void_p(h))
    return None
