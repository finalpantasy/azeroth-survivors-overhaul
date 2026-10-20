#!/usr/bin/env python3
"""Explicit, separate downloader for a recipe's user-supplied community files.

The installer itself never touches the network. This helper is the one deliberate exception, run by the
owner: it reads the ``origin`` URLs pinned in a recipe's ``user-supplied`` sources, downloads each file,
checks its size and sha256, and writes it under ``--dest/<name>``. Nothing is trusted unless the hash matches.

    python tools/fetch_community_sources.py --recipe hd-terrain-community --dest D:/as-sources/terrain --yes
    python tools/fetch_community_sources.py --recipe hd-terrain-community --dest D:/as-sources/terrain --dry-run

Allowed origins: https://raw.githubusercontent.com/ only (plus file:// for tests, with --allow-file).
Review the source repositories' licenses before using the files; this helper does not redistribute anything.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
RECIPES = HERE.parent / "recipes"
ALLOWED_HOSTS = {"raw.githubusercontent.com"}
CHUNK = 1 << 20


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def load_files(recipe_path: Path) -> list[dict]:
    recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
    files = []
    for sid, src in recipe.get("sources", {}).items():
        if src.get("kind") != "user-supplied":
            continue
        for f in src.get("files", []):
            if not all(k in f for k in ("name", "bytes", "sha256", "origin")):
                raise SystemExit(f"{recipe_path.name}: source {sid} file entry lacks name/bytes/sha256/origin: {f}")
            files.append({"source": sid, **f})
    return files


def safe_target(dest: Path, name: str) -> Path:
    target = (dest / name).resolve()
    if dest.resolve() not in target.parents:
        raise SystemExit(f"Refusing path outside --dest: {name!r}")
    return target


def check_origin(url: str, allow_file: bool) -> None:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme == "https" and parts.hostname in ALLOWED_HOSTS:
        return
    if parts.scheme == "file" and allow_file:
        return
    raise SystemExit(f"Refusing origin {url!r}: only https://{'/'.join(sorted(ALLOWED_HOSTS))} is allowed.")


def fetch_one(entry: dict, dest: Path, allow_file: bool) -> str:
    """Return 'present' (already valid), 'downloaded', or raise SystemExit on any mismatch."""
    target = safe_target(dest, entry["name"])
    if target.is_file() and target.stat().st_size == entry["bytes"] and sha256_file(target) == entry["sha256"]:
        return "present"
    check_origin(entry["origin"], allow_file)
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    h = hashlib.sha256()
    size = 0
    request = urllib.request.Request(entry["origin"], headers={"User-Agent": "as-installer-fetch/1"})
    with urllib.request.urlopen(request, timeout=60) as resp, part.open("wb") as out:
        for block in iter(lambda: resp.read(CHUNK), b""):
            size += len(block)
            if size > entry["bytes"]:
                break
            h.update(block)
            out.write(block)
    if size != entry["bytes"] or h.hexdigest() != entry["sha256"]:
        part.unlink(missing_ok=True)
        raise SystemExit(f"{entry['name']}: size/hash mismatch (got {size} bytes, sha256 {h.hexdigest()[:16]}...); not kept.")
    part.replace(target)
    return "downloaded"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--recipe", required=True, help="recipe id (recipes/<id>.json) or a path to a recipe JSON")
    ap.add_argument("--dest", required=True, type=Path, help="folder to fill; pass it to the installer as --source <id>=<folder>")
    ap.add_argument("--source", help="only this source id")
    ap.add_argument("--yes", action="store_true", help="actually download (without it nothing is fetched)")
    ap.add_argument("--dry-run", action="store_true", help="list what would be downloaded and total size")
    ap.add_argument("--allow-file", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    path = Path(args.recipe)
    if not path.is_file():
        path = RECIPES / f"{args.recipe}.json"
    if not path.is_file():
        raise SystemExit(f"Recipe not found: {args.recipe}")
    files = [f for f in load_files(path) if not args.source or f["source"] == args.source]
    if not files:
        raise SystemExit("This recipe has no user-supplied files to fetch.")
    total = sum(f["bytes"] for f in files)
    todo = [f for f in files if not (safe_target(args.dest, f["name"]).is_file()
                                     and safe_target(args.dest, f["name"]).stat().st_size == f["bytes"]
                                     and sha256_file(safe_target(args.dest, f["name"])) == f["sha256"])]
    print(f"{len(files)} file(s), {total / 1048576:.1f} MiB total; {len(todo)} to download "
          f"({sum(f['bytes'] for f in todo) / 1048576:.1f} MiB) into {args.dest}")
    for f in todo[:5]:
        print(f"  {f['name']}  <-  {f['origin']}")
    if len(todo) > 5:
        print(f"  ... and {len(todo) - 5} more")
    if args.dry_run or not args.yes:
        print("Nothing downloaded." + ("" if args.dry_run else " Re-run with --yes to download."))
        return 0
    counts = {"present": 0, "downloaded": 0}
    for f in files:
        counts[fetch_one(f, args.dest, args.allow_file)] += 1
    print(f"Done: {counts['downloaded']} downloaded, {counts['present']} already valid. All hashes verified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
