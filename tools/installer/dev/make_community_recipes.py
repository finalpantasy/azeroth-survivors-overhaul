#!/usr/bin/env python3
"""Generate the recipes for everything already installed or staged in the project (read-only on the project).

  hd-terrain-community        patch-1.MPQ  671 community textures (VanillaHD), user-supplied pinned files
  hd-spells-community         patch-0.MPQ  12 spell payloads (community backports + 2 derived models)
  renderer-sharpen-ffx        EXE patch    relocated ffx_glow shader with contrast-adaptive sharpen (experimental)

and the profiles that order them. Re-run after the source MPQs/EXEs change; the script verifies every hash it writes.
"""
from __future__ import annotations

import base64
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
INSTALLER = HERE.parent
sys.path.insert(0, str(INSTALLER))
from asinstall import mpq  # noqa: E402
from asinstall.recipe import validate_recipe  # noqa: E402
from asinstall.steps import apply_byte_edits, load_registry  # noqa: E402

BS = chr(92)
PROJECT = Path(".")
MAIN = PROJECT / "AzerothSurvivors"
OFFICIAL_EXE = "6382e9ae654295293b82d48fb07a1aa999132775bda10c6c6f80d973d2c1ddfb"
WATER_EXE = "524e83499dd8f216fadddf75c57346d03dcecb0b456b1f07f87eb6b109c35a1b"
SHARPEN_EXE = "ef6c5d2fe51aef04809e8b0b9a35158365cfe021423f8889ca4e526d97c357f2"
# v0.103.1 (2026-10-19): data patches are unchanged, so data recipes accept both EXE generations (see dev/rebase_0_103_1.py)
NEW_EXE = "3b46bad92e7771afa61c93658fd850937834a40f3671af8b1a3e1d2aeb8b63c7"
NEW_WATER_EXE = "d220c45dfb0ccc5d10bc5b7226acdf907a956d36396adf856080bb453102cc2e"
ALL_EXE = [OFFICIAL_EXE, WATER_EXE, SHARPEN_EXE, NEW_EXE, NEW_WATER_EXE]
TERRAIN_MPQ_SHA = "be6020322e2361d3666052ea6db86b15613c049093825128abc2799503398ab1"
SPELL_COMMIT_A = "dcf3647e80104d03c02e1540c61cce3ca7e24766"
SPELL_COMMIT_Y = "682896e191ac0a572090a3daa7714348b52180b6"
RAW = "https://raw.githubusercontent.com/{repo}/{commit}/{path}"


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def write(name: str, doc: dict) -> None:
    validate_recipe(doc, None, load_registry())
    (INSTALLER / "recipes" / name).write_text(json.dumps(doc, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    print("wrote", name, (INSTALLER / "recipes" / name).stat().st_size, "bytes")


def byte_diff(a: bytes, b: bytes, gap: int = 8) -> tuple[list[dict], bytes]:
    """Same-length edits over the common prefix + the appended tail that turn ``a`` into ``b``."""
    n = min(len(a), len(b))
    runs: list[list[int]] = []
    i = 0
    while i < n:
        if a[i] != b[i]:
            j = i
            while j < n and a[j] != b[j]:
                j += 1
            if runs and i - runs[-1][1] <= gap:
                runs[-1][1] = j
            else:
                runs.append([i, j])
            i = j
        else:
            i += 1
    if len(b) < len(a):
        raise SystemExit("target shorter than source; not supported")
    edits = [{"offset": s, "length": e - s, "expect_sha256": sha(a[s:e]), "new_b64": base64.b64encode(b[s:e]).decode()} for s, e in runs]
    return edits, b[len(a):]


def terrain() -> None:
    man = json.loads((MAIN / "mod-source/source-manifest.json").read_text("utf-8"))
    by_path = {m["path"].replace("/", BS).casefold(): m for m in man}
    files, outputs = [], []
    with mpq.MpqArchive(MAIN / "mod-source/terrain-hd.MPQ") as arc:
        for name in sorted(n for n in arc.names() if n.casefold() != "(listfile)"):
            m = by_path[name.casefold()]
            assert sha(arc.read(name)) == m["sha256"], name
            src_name = f"{m['repo']}/{m['path']}"
            files.append({"name": src_name, "bytes": m["bytes"], "sha256": m["sha256"],
                          "origin": RAW.format(repo="VanillaHD/" + m["repo"], commit=m["commit"], path=m["path"]).replace(" ", "%20")})
            outputs.append({"slot": "patch-1.MPQ", "path": name, "from": "src:" + src_name, "sha256": m["sha256"]})
    write("hd-terrain-community.json", {
        "schema": "as-recipe/1", "id": "hd-terrain-community", "version": "1.0.0",
        "title": "HD terrain texture overlay (VanillaHD community textures)",
        "description": f"Packs {len(outputs)} community terrain textures into Data/patch-1.MPQ (below the official patches). "
                       "The textures are NOT from your WoW install: you obtain the pinned files yourself (see the source instructions) "
                       "and the installer verifies every byte against the hashes recorded here.",
        "status": "ready",
        "requires": {"game": {"exe_sha256": ALL_EXE, "versions": ["0.103.0", "0.103.1"]}, "tools": ["mpq-packer"], "disk_mb": 900},
        "sources": {"vanillahd": {
            "kind": "user-supplied",
            "instructions": "Get the pinned community files into one folder laid out as <folder>/Patch-B/<path> (VanillaHD/Patch-B at commit "
                            "a112a137f3183d53cdd4e88c94f9c81fbeaa294a). Easiest: python tools/fetch_community_sources.py --recipe "
                            "hd-terrain-community --dest <folder> --yes (an explicit, separate download of only these hash-pinned files). "
                            "Then pass --source vanillahd=<folder>. Review the repositories' licenses before use; the installer never "
                            "redistributes these files.",
            "files": files}},
        "steps": [{"id": "src", "op": "user-source", "source": "vanillahd"}],
        "outputs": {"mpq_files": outputs},
        "acceptance": [{"check": "mpq-has", "slot": "patch-1.MPQ", "path": o["path"], "sha256": o["sha256"]}
                       for o in (outputs[0], outputs[len(outputs) // 2], outputs[-1])],
        "provenance": {"manifest": "mod-source/source-manifest.json", "repos": {"VanillaHD/Patch-B": "a112a137f3183d53cdd4e88c94f9c81fbeaa294a"},
                       "installed_archive_sha256": TERRAIN_MPQ_SHA,
                       "note": "pinned community textures; not latest-retail proof. The live patch-1.MPQ stores the same 671 files "
                               "uncompressed; the installer's packer compresses, so the archive bytes differ but every file is byte-identical."},
    })


def spells() -> dict[str, str]:
    live = {}
    with mpq.MpqArchive(MAIN / "mod-source/spell-hd-integrated/spell-hd.MPQ") as arc:
        for n in arc.names():
            if n.casefold() != "(listfile)":
                live[n] = arc.read(n)
    src_dir = MAIN / "analysis/spell-hd-phase2/source/spells"
    prov = json.loads((MAIN / "mod-source/spell-hd-phase2/provenance.json").read_text("utf-8"))
    files, steps, outputs = [], [], []
    names = {}

    def add_source(name: str, repo: str, commit: str, path: str, data: bytes) -> None:
        files.append({"name": name, "bytes": len(data), "sha256": sha(data), "origin": RAW.format(repo=repo, commit=commit, path=path)})
        names[name] = data

    # simple copies -------------------------------------------------------------------
    clouds = live[f"Spells{BS}CLOUDS8X8.BLP"]
    add_source("yuritsuki-wow-spells/SPELLS/CLOUDS8X8.BLP", "yuritsuki/wow-spells", SPELL_COMMIT_Y, "SPELLS/CLOUDS8X8.BLP", clouds)
    outputs.append({"slot": "patch-0.MPQ", "path": f"Spells{BS}CLOUDS8X8.BLP", "from": "src:yuritsuki-wow-spells/SPELLS/CLOUDS8X8.BLP", "sha256": sha(clouds)})
    for internal, repo_path in ((f"Spells{BS}Fireball_Missile_Low.M2", "spells/Fireball_Missile_Low.M2"),
                                (f"Spells{BS}T_VFX_FIRE_ANIM02_64.BLP", "spells/T_VFX_FIRE_ANIM02_64.BLP")):
        data = live[internal]
        add_source("Patch-A/" + repo_path, "VanillaHD/Patch-A", SPELL_COMMIT_A, repo_path, data)
        outputs.append({"slot": "patch-0.MPQ", "path": internal, "from": "src:Patch-A/" + repo_path, "sha256": sha(data)})
    # renamed textures ---------------------------------------------------------------------
    for f in prov["files"]:
        if f["kind"] != "texture":
            continue
        base = Path(f["source_path"]).name
        group = "ArcaneExplosion" if base.upper().startswith("SHOCKWAVE") else "Frostbolt"
        internal = f"Spells{BS}AzerothSurvivorsHD{BS}Phase2{BS}{group}{BS}{base}"
        match = [n for n in live if n.casefold() == internal.casefold()]
        assert match, internal
        data = live[match[0]]
        assert sha(data) == f["source_sha256"], base
        add_source("Patch-A/" + f["source_path"], "VanillaHD/Patch-A", SPELL_COMMIT_A, f["source_path"], data)
        outputs.append({"slot": "patch-0.MPQ", "path": match[0], "from": "src:Patch-A/" + f["source_path"], "sha256": sha(data)})
    # derived models: community model + small authored byte edits ---------------------------------
    for step_id, src_file, internal, repo_path in (
            ("frostbolt", "Ice_Missile_Low.M2", f"Spells{BS}Frostbolt.m2", "spells/Ice_Missile_Low.M2"),
            ("arcane", "ArcaneExplosion_Base.M2", f"Spells{BS}ArcaneExplosion_Base.m2", "spells/ArcaneExplosion_Base.M2")):
        a = (src_dir / src_file).read_bytes()
        b = live[[n for n in live if n.casefold() == internal.casefold()][0]]
        add_source("Patch-A/" + repo_path, "VanillaHD/Patch-A", SPELL_COMMIT_A, repo_path, a)
        edits, tail = byte_diff(a, b)
        assert apply_byte_edits(a, edits, "x") + tail == b
        steps.append({"id": step_id, "op": "binary-edit", "input": "src:Patch-A/" + repo_path, "output": src_file,
                      "expect_input_sha256": sha(a), "edits": edits, "append_b64": base64.b64encode(tail).decode(),
                      "expect_output_sha256": sha(b)})
        outputs.append({"slot": "patch-0.MPQ", "path": [n for n in live if n.casefold() == internal.casefold()][0],
                        "from": f"{step_id}:{src_file}", "sha256": sha(b)})
    assert len(outputs) == len(live) == 12, (len(outputs), len(live))
    files.sort(key=lambda f: f["name"])
    outputs.sort(key=lambda o: o["path"].casefold())
    write("hd-spells-community.json", {
        "schema": "as-recipe/1", "id": "hd-spells-community", "version": "1.0.0",
        "title": "HD spell visuals: Fireball, Frostbolt, Arcane Explosion, smoke atlas (12 files)",
        "description": "Installs 12 spell payloads into Data/patch-0.MPQ. Community backports from VanillaHD/Patch-A and yuritsuki/wow-spells "
                       "(pinned, not latest-retail proof); the Frostbolt and Arcane Explosion models are the community models plus small "
                       "authored byte edits (texture aliases, one animation clamp flag) recorded in this recipe. No spell rules or sounds change.",
        "status": "ready",
        "requires": {"game": {"exe_sha256": ALL_EXE, "versions": ["0.103.0", "0.103.1"]}, "tools": ["mpq-packer"], "disk_mb": 64},
        "sources": {"community": {
            "kind": "user-supplied",
            "instructions": "Get the pinned community files into one folder: <folder>/Patch-A/spells/<file> (VanillaHD/Patch-A at "
                            f"{SPELL_COMMIT_A}) and <folder>/yuritsuki-wow-spells/SPELLS/CLOUDS8X8.BLP (yuritsuki/wow-spells at "
                            f"{SPELL_COMMIT_Y}). Easiest: python tools/fetch_community_sources.py --recipe hd-spells-community "
                            "--dest <folder> --yes (an explicit, separate download of only these hash-pinned files). Then pass "
                            "--source community=<folder>. Review the repositories' licenses; the installer never redistributes them.",
            "files": files}},
        "steps": [{"id": "src", "op": "user-source", "source": "community"}] + steps,
        "outputs": {"mpq_files": outputs},
        "acceptance": [
            {"check": "mpq-has", "slot": "patch-0.MPQ", "path": o["path"], "sha256": o["sha256"]}
            for o in outputs if o["path"].lower().endswith(".m2")],
        "provenance": {"integration_review": "analysis/spell-hd-phase2-isolated/integration-review.md",
                       "build_script": "analysis/spell-hd-phase2-isolated/build_phase2.py (the byte edits here reproduce its output)",
                       "installed_archive_sha256_with_mace": "144d9b4274086657f0e0e6f3307956237d7c7c97c0c71a53e30c914c82dc2e95"},
        "limits": ["corrected Frostbolt alias appearance not directly acquired in final run",
                   "repeated-cast stress and late-wave performance unmeasured"],
    })
    return {n: sha(d) for n, d in live.items()}


def sharpen() -> None:
    a = (MAIN / "AzerothSurvivors.exe").read_bytes()
    b = (PROJECT / "previews/renderer-sharpen-v0.103.0/AzerothSurvivors.exe").read_bytes()
    assert sha(a) == OFFICIAL_EXE and sha(b) == SHARPEN_EXE
    edits, tail = byte_diff(a, b, gap=16)
    assert apply_byte_edits(a, edits, "exe") + tail == b
    water = json.loads((INSTALLER / "recipes/water-shader-adt-liquid.json").read_text("utf-8"))
    wspans = [(e["offset"], e["offset"] + e["length"]) for e in water["outputs"]["exe_patches"][0]["edits"]]
    overlap = [(e["offset"], e["length"]) for e in edits if any(e["offset"] < we and ws < e["offset"] + e["length"] for ws, we in wspans)]
    print("sharpen edits", len(edits), "append", len(tail), "overlap with water:", overlap)
    write("renderer-sharpen-ffx.json", {
        "schema": "as-recipe/1", "id": "renderer-sharpen-ffx", "version": "0.1.0",
        "title": "Renderer post-process: contrast-adaptive sharpen in the FFX combine pass",
        "description": "Replaces the embedded ffx_glow shader text (relocated to a new executable section) with a version that adds a "
                       "contrast-adaptive sharpen (strength 0.5). The executable is the official v0.103.0 plus the byte edits and the "
                       "appended shader section recorded here; everything else is identical. Static validation passed; runtime A/B pending.",
        "status": "experimental",
        "requires": {"game": {"exe_sha256": [OFFICIAL_EXE], "versions": ["0.103.0"]}, "disk_mb": 600},
        "outputs": {"exe_patches": [{"target": "AzerothSurvivors.exe", "expect_exe_sha256": OFFICIAL_EXE, "result_exe_sha256": SHARPEN_EXE,
                                     "edits": edits, "append_b64": base64.b64encode(tail).decode()}]},
        "acceptance": [{"check": "exe-sha256", "equals": SHARPEN_EXE}],
        "provenance": {"stage_script": "tools/renderer-upgrade/stage_renderer.py", "shader_patch": "tools/renderer-upgrade/patches/ffx_post.wgsl",
                       "candidate": "previews/renderer-sharpen-v0.103.0", "settings": {"sharpen": 0.5, "fxaa": 0.0, "vibrance": 0.0, "water": False}},
        "limits": ["runtime A/B and gpu cost not yet confirmed by the renderer worker",
                   "adds a PE section: cannot be combined with another recipe that also changes the PE headers"],
    })


def profiles() -> None:
    folder = INSTALLER / "profiles"
    folder.mkdir(exist_ok=True)
    cur = {"schema": "as-profile/1", "id": "current-upgrade", "version": "1.0.0",
           "title": "Current installed upgrade (HD terrain, spell visuals, Retail silver mace)",
           "description": "Reproduces what is installed in the main game today: terrain in patch-1, spells and the mace in patch-0.",
           "recipes": ["hd-terrain-community", "hd-spells-community", "retail-mace-kultiras-silver"],
           "allow_experimental": False,
           "expect_slot_sha256": {"patch-0.MPQ": "144d9b4274086657f0e0e6f3307956237d7c7c97c0c71a53e30c914c82dc2e95"}}
    ren = {"schema": "as-profile/1", "id": "current-upgrade-renderer", "version": "0.1.0",
           "title": "Current upgrade plus the staged renderer patches (experimental)",
           "description": "Everything in current-upgrade plus the ADT water shader patch and the renderer sharpen pass. Both are experimental "
                          "and edit the EXE; their byte edits are disjoint so they compose into one patched executable.",
           "recipes": ["hd-terrain-community", "hd-spells-community", "retail-mace-kultiras-silver", "water-shader-adt-liquid", "renderer-sharpen-ffx"],
           "allow_experimental": True, "expect_slot_sha256": cur["expect_slot_sha256"]}
    for doc in (cur, ren):
        (folder / f"{doc['id']}.json").write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        print("wrote profile", doc["id"])


if __name__ == "__main__":
    which = sys.argv[1:] or ["terrain", "spells", "sharpen", "profiles"]
    for w in which:
        {"terrain": terrain, "spells": spells, "sharpen": sharpen, "profiles": profiles}[w]()
