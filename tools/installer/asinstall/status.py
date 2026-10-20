"""Content-based recipe status: which recipes are present in a game folder, however they got there."""
from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from . import mpq
from .recipe import Recipe
from .util import InstallerError, sha256_bytes

SLOT_REL = {"patch-0.MPQ": "Data/patch-0.MPQ", "patch-1.MPQ": "Data/patch-1.MPQ"}
EXE_REL = "AzerothSurvivors.exe"


def recipe_status(game_root: Path, recipes: list[Recipe]) -> list[dict[str, Any]]:
    """``installed``: every output file/edit is present and byte-exact. ``partial``: some. ``absent``: none.

    Reads archives and the EXE only; nothing is written.
    """
    game_root = Path(game_root)
    archives: dict[str, dict[str, str] | None] = {}

    def slot_hashes(slot: str) -> dict[str, str] | None:
        if slot not in archives:
            path = game_root / SLOT_REL[slot]
            if not path.is_file():
                archives[slot] = None
            else:
                try:
                    archives[slot] = {n.casefold(): sha256_bytes(d) for n, d in mpq.read_all(path).items()}
                except InstallerError:
                    archives[slot] = None
        return archives[slot]

    exe_path = game_root / EXE_REL
    exe_data = exe_path.read_bytes() if exe_path.is_file() and any(r.exe_patches for r in recipes) else b""
    rows = []
    for rec in recipes:
        want = have = 0
        for f in rec.mpq_files:
            want += 1
            hashes = slot_hashes(f["slot"])
            if hashes is not None and hashes.get(f["path"].casefold()) == f["sha256"]:
                have += 1
        for p in rec.exe_patches:
            for e in p["edits"]:
                want += 1
                if exe_data[e["offset"]:e["offset"] + e["length"]] == base64.b64decode(e["new_b64"]):
                    have += 1
            if p.get("append_b64"):
                want += 1
                tail = base64.b64decode(p["append_b64"])
                if tail and exe_data.endswith(tail):
                    have += 1
        if not rec.available:
            state = "unavailable"
        elif want == 0:
            state = "n/a"
        else:
            state = "installed" if have == want else ("absent" if have == 0 else "partial")
        rows.append({"id": rec.id, "version": rec.version, "status": rec.status, "state": state, "matched": have, "of": want,
                     "targets": list(rec.slots) + ([EXE_REL] if rec.exe_patches else [])})
    return rows
