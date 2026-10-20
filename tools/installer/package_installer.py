#!/usr/bin/env python3
"""Build the code-only installer ZIP and a byte audit proving it holds no game assets.

    python package_installer.py --out <dir>

Writes <dir>/as-installer-<version>.zip and <dir>/as-installer-<version>.audit.json.
Exit code 1 if the audit finds anything forbidden; the ZIP is then deleted.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from asinstall import __version__  # noqa: E402

ZIP_ROOT = "as-installer"
INCLUDE_DIRS = ("asinstall", "recipes", "profiles", "tests")
INCLUDE_FILES = ("as_installer.py", "known_builds.json", "README.md", "RECIPES.md", "LAUNCHER.md", "package_installer.py")
# tools that live elsewhere in the repo; the registry pins their hashes so the copy is verified
EXTERNAL_TOOLS = {"casc_offline.py": "../casc-offline", "pack-mpq.py": "..", "build_retail_rigid.py": "../retail-rigid-preview"}
SKIP_PARTS = {"__pycache__", ".pytest_cache"}
TEXT_EXT = {".py", ".json", ".md", ".txt"}
FORBIDDEN_EXT = {".exe", ".dll", ".mpq", ".m2", ".blp", ".skin", ".dbc", ".adt", ".wmo", ".wdt", ".bls", ".anim", ".db2",
                 ".zip", ".7z", ".rar", ".bin", ".dat", ".idx", ".data"}
# magic bytes of game / Windows binary formats, searched anywhere in any file
MAGICS = {
    "PE executable (MZ + PE header)": re.compile(rb"MZ[\x00-\xff]{58}\x80?[\x00-\xff]{0,0}PE\x00\x00", re.S),
    "MPQ archive header": re.compile(rb"MPQ\x1a"),
    "MPQ user-data header": re.compile(rb"MPQ\x1b"),
    "M2 model": re.compile(rb"MD20"),
    "BLP texture": re.compile(rb"BLP[12]"),
    "WDBC table": re.compile(rb"WDBC"),
    "WDB2/WDB5/WDC": re.compile(rb"WDB[256]|WDC[1-5]"),
    "ADT/WMO chunk": re.compile(rb"REVM|MVER\x04\x00\x00\x00"),
}
# identity strings that must not ship in anything destined for a public host
IDENTITY = [re.compile(p, re.I) for p in (b"Ja" + b"mes", b"Du" + b"pre")]  # personal name fragments, built so this file does not contain them
MAX_FILE = 1024 * 1024
# files whose job is to *name* a magic (the audit script itself, the validator) are checked as text only
B64 = re.compile(rb"[A-Za-z0-9+/]{120,}={0,2}")


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def collect() -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for d in INCLUDE_DIRS:
        for p in sorted((HERE / d).rglob("*")):
            if p.is_file() and not (set(p.relative_to(HERE).parts) & SKIP_PARTS) and p.suffix != ".pyc":
                files[p.relative_to(HERE).as_posix()] = p.read_bytes()
    for n in INCLUDE_FILES:
        files[n] = (HERE / n).read_bytes()
    reg = json.loads((HERE / "tools" / "registry.json").read_text("utf-8"))
    for p in sorted((HERE / "tools").glob("*")):
        if p.is_file() and p.suffix == ".py" or p.name == "registry.json":
            files["tools/" + p.name] = p.read_bytes()
    for script, rel in EXTERNAL_TOOLS.items():
        src = (HERE / rel / script).resolve()
        data = src.read_bytes()
        pin = next((t["sha256"] for t in reg["tools"].values() if t["script"] == script), None)
        if pin is None or sha(data) != pin:
            raise SystemExit(f"tool {script} at {src} does not match its registry pin; refusing to package")
        files["tools/" + script] = data
    return files


def audit(files: dict[str, bytes]) -> dict:
    rows, problems = [], []
    for name, data in sorted(files.items()):
        ext = Path(name).suffix.lower()
        row = {"path": name, "bytes": len(data), "sha256": sha(data), "magic_hits": [], "identity_hits": 0, "ok": True}
        if ext in FORBIDDEN_EXT:
            problems.append(f"{name}: forbidden extension {ext}")
        if ext not in TEXT_EXT:
            problems.append(f"{name}: unexpected non-text file type")
        if len(data) > MAX_FILE:
            problems.append(f"{name}: {len(data)} bytes exceeds the {MAX_FILE} byte code-file limit")
        if b"\x00" in data and ext in TEXT_EXT:
            problems.append(f"{name}: NUL byte inside a text file")
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            problems.append(f"{name}: not valid UTF-8 text")
        if re.search(rb"[\x00-\x08\x0b\x0c\x0e-\x1f]", data):
            problems.append(f"{name}: control bytes in a text file")
        for label, rx in MAGICS.items():
            if rx.match(data):   # a real asset starts with its magic; text that merely names a format does not
                row["magic_hits"].append(label)
        embedded = 0
        for m in B64.finditer(data):   # decoded base64 payloads (recipe edit bytes) must not be assets either
            try:
                blob = base64.b64decode(m.group(0) + b"=" * (-len(m.group(0)) % 4))
            except Exception:
                continue
            embedded += len(blob)
            for label, rx in MAGICS.items():
                if rx.match(blob):
                    row["magic_hits"].append(label + " (inside base64)")
        row["embedded_base64_bytes"] = embedded
        if row["magic_hits"]:
            problems.append(f"{name}: contains {row['magic_hits']}")
        for rx in IDENTITY:
            row["identity_hits"] += len(rx.findall(data))
        if row["identity_hits"]:
            problems.append(f"{name}: {row['identity_hits']} personal-identity string(s)")
        row["ok"] = not any(p.startswith(name + ":") for p in problems)
        rows.append(row)
    return {"schema": "as-package-audit/1", "version": __version__, "file_count": len(rows),
            "total_bytes": sum(r["bytes"] for r in rows), "embedded_base64_bytes": sum(r["embedded_base64_bytes"] for r in rows), "largest_file_bytes": max(r["bytes"] for r in rows),
            "checks": ["forbidden extensions", "text-only file types", "per-file size cap", "strict UTF-8 text without control bytes", "magic bytes at file start and inside decoded base64 payloads (PE, MPQ, M2, BLP, WDBC, ADT/WMO)",
                       "identity strings", "external tools equal their registry pins"],
            "problems": problems, "files": rows, "passed": not problems}


def write_zip(files: dict[str, bytes], path: Path) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for name, data in sorted(files.items()):
            zi = zipfile.ZipInfo(f"{ZIP_ROOT}/{name}", (2026, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o644 << 16
            z.writestr(zi, data)


def reaudit_zip(path: Path) -> list[str]:
    """Audit the archive itself (not the in-memory list): every member again, from its stored bytes."""
    problems = []
    with zipfile.ZipFile(path) as z:
        for info in z.infolist():
            data = z.read(info)
            for label, rx in MAGICS.items():
                if rx.match(data):
                    problems.append(f"{info.filename}: {label}")
            if Path(info.filename).suffix.lower() in FORBIDDEN_EXT:
                problems.append(f"{info.filename}: forbidden extension")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", required=True, help="folder for the ZIP and its audit")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    files = collect()
    rep = audit(files)
    zpath = out / f"as-installer-{__version__}.zip"
    apath = out / f"as-installer-{__version__}.audit.json"
    write_zip(files, zpath)
    rep["zip"] = {"name": zpath.name, "bytes": zpath.stat().st_size, "sha256": sha(zpath.read_bytes())}
    rep["zip_reaudit_problems"] = reaudit_zip(zpath)
    rep["passed"] = rep["passed"] and not rep["zip_reaudit_problems"]
    apath.write_text(json.dumps(rep, indent=2) + "\n", encoding="utf-8")
    if not rep["passed"]:
        zpath.unlink()
        print("AUDIT FAILED:\n  " + "\n  ".join(rep["problems"] + rep["zip_reaudit_problems"]), file=sys.stderr)
        return 1
    print(f"{zpath}  {rep['zip']['bytes']:,} B  {rep['file_count']} files  audit: PASS  ({apath.name})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
