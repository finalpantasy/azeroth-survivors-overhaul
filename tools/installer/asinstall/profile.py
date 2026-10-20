"""Profiles (``as-profile/1``): an ordered list of recipes that together make one reproducible game state."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .recipe import ID_RE, VERSION_RE, WRITABLE_SLOTS, Recipe, _only, _str
from .util import InstallerError, lower_hex, read_json

SCHEMA = "as-profile/1"
HERE = Path(__file__).resolve().parent.parent


@dataclass
class Profile:
    raw: dict[str, Any]
    path: Path | None = None

    @property
    def id(self) -> str:
        return self.raw["id"]

    @property
    def title(self) -> str:
        return self.raw["title"]

    @property
    def recipe_ids(self) -> list[str]:
        return list(self.raw["recipes"])

    @property
    def allow_experimental(self) -> bool:
        return bool(self.raw.get("allow_experimental"))

    @property
    def priority(self) -> list[str]:
        return list(self.raw.get("priority", []))

    @property
    def expect_slot_sha256(self) -> dict[str, str]:
        return dict(self.raw.get("expect_slot_sha256", {}))


def validate_profile(raw: Any, path: Path | None = None) -> Profile:
    where = f" ({path.name})" if path else ""
    try:
        p = _only(raw, {"schema", "id", "version", "title", "description", "recipes", "allow_experimental", "expect_slot_sha256", "priority"},
                  {"schema", "id", "version", "title", "description", "recipes"}, "profile")
        if p["schema"] != SCHEMA:
            raise InstallerError(f"Unsupported profile schema {p['schema']!r}", code="profile-invalid")
        if not ID_RE.match(p["id"]) or not VERSION_RE.match(p["version"]):
            raise InstallerError("profile id/version are malformed", code="profile-invalid")
        _str(p["title"], "title")
        _str(p["description"], "description")
        ids = p["recipes"]
        if not isinstance(ids, list) or not ids or not all(isinstance(i, str) and ID_RE.match(i) for i in ids):
            raise InstallerError("recipes must be a non-empty list of recipe ids", code="profile-invalid")
        if len(set(ids)) != len(ids):
            raise InstallerError("recipes lists an id twice", code="profile-invalid")
        prio = p.get("priority", [])
        if not isinstance(prio, list) or not all(i in ids for i in prio) or len(set(prio)) != len(prio):
            raise InstallerError("priority must list distinct recipe ids from this profile, highest first", code="profile-invalid")
        for slot, digest in p.get("expect_slot_sha256", {}).items():
            if slot not in WRITABLE_SLOTS:
                raise InstallerError(f"expect_slot_sha256 slot must be one of {WRITABLE_SLOTS}", code="profile-invalid")
            lower_hex(digest, 64, f"expect_slot_sha256.{slot}")
        if "allow_experimental" in p and not isinstance(p["allow_experimental"], bool):
            raise InstallerError("allow_experimental must be true or false", code="profile-invalid")
        return Profile(raw=p, path=path)
    except InstallerError as exc:
        exc.message = f"Profile{where}: {exc.message}"
        raise


def load_profiles(extra_dirs: list[Path] | None = None) -> dict[str, Profile]:
    out: dict[str, Profile] = {}
    for folder in [HERE / "profiles", *[Path(d) for d in extra_dirs or []]]:
        if not folder.is_dir():
            continue
        for file in sorted(folder.glob("*.json")):
            prof = validate_profile(read_json(file), file)
            if prof.id in out:
                raise InstallerError(f"Two profiles share the id {prof.id}", code="profile-invalid")
            out[prof.id] = prof
    return out


def resolve_profile(profile: Profile, catalog: dict[str, Recipe], include_experimental: bool) -> list[Recipe]:
    """The profile's recipes in order; every one must exist, be available and (if experimental) be allowed."""
    recipes: list[Recipe] = []
    for rid in profile.recipe_ids:
        rec = catalog.get(rid)
        if rec is None:
            raise InstallerError(f"Profile {profile.id} names unknown recipe {rid!r}", f"Available: {', '.join(sorted(catalog))}", "recipe-unknown")
        if not rec.available:
            raise InstallerError(f"Profile {profile.id}: recipe {rid} is unavailable: {rec.raw['unavailable_reason']}", code="recipe-unavailable")
        if rec.status == "experimental" and not (profile.allow_experimental or include_experimental):
            raise InstallerError(f"Profile {profile.id}: recipe {rid} is experimental.", "Add --allow-experimental.", "recipe-experimental")
        recipes.append(rec)
    return recipes
