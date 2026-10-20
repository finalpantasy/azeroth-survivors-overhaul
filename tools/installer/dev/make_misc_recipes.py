"""Regenerates the water-shader EXE-patch recipe and the two documented-unavailable overlay recipes (maintainer tool).

The water recipe stores only byte-range edits (hash of each original run + the replacement bytes), computed from the
verified original EXE interval and the reviewed candidate. It does not embed the game's executable.
"""
import base64, hashlib, json, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent.parent
ROOT = HERE.parent.parent
ORIG = ROOT / "update-staging/v0.103.0/AzerothSurvivors-v0.103.0.exe"
CAND = ROOT / "analysis/renderer-rea/candidate-0.103.0/AzerothSurvivors-water-preview.exe"
START, LENGTH = 102530219, 15111
ORIG_SHA = "6382e9ae654295293b82d48fb07a1aa999132775bda10c6c6f80d973d2c1ddfb"
CAND_SHA = "524e83499dd8f216fadddf75c57346d03dcecb0b456b1f07f87eb6b109c35a1b"
sha = lambda b: hashlib.sha256(b).hexdigest()

def interval(path):
    with open(path, "rb") as f:
        f.seek(START); return f.read(LENGTH)
a, b = interval(ORIG), interval(CAND)
edits, i, GAP = [], 0, 8
while i < LENGTH:
    if a[i] == b[i]:
        i += 1; continue
    j = i
    last = i
    while j < LENGTH and j - last <= GAP:
        if a[j] != b[j]: last = j
        j += 1
    end = last + 1
    edits.append({"offset": START + i, "length": end - i, "expect_sha256": sha(a[i:end]), "new_b64": base64.b64encode(b[i:end]).decode()})
    i = end
water = {
  "schema": "as-recipe/1", "id": "water-shader-adt-liquid", "version": "0.1.0",
  "title": "ADT water shading preview (embedded liquid WGSL patch)",
  "description": "Edits the embedded ADT liquid shader inside a copy of the v0.103.0 executable (normal ripple, Fresnel sky tint, glint). "
                 "The patch is a set of hash-guarded byte ranges applied to your own EXE; no executable is distributed.",
  "status": "experimental", "default": False,
  "requires": {"game": {"exe_sha256": [ORIG_SHA], "versions": ["0.103.0"]}, "disk_mb": 400},
  "outputs": {"exe_patches": [{"target": "AzerothSurvivors.exe", "expect_exe_sha256": ORIG_SHA, "result_exe_sha256": CAND_SHA, "edits": edits}]},
  "acceptance": [{"check": "exe-sha256", "equals": CAND_SHA}],
  "provenance": "analysis/renderer-rea/candidate-0.103.0 (REPORT.md, REVIEW.md, manifest.json); changed interval [102530219,102545330) inside .rdata; PE layout and size unchanged.",
  "limits": ["Not GPU-validated for promotion; the renderer worker owns acceptance. No performance claim: provisional runs showed +4.8% mean frame time.",
             "Affects ADT liquid only (WMO exterior path returns earlier).",
             "The shader hunk list targets exactly the official v0.103.0 EXE; any other build fails closed."]
}
(HERE / "recipes" / "water-shader-adt-liquid.json").write_text(json.dumps(water, indent=2) + "\n", encoding="utf-8")
print("water edits:", len(edits), sum(e["length"] for e in edits), "bytes")

terrain = {
  "schema": "as-recipe/1", "id": "hd-terrain-community", "version": "0.0.1",
  "title": "HD terrain texture overlay (patch-1.MPQ)", "status": "unavailable",
  "description": "The installed 192,923,258-byte HD terrain overlay (694 BLP files).",
  "unavailable_reason": "Not reproducible from an owned WoW install: all 694 inputs are community textures from the VanillaHD Patch-A "
                        "(dcf3647e80104d03c02e1540c61cce3ca7e24766, 20 files) and Patch-B (a112a137f3183d53cdd4e88c94f9c81fbeaa294a, 674 files) "
                        "GitHub repositories, not CASC data. Their redistribution terms are unreviewed and the installer never downloads. "
                        "It could become a 'user-supplied' recipe (owner fetches the pinned commits, installer verifies each hash) once the "
                        "repository-path to MPQ-path mapping is written down; the pin list is mod-source/source-manifest.json.",
  "provenance": "mod-source/source-manifest.json; analysis/installed-overlays.json (patch-1.MPQ sha256 be6020322e2361d3666052ea6db86b15613c049093825128abc2799503398ab1)."
}
spells = {
  "schema": "as-recipe/1", "id": "hd-spells-community", "version": "0.0.1",
  "title": "HD spell visuals: Fireball, Frostbolt, Arcane Explosion, smoke atlas (12 files)", "status": "unavailable",
  "description": "The 12-file spell overlay that lives in patch-0.MPQ next to the mace.",
  "unavailable_reason": "Not reproducible from an owned WoW install: files come from community repositories (VanillaHD/Patch-A "
                        "dcf3647e80104d03c02e1540c61cce3ca7e24766 and yuritsuki/wow-spells 682896e191ac0a572090a3daa7714348b52180b6), "
                        "documented as 'pinned community backports; not latest-retail proof'. A Retail-derived replacement needs the "
                        "asset-coverage worker's spell recipes (M2 particle/ribbon conversion is not built yet).",
  "provenance": "mod-source/spell-hd-integrated/provenance.json (patch-0 spell package sha256 d63657b18e25363a5067f9dbdc1291aa0a2ec78f02f053a0f0cf955b575add25)."
}
for r in (terrain, spells):
    (HERE / "recipes" / (r["id"] + ".json")).write_text(json.dumps(r, indent=2) + "\n", encoding="utf-8")
