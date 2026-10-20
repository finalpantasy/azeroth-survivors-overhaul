#!/usr/bin/env python3
"""Build and stage an isolated renderer-upgrade candidate of Azeroth Survivors v0.103.0.

Shader-only engine patch: the game embeds its WGSL as Bevy embedded assets. Each shader is one
`lea reg,[rip+disp32]` (the bytes) plus one `mov qword [rbp-0x28], imm32` (the length) in that
shader's own registration function (analysis/renderer-upgrade/PLAN.md, "Patch method"). So a
patched shader goes either
  - in place: comments stripped, CRLF, padded with spaces to the original slot length, or
  - relocated: appended in a new read-only PE section, with the lea disp32 and length imm32
    re-pointed (any length; header room for 6 more sections).
Nothing else in the executable changes. The source executable is read-only and hash-pinned.

  python -I stage_renderer.py --toggles all|none|t1,t2,... [--sharpen 0.5] [--vibrance 0.15] [--fxaa 0]
                              [--opt key=value ...] [--name renderer-v3-v0.103.0] [--relocate]
                              [--build-only] [--recipes]
Toggles: sharpen, grade (ffx_glow combine), water (Codex ADT liquid patch) and the v3 world
toggles in v3_patches.py (terrain_light, terrain_sheen, mcsh_soft, model_rim, fog, blob_soft).
Each patched shader is fitted into its own slot when it can be (comments stripped, then minified
without merging tokens), which keeps the change a same-length edit an as-recipe/1 recipe can carry;
only a shader that still does not fit is relocated. --recipes writes one recipe per in-place slot.

Output: <project>/previews/<name>/ (patched EXE, copied config and saves, diagnostics/,
manifest.json, README.md, Play.cmd); data is read in place from the main install's Data/
through WOW_DATA (read-only), so the candidate differs from main only in the EXE.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]                       # .../AzerothSurvivors
PROJECT = ROOT.parent                        # .../Azeroth Survivors
SOURCE_EXE = ROOT / "AzerothSurvivors.exe"
SOURCE_SHA256 = "6382e9ae654295293b82d48fb07a1aa999132775bda10c6c6f80d973d2c1ddfb"
SOURCE_SIZE = 139_713_536
IMAGE_BASE = 0x140000000
VALIDATOR_DIR = HERE / "wgsl-validate"
VALIDATOR_EXE = Path(os.environ.get("TEMP", "C:/Temp")) / "claude" / "wgslv-target" / "release" / "wgsl-validate.exe"
CARGO_REGISTRY = Path.home() / ".cargo" / "registry" / "src" / "index.crates.io-1949cf8c6b5b557f"
SECTION_NAME = b".wgslx\0\0"

# Mesh-pipeline defs that compose every original world shader (all PASS unpatched).
BASE_DEFS = ["AVAILABLE_STORAGE_BUFFER_BINDINGS=8", "MATERIAL_BIND_GROUP=3", "MESH_PIPELINE", "VERTEX_POSITIONS",
             "VERTEX_NORMALS", "VERTEX_UVS", "VERTEX_UVS_A", "VERTEX_UVS_B", "VERTEX_COLORS",
             "VERTEX_OUTPUT_INSTANCE_INDEX", "VIEW_PROJECTION_PERSPECTIVE", "MULTISAMPLED", "MAX_DIRECTIONAL_LIGHTS=10",
             "MAX_CASCADES_PER_LIGHT=4", "IRRADIANCE_VOLUMES_ARE_USABLE", "MULTIPLE_LIGHTMAPS_IN_ARRAY",
             "MULTIPLE_LIGHT_PROBES_IN_ARRAY", "SCREEN_SPACE_SPECULAR_TRANSMISSION_BLUR_TAPS=8",
             "SHADOW_FILTER_METHOD_GAUSSIAN"]

# Pinned v0.103.0 shader slots (analysis/renderer-upgrade/shader-inventory.json).
SHADERS = {
    "ffx_glow": {
        "offset": 100_972_965, "length": 7_494,
        "lf_sha256": "2a17c73ebbcef5fea8b28068735105851596747772781ba6571d7c07cca7e175",
        "lea_va": 0x14179DC22, "len_va": 0x14165FB93,
        "validate": [[], ["GAMMA_OUT"]],
    },
    "liquid": {
        "offset": 102_530_219, "length": 15_111,
        "lf_sha256": "4d873078aa7f2b143652955ee28c057e84638069b661e3c73ed2af7a3e651846",
        "lea_va": 0x1422D13CB, "len_va": 0x1422A0C6E,
        "validate": [BASE_DEFS],
    },
    "terrain": {
        "offset": 102_468_510, "length": 13_228,
        "lf_sha256": "e540be0de98347db43dfb13fae447c6890582eef002c5e153374d4b778044b27",
        "lea_va": 0x1422D0DFD, "len_va": 0x1422A088E,
        "validate": [BASE_DEFS],
    },
    "static_gx": {
        "offset": 100_991_790, "length": 18_270,
        "lf_sha256": "e384053e8588db831458e4b0a53db6f5abe6a4cd76a00f5db33291d5f15de950",
        "lea_va": 0x14179E061, "len_va": 0x141660C13,
        "validate": [BASE_DEFS],
    },
    "wow_model": {
        "offset": 102_481_790, "length": 44_357,
        "lf_sha256": "77216fd025fc62c9311e7117753c849d10c4cc2195eabdf7ace69f331546b8fe",
        "lea_va": 0x1422D0FCF, "len_va": 0x1422A104E,
        "validate": [BASE_DEFS, BASE_DEFS + ["WOW_RIG_SKIN", "MAY_DISCARD"]],
    },
    "wow_effect": {
        "offset": 100_980_511, "length": 11_233,
        "lf_sha256": "6f650bc8985071cfa9ec6b62b7f6ebbabc0cd603b46b3be594aaf15715c121df",
        "lea_va": 0x14179DE40, "len_va": 0x1416603D3,
        "validate": [BASE_DEFS, BASE_DEFS + ["BLEND_MULTIPLY", "DECAL_WORLD_CLIP"], BASE_DEFS + ["BLEND_ADD"],
                     BASE_DEFS + ["BLEND_ALPHAKEY", "EFFECT_LIT"], BASE_DEFS + ["BLEND_MOD"]],
    },
}
# Other official builds: the same shader bytes at new places. Per slot: (offset, lea_va, len_va), each
# located by content (slot bytes unique, one rip-relative lea, one length site) and proven by check_sites.
BASES = {
    "0.103.0": {"exe": SOURCE_EXE, "sha256": SOURCE_SHA256, "size": SOURCE_SIZE, "slots": {}},
    "0.103.1": {
        "exe": PROJECT / "previews" / "base-v0.103.1" / "AzerothSurvivors.exe",
        "sha256": "3b46bad92e7771afa61c93658fd850937834a40f3671af8b1a3e1d2aeb8b63c7", "size": 139_728_384,
        "slots": {
            "ffx_glow": (100_981_749, 0x14179BB92, 0x14165DB03),
            "liquid": (102_539_035, 0x1422D003B, 0x14229F8DE),
            "terrain": (102_477_326, 0x1422CFA6D, 0x14229F4FE),
            "static_gx": (101_000_574, 0x14179BFD1, 0x14165EB83),
            "wow_model": (102_490_606, 0x1422CFC3F, 0x14229FCBE),
            "wow_effect": (100_989_295, 0x14179BDB0, 0x14165E343),
        },
    },
}
BASE_VERSION = "0.103.0"


def select_base(version: str) -> None:
    """Point SOURCE_* and every slot at an official build; the shader bytes are the same, only places move."""
    global SOURCE_EXE, SOURCE_SHA256, SOURCE_SIZE, BASE_VERSION
    b = BASES[version]
    SOURCE_EXE, SOURCE_SHA256, SOURCE_SIZE, BASE_VERSION = b["exe"], b["sha256"], b["size"], version
    for name, (off, lea, ln) in b["slots"].items():
        SHADERS[name].update(offset=off, lea_va=lea, len_va=ln)


SHADER_ORDER = ["ffx_glow", "terrain", "wow_model", "static_gx", "liquid", "wow_effect"]
RECIPE_IDS = {"ffx_glow": "renderer-v3-post", "terrain": "renderer-v3-terrain", "wow_model": "renderer-v3-model",
              "static_gx": "renderer-v3-static", "liquid": "renderer-v3-liquid", "wow_effect": "renderer-v3-effect"}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def pe(data: bytes) -> dict:
    p = struct.unpack_from("<I", data, 0x3C)[0]
    nsec = struct.unpack_from("<H", data, p + 6)[0]
    opt_size = struct.unpack_from("<H", data, p + 20)[0]
    opt = p + 24
    table = opt + opt_size
    secs = []
    for i in range(nsec):
        o = table + 40 * i
        vsize, va, rsize, raw = struct.unpack_from("<IIII", data, o + 8)
        secs.append({"name": data[o:o + 8].rstrip(b"\0").decode(), "vsize": vsize, "va": va,
                     "rsize": rsize, "raw": raw, "hdr": o})
    return {"p": p, "opt": opt, "nsec": nsec, "table": table, "secs": secs,
            "size_of_image": struct.unpack_from("<I", data, opt + 56)[0],
            "size_of_headers": struct.unpack_from("<I", data, opt + 60)[0],
            "file_align": struct.unpack_from("<I", data, opt + 36)[0],
            "sect_align": struct.unpack_from("<I", data, opt + 32)[0],
            "security_dir": struct.unpack_from("<II", data, opt + 112 + 8 * 4)}


def va_to_off(layout: dict, va: int) -> int:
    rva = va - IMAGE_BASE
    for s in layout["secs"]:
        if s["va"] <= rva < s["va"] + max(s["vsize"], s["rsize"]):
            return s["raw"] + rva - s["va"]
    raise ValueError(f"VA {va:#x} outside every section")


def off_to_rva(layout: dict, off: int) -> int:
    for s in layout["secs"]:
        if s["raw"] <= off < s["raw"] + s["rsize"]:
            return s["va"] + off - s["raw"]
    raise ValueError(f"offset {off} outside every section")


def check_sites(data: bytes, layout: dict, name: str, slot: dict) -> dict:
    """Prove the lea points at the slot and the length site holds its length, uniquely."""
    lea_off = va_to_off(layout, slot["lea_va"])
    if data[lea_off] not in (0x48, 0x4C) or data[lea_off + 1] != 0x8D or (data[lea_off + 2] & 0xC7) != 0x05:
        raise RuntimeError(f"{name}: no rip-relative lea at {slot['lea_va']:#x}")
    disp = struct.unpack_from("<i", data, lea_off + 3)[0]
    target = slot["lea_va"] - IMAGE_BASE + 7 + disp
    if target != off_to_rva(layout, slot["offset"]):
        raise RuntimeError(f"{name}: lea target {target:#x} is not the shader slot")
    len_off = va_to_off(layout, slot["len_va"])
    want = b"\x48\xC7\x45\xD8" + struct.pack("<I", slot["length"])
    if data[len_off:len_off + 8] != want:
        raise RuntimeError(f"{name}: length site {slot['len_va']:#x} is not `mov [rbp-0x28], {slot['length']}`")
    # The slot must have no other rip-relative lea and no absolute pointer to it.
    text = layout["secs"][0]
    hits = 0
    for m in re.finditer(rb"[\x48\x4c]\x8d[\x05\x0d\x15\x1d\x25\x2d\x35\x3d]", data[text["raw"]:text["raw"] + text["rsize"]]):
        o = text["raw"] + m.start()
        d = struct.unpack_from("<i", data, o + 3)[0]
        if text["va"] + m.start() + 7 + d == target:
            hits += 1
    ptr = struct.pack("<Q", IMAGE_BASE + target)
    abs_refs = data.count(ptr)
    if hits != 1 or abs_refs:
        raise RuntimeError(f"{name}: expected exactly one lea and no absolute refs, got {hits}/{abs_refs}")
    return {"lea_file_offset": lea_off, "len_file_offset": len_off + 4, "slot_rva": target}


def strip_comments(text: str) -> str:
    out, i, n = [], 0, len(text)
    while i < n:
        if text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
            continue
        if text.startswith("/*", i):
            depth, i = 1, i + 2
            out.append(" ")
            while i < n and depth:
                if text.startswith("/*", i):
                    depth, i = depth + 1, i + 2
                elif text.startswith("*/", i):
                    depth, i = depth - 1, i + 2
                else:
                    if text[i] == "\n":
                        out.append("\n")
                    i += 1
            continue
        out.append(text[i])
        i += 1
    lines = [l.rstrip() for l in "".join(out).split("\n")]
    return "\n".join(l for l in lines if l) + "\n"


def build_ffx_glow(original_lf: str, sharpen: float, fxaa: float, vibrance: float) -> str:
    helpers = (HERE / "patches" / "ffx_post.wgsl").read_text(encoding="utf-8").replace("\r\n", "\n")
    for const, value in (("POST_SHARPEN", sharpen), ("POST_FXAA", fxaa), ("POST_VIBRANCE", vibrance)):
        pat = rf"const {const}: f32 = [0-9.]+;"
        if len(re.findall(pat, helpers)) != 1:
            raise RuntimeError(f"helper constant {const} missing")
        helpers = re.sub(pat, f"const {const}: f32 = {value:.4f};", helpers)
    anchor = "// Both combines, at the plain or the warped UV;"
    original_sg = "    let sg = clamp(scene.rgb, vec3<f32>(0.0), vec3<f32>(1.0));\n"
    if original_lf.count(anchor) != 1 or original_lf.count(original_sg) != 1:
        raise RuntimeError("ffx_glow.wgsl anchors not unique; refusing to patch")
    if fxaa == 0.0:
        # FXAA off: drop its code so the combine fits its original slot (same output, the branch was dead).
        start = helpers.index("fn post_fxaa(")
        end = helpers.index("\n}\n", start) + 3
        helpers = helpers[:start] + helpers[end:]
        branch = ("    if POST_FXAA > 0.0 && range >= max(POST_FXAA_EDGE_MIN, lmax * POST_FXAA_EDGE_REL) {\n"
                  "        res = post_fxaa(uv, texel, lc, ln, ls, le, lw, range);\n"
                  "    } else if POST_SHARPEN > 0.0 {\n")
        if helpers.count(branch) != 1:
            raise RuntimeError("ffx_post.wgsl FXAA branch anchor missing")
        helpers = helpers.replace(branch, "    if POST_SHARPEN > 0.0 {\n", 1)
    text = original_lf.replace(anchor, helpers + anchor, 1)
    return text.replace(original_sg, "    let sg = post_scene(uv, scene.rgb);\n", 1)


# Two-character tokens that removing a space could create (WGSL has no string literals).
_MERGES = {"--", "++", "==", "!=", "<=", ">=", "<<", ">>", "&&", "||", "->", "+=", "-=", "*=", "/=", "%=",
           "&=", "|=", "^=", "//", "/*", "*/", "::"}
_PUNCT = set("(){}[],;:=+-*/<>&|!%^~.@")


def minify(text: str) -> str:
    """Comments stripped, indentation dropped, and spaces removed wherever a punctuation character is on
    either side and no new token forms. Lines are kept (naga_oil directives are line based) and directive
    lines are left as they are, so the token stream the compiler sees is unchanged."""
    out = []
    for line in strip_comments(text).split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            out.append(line)
            continue
        res, i = [], 0
        while i < len(line):
            ch = line[i]
            if ch == " ":
                j = i
                while j < len(line) and line[j] == " ":
                    j += 1
                a = res[-1] if res else ""
                b = line[j] if j < len(line) else ""
                if (a in _PUNCT or b in _PUNCT) and (a + b) not in _MERGES:
                    i = j
                    continue
                res.append(" ")
                i = j
                continue
            res.append(ch)
            i += 1
        out.append("".join(res))
    return "\n".join(l for l in out if l) + "\n"


def fit(patched_lf: str, slot_len: int) -> tuple[bytes, str]:
    """The smallest-change embedding that fits the slot, else the minified text for relocation."""
    stripped = strip_comments(patched_lf)
    small = minify(patched_lf)
    for level, blob in (("strip-crlf", stripped.replace("\n", "\r\n")), ("strip-lf", stripped),
                        ("minify-crlf", small.replace("\n", "\r\n")), ("minify-lf", small)):
        if len(blob) <= slot_len:
            return blob.encode("ascii"), level
    return small.encode("ascii"), "minify-lf"


def build_liquid(original_lf: str) -> str:
    sys.path.insert(0, str(ROOT / "tools" / "native-shader-preview"))
    import patch_liquid_shader  # the Codex water preview transform, unchanged
    return patch_liquid_shader.build_preview_source(original_lf)


def validate(path: Path, defs: list[str]) -> str:
    if not VALIDATOR_EXE.is_file():
        env = dict(os.environ, CARGO_TARGET_DIR=str(VALIDATOR_EXE.parents[1]))
        subprocess.run(["cargo", "build", "--release", "--offline"], cwd=VALIDATOR_DIR, env=env, check=True)
    libs = []
    for crate in sorted(CARGO_REGISTRY.glob("bevy_*-0.18.1")):
        libs += ["--lib", str(crate / "src")]
    cmd = [str(VALIDATOR_EXE), str(path)] + libs
    for d in defs:
        cmd += ["--def", d]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    line = (r.stdout + r.stderr).strip()
    if r.returncode != 0 or not line.startswith("PASS"):
        raise RuntimeError(f"static validation failed for {path.name} {defs}:\n{line}")
    return line.split(" entries=")[1] if " entries=" in line else line


def patch_exe(data: bytes, built: dict, relocate: bool) -> tuple[bytes, list, dict]:
    """built: name -> embedded bytes (CRLF). Returns (new bytes, changed intervals, per-shader info)."""
    layout = pe(data)
    out = bytearray(data)
    info, changed, payload = {}, [], bytearray()
    placements = []
    for name, blob in built.items():
        slot = SHADERS[name]
        sites = check_sites(data, layout, name, slot)
        if not relocate and len(blob) <= slot["length"]:
            padded = blob + b" " * (slot["length"] - len(blob))
            out[slot["offset"]:slot["offset"] + slot["length"]] = padded
            changed.append([slot["offset"], slot["offset"] + slot["length"]])
            info[name] = {"method": "in_place", "embedded_bytes": len(blob), "slot_bytes": slot["length"],
                          "readback": [slot["offset"], slot["length"]], **sites}
        else:
            while len(payload) % 16:
                payload += b"\0"
            placements.append((name, len(payload), blob, sites))
            payload += blob
            info[name] = {"method": "relocated", "embedded_bytes": len(blob), "slot_bytes": slot["length"], **sites}
    if placements:
        if layout["security_dir"] != (0, 0):
            raise RuntimeError("executable is signed; refusing to add a section")
        last = layout["secs"][-1]
        new_hdr = layout["table"] + 40 * layout["nsec"]
        if new_hdr + 40 > layout["size_of_headers"] or any(out[new_hdr:new_hdr + 40]):
            raise RuntimeError("no free section-header slot")
        if len(data) != last["raw"] + last["rsize"]:
            raise RuntimeError("executable has an overlay; refusing to append a section")
        fa, sa = layout["file_align"], layout["sect_align"]
        va = -(-(last["va"] + last["vsize"]) // sa) * sa
        raw = len(data)
        rsize = -(-len(payload) // fa) * fa
        hdr = SECTION_NAME + struct.pack("<IIIIIIHHI", len(payload), va, rsize, raw, 0, 0, 0, 0, 0x40000040)
        out[new_hdr:new_hdr + 40] = hdr
        struct.pack_into("<H", out, layout["p"] + 6, layout["nsec"] + 1)
        struct.pack_into("<I", out, layout["opt"] + 56, -(-(va + len(payload)) // sa) * sa)
        init = struct.unpack_from("<I", out, layout["opt"] + 8)[0]
        struct.pack_into("<I", out, layout["opt"] + 8, init + rsize)
        changed += [[layout["p"] + 6, layout["p"] + 8], [layout["opt"] + 8, layout["opt"] + 12],
                    [layout["opt"] + 56, layout["opt"] + 60], [new_hdr, new_hdr + 40]]
        out += payload + b"\0" * (rsize - len(payload))
        changed.append([raw, raw + rsize])
        for name, at, blob, sites in placements:
            new_rva = va + at
            lea_rva = SHADERS[name]["lea_va"] - IMAGE_BASE
            struct.pack_into("<i", out, sites["lea_file_offset"] + 3, new_rva - (lea_rva + 7))
            struct.pack_into("<I", out, sites["len_file_offset"], len(blob))
            changed += [[sites["lea_file_offset"] + 3, sites["lea_file_offset"] + 7],
                        [sites["len_file_offset"], sites["len_file_offset"] + 4]]
            info[name].update({"new_rva": new_rva, "readback": [raw + at, len(blob)]})
    return bytes(out), sorted(changed), info


def verify(before: bytes, after: bytes, changed: list, built: dict, info: dict) -> dict:
    n = min(len(before), len(after))
    # Every byte outside the declared intervals must be identical: compare the gaps exactly.
    pos = 0
    for a, b in changed + [[n, n]]:
        if before[pos:min(a, n)] != after[pos:min(a, n)]:
            raise RuntimeError(f"bytes changed outside the declared intervals in [{pos},{a})")
        pos = max(pos, min(b, n))
    for name, blob in built.items():
        o, ln = info[name]["readback"]
        got = after[o:o + ln]
        if not got.startswith(blob) or got[len(blob):].strip(b" ") != b"":
            raise RuntimeError(f"{name}: readback mismatch")
    layout = pe(after)
    for name in built:
        if info[name]["method"] == "relocated":
            check = dict(SHADERS[name], offset=info[name]["readback"][0], length=len(built[name]))
            check_sites(after, layout, name, check)
    return {"bytes_outside_intervals_identical": True, "appended_bytes": len(after) - len(before),
            "sections": [s["name"] for s in layout["secs"]]}


def copy_saves(dest: Path) -> list:
    appdata = os.environ.get("APPDATA")
    src = Path(appdata) / "wenilla-rogue" if appdata else None
    if not (src and (src / "characters.txt").is_file()):
        # Fallback: the v0.103.0 graphics preview's own copies of the roster.
        src = PROJECT / "previews" / "graphics-v0.103.0" / "saves"
    copied = []
    if src.is_dir():
        for f in sorted(src.glob("*.txt")):
            shutil.copy2(f, dest / f.name)
            copied.append({"name": f.name, "bytes": f.stat().st_size, "from": str(src)})
    return copied


PLAY_CMD = r"""@echo off
setlocal
rem Isolated renderer-upgrade candidate: own config, saves and diagnostics; reads the main
rem install's Data\ read-only. Normal saves and the main game are not touched.
set "PREVIEW_ROOT=%~dp0"
set "PREVIEW_ROOT=%PREVIEW_ROOT:~0,-1%"
for %%V in (WOW_NOSOUND WOW_CAPTURE WOW_CAPTURE_UI WOW_CAM_DUMP WOW_ROGUE_ARMORY WOW_ROGUE_GLUE WOW_ROGUE_TAPS WOW_ROGUE_KEY WOW_ROGUE_AUTOPLAY WOW_ROGUE_AUTOSTART WOW_ROGUE_AUTOPICK WOW_ROGUE_SHOTS WOW_ROGUE_SHOWCASE WOW_ROGUE_DEBUG_LOOT WOW_ROGUE_UPDATE_URL WOW_ROGUE_SINK WOW_ROGUE_SINK_AT) do set "%%V="
set "WOW_DATA=%PREVIEW_ROOT%\..\..\AzerothSurvivors\Data"
set "BENILLA_HOME=%PREVIEW_ROOT%\config"
set "WOW_ROGUE_SAVE=%PREVIEW_ROOT%\saves\characters.txt"
set "WOW_ROGUE_UPDATE_AUTO=0"
set "WOW_FPS_JOURNAL=%PREVIEW_ROOT%\diagnostics\frame-times-%RANDOM%-%RANDOM%.csv"
cd /d "%PREVIEW_ROOT%"
start "" "AzerothSurvivors.exe"
endlocal
"""


sys.path.insert(0, str(HERE))  # python -I drops the script folder
import v3_patches  # noqa: E402  (the v3 world-shader toggles)

ALL_TOGGLES = ["sharpen", "grade", "water", "terrain_light", "terrain_sheen", "mcsh_soft", "model_rim", "fog",
               "blob_soft"]


def parse_toggles(spec: str) -> list[str]:
    if spec == "all":
        return list(ALL_TOGGLES)
    if spec in ("", "none"):
        return []
    got = [t.strip() for t in spec.split(",") if t.strip()]
    bad = [t for t in got if t not in ALL_TOGGLES]
    if bad:
        raise ValueError(f"unknown toggle(s) {bad}; known: {ALL_TOGGLES}")
    return [t for t in ALL_TOGGLES if t in got]


def build_shader(name: str, text_lf: str, toggles: set, args, opts: dict) -> str:
    if name == "ffx_glow":
        return build_ffx_glow(text_lf, args.sharpen if "sharpen" in toggles else 0.0, args.fxaa,
                              args.vibrance if "grade" in toggles else 0.0)
    if name == "liquid" and "water" in toggles:
        text_lf = build_liquid(text_lf)
    return v3_patches.PATCHERS[name](text_lf, toggles, opts)


def shader_toggles(name: str, toggles: list[str]) -> list[str]:
    return [t for t in toggles if name in v3_patches.TOGGLE_SHADERS.get(t, [])]


def write_recipe(path: Path, name: str, slot: dict, original: bytes, new: bytes, applied: list[str],
                 settings: dict) -> dict:
    """One as-recipe/1 recipe per in-place slot: a single same-length edit, composable with the others."""
    if len(new) != slot["length"]:
        raise RuntimeError(f"{name}: recipe edit must be same-length")
    data = bytearray(original)
    data[slot["offset"]:slot["offset"] + slot["length"]] = new
    result = sha256(bytes(data))
    rid = RECIPE_IDS[name]
    recipe = {
        "schema": "as-recipe/1", "id": rid, "version": "0.3.0",
        "title": f"Renderer v3: {name} shader ({', '.join(applied)})",
        "description": (f"Replaces the embedded {name} WGSL inside its own slot (same length, PE layout unchanged) "
                        f"with the renderer-upgrade v3 version: {', '.join(applied)}. Composes with the other "
                        "renderer-v3-* recipes (disjoint slots). No executable is distributed."),
        "status": "experimental", "default": False,
        "requires": {"game": {"exe_sha256": [SOURCE_SHA256], "versions": [BASE_VERSION]}, "disk_mb": 400},
        "outputs": {"exe_patches": [{
            "target": "AzerothSurvivors.exe", "expect_exe_sha256": SOURCE_SHA256, "result_exe_sha256": result,
            "edits": [{"offset": slot["offset"], "length": slot["length"],
                       "expect_sha256": sha256(original[slot["offset"]:slot["offset"] + slot["length"]]),
                       "new_b64": base64.b64encode(new).decode("ascii")}]}]},
        "acceptance": [{"check": "exe-sha256", "equals": result}],
        "provenance": {"stage_script": "tools/renderer-upgrade/stage_renderer.py",
                       "patches": "tools/renderer-upgrade/v3_patches.py, patches/ffx_post.wgsl",
                       "settings": settings},
        "limits": ["Renderer worker acceptance (runtime A/B and exclusive-mode cost) is recorded in "
                   "analysis/renderer-upgrade/PLAN.md; promotion is the coordinator's call."],
    }
    if name == "liquid" and "water" in applied:
        recipe["limits"].append("Includes the water-shader-adt-liquid transform; it edits the same slot, so select "
                                "one of the two, not both.")
    if name == "ffx_glow":
        recipe["limits"].append("Supersedes renderer-sharpen-ffx (relocated v2, same sharpen math) in place.")
    path.write_text(json.dumps(recipe, indent=1), encoding="utf-8")
    return {"id": rid, "result_exe_sha256": result}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--name", default=None, help="default renderer-v3-v<base>")
    ap.add_argument("--base", default="0.103.0", choices=sorted(BASES), help="official build to patch")
    ap.add_argument("--toggles", default="all", help="all, none, or a comma list of " + ",".join(ALL_TOGGLES))
    ap.add_argument("--sharpen", type=float, default=0.5)
    ap.add_argument("--fxaa", type=float, default=0.0)
    ap.add_argument("--vibrance", type=float, default=0.15, help="used when the grade toggle is on")
    ap.add_argument("--opt", action="append", default=[], help="v3 option key=value, see v3_patches.DEFAULTS")
    ap.add_argument("--relocate", action="store_true", help="force the new-section path for every shader")
    ap.add_argument("--build-only", action="store_true", help="write the EXE and report under analysis/ only")
    ap.add_argument("--recipes", action="store_true", help="write as-recipe/1 recipes for in-place slots")
    args = ap.parse_args()
    toggles = parse_toggles(args.toggles)
    opts = dict(v3_patches.DEFAULTS)
    for kv in args.opt:
        k, _, v = kv.partition("=")
        if k not in opts:
            raise ValueError(f"unknown option {k}; known: {sorted(opts)}")
        opts[k] = float(v)

    select_base(args.base)
    args.name = args.name or f"renderer-v3-v{args.base}"
    data = SOURCE_EXE.read_bytes()
    if len(data) != SOURCE_SIZE or sha256(data) != SOURCE_SHA256:
        raise RuntimeError(f"source executable is not the pinned official v{BASE_VERSION}")
    work = ROOT / "analysis" / "renderer-upgrade" / "candidates" / args.name
    if work.exists():
        raise RuntimeError(f"candidate output exists: {work}")
    work.mkdir(parents=True)

    built, sources, checks = {}, {}, {}
    names = [n for n in SHADER_ORDER if shader_toggles(n, toggles)]
    if not names:
        raise ValueError("no shader is changed by these toggles")
    for name in names:
        slot = SHADERS[name]
        original = data[slot["offset"]:slot["offset"] + slot["length"]]
        lf = original.replace(b"\r\n", b"\n")
        if sha256(lf) != slot["lf_sha256"]:
            raise RuntimeError(f"{name}: embedded original does not match its pinned source")
        text_lf = lf.decode("utf-8")
        patched = build_shader(name, text_lf, set(toggles), args, opts)
        (work / f"{name}.patched.wgsl").write_text(patched, encoding="utf-8", newline="\n")
        embedded, level = fit(patched, slot["length"])
        emb_path = work / f"{name}.embedded.wgsl"
        emb_path.write_bytes(embedded)
        orig_path = work / f"{name}.original.wgsl"
        orig_path.write_bytes(strip_comments(text_lf).encode("utf-8"))
        checks[name] = []
        for d in slot["validate"]:
            try:
                validate(orig_path, d)
            except RuntimeError as e:
                raise RuntimeError(f"{name}: the ORIGINAL fails this def set, so the set is wrong: {e}")
            extra = d[len(BASE_DEFS):] if d[:len(BASE_DEFS)] == BASE_DEFS else d
            checks[name].append({"defs": ("BASE+" if d[:len(BASE_DEFS)] == BASE_DEFS else "") + ",".join(extra),
                                 "result": validate(emb_path, d)})
        orig_path.unlink()
        built[name] = embedded
        sources[name] = {"toggles": shader_toggles(name, toggles), "fit": level,
                         "patched_lf_sha256": sha256(patched.encode()), "embedded_sha256": sha256(embedded)}

    out, changed, info = patch_exe(data, built, args.relocate)
    proof = verify(data, out, changed, built, info)
    settings = {"toggles": toggles, "sharpen": args.sharpen, "fxaa": args.fxaa, "vibrance": args.vibrance,
                "options": opts, "relocate": args.relocate}
    recipes = {}
    if args.recipes:
        rdir = ROOT / "analysis" / "renderer-upgrade" / "recipes" / ("" if BASE_VERSION == "0.103.0" else f"v{BASE_VERSION}")
        rdir.mkdir(exist_ok=True)
        for name in built:
            if info[name]["method"] != "in_place":
                recipes[name] = "not written: relocated (as-recipe/1 needs same-length edits)"
                continue
            slot = SHADERS[name]
            new = out[slot["offset"]:slot["offset"] + slot["length"]]
            recipes[name] = write_recipe(rdir / f"{RECIPE_IDS[name]}.json", name, slot, data, new,
                                         sources[name]["toggles"], settings)
    report = {
        "schema": 2, "candidate": args.name, "built_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_exe": {"path": str(SOURCE_EXE), "version": BASE_VERSION, "sha256": SOURCE_SHA256, "bytes": SOURCE_SIZE},
        "output_exe": {"sha256": sha256(out), "bytes": len(out)},
        "settings": settings,
        "shaders": {k: {**info[k], **sources[k], "static_validation": checks.get(k, "not run")} for k in built},
        "recipes": recipes, "changed_intervals": changed, "verification": proof,
        "limits": ["Shader-only change; no code, binding, pass or pipeline change.",
                   "Static validation = naga_oil 0.20 compose + naga 27 validate + SPIR-V and HLSL SM6 lowering, "
                   "per def variant; the original shader passes each variant too.",
                   "Runtime acceptance, visual quality and frame cost are recorded separately."],
    }
    (work / "build-report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(f"built {args.name} sha256 {report['output_exe']['sha256']} toggles {toggles}")
    for k in built:
        print(f"  {k}: {info[k]['method']} ({sources[k]['fit']}) {info[k]['embedded_bytes']}/{info[k]['slot_bytes']} B; "
              f"{len(checks[k])} def variants PASS; toggles {sources[k]['toggles']}")
    if args.build_only:
        with open(work / "AzerothSurvivors.exe", "xb") as f:
            f.write(out)
        return 0

    preview = PROJECT / "previews" / args.name
    if os.path.lexists(preview):
        raise RuntimeError(f"preview exists: {preview}")
    stage = Path(tempfile.mkdtemp(prefix=f".{args.name}-stage-", dir=PROJECT / "previews"))
    try:
        for sub in ("config", "saves", "diagnostics"):
            (stage / sub).mkdir()
        with open(stage / "AzerothSurvivors.exe", "xb") as f:
            f.write(out)
        if sha256((stage / "AzerothSurvivors.exe").read_bytes()) != report["output_exe"]["sha256"]:
            raise RuntimeError("staged EXE hash mismatch")
        shutil.copy2(ROOT / "benilla-config" / "config.toml", stage / "config" / "config.toml")
        saves = copy_saves(stage / "saves")
        (stage / "Play.cmd").write_text(PLAY_CMD.replace("\n", "\r\n"), encoding="ascii", newline="")
        manifest = dict(report, staged_at_utc=datetime.now(timezone.utc).isoformat(),
                        data="main install Data/ via WOW_DATA (read-only)",
                        config="copy of AzerothSurvivors/benilla-config/config.toml, unchanged",
                        saves=saves)
        (stage / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
        (stage / "README.md").write_text(
            f"# Renderer candidate {args.name}\n\nOptional, isolated. Launch with `Play.cmd`. The EXE is the official "
            f"v{BASE_VERSION} with only embedded shader text changed (toggles: {', '.join(toggles)}; see manifest.json). "
            "Config and saves here are copies; progress stays separate from normal play. Game data is read from "
            "the main install's Data folder.\n",
            encoding="utf-8")
        os.rename(stage, preview)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    print(f"staged {preview}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, RuntimeError, UnicodeError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
