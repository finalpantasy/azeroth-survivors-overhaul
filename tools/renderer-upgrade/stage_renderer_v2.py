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

  python -I stage_renderer.py [--water] [--relocate] [--sharpen 0.35] [--fxaa 1] [--vibrance 0]
                              [--name renderer-post-v0.103.0] [--build-only]

Output: <project>/previews/<name>/ (patched EXE, copied config and saves, diagnostics/,
manifest.json, README.md, Play.cmd); data is read in place from the main install's Data/
through WOW_DATA (read-only), so the candidate differs from main only in the EXE.
"""

from __future__ import annotations

import argparse
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
        "validate": None,  # needs Bevy view-pipeline defs; runtime-proven by the v0.103 preview
    },
}


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
    text = original_lf.replace(anchor, helpers + anchor, 1)
    return text.replace(original_sg, "    let sg = post_scene(uv, scene.rgb);\n", 1)


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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--name", default="renderer-post-v0.103.0")
    ap.add_argument("--sharpen", type=float, default=0.5)
    ap.add_argument("--fxaa", type=float, default=0.0)
    ap.add_argument("--vibrance", type=float, default=0.0)
    ap.add_argument("--water", action="store_true", help="also apply the Codex water-shader preview")
    ap.add_argument("--relocate", action="store_true", help="force the new-section path for every shader")
    ap.add_argument("--build-only", action="store_true", help="write the EXE and report under analysis/ only")
    args = ap.parse_args()

    data = SOURCE_EXE.read_bytes()
    if len(data) != SOURCE_SIZE or sha256(data) != SOURCE_SHA256:
        raise RuntimeError("source executable is not the pinned official v0.103.0")
    work = ROOT / "analysis" / "renderer-upgrade" / "candidates" / args.name
    if work.exists():
        raise RuntimeError(f"candidate output exists: {work}")
    work.mkdir(parents=True)

    built, sources, checks = {}, {}, {}
    names = ["ffx_glow"] + (["liquid"] if args.water else [])
    for name in names:
        slot = SHADERS[name]
        original = data[slot["offset"]:slot["offset"] + slot["length"]]
        lf = original.replace(b"\r\n", b"\n")
        if sha256(lf) != slot["lf_sha256"]:
            raise RuntimeError(f"{name}: embedded original does not match its pinned source")
        text_lf = lf.decode("utf-8")
        if name == "ffx_glow":
            patched = build_ffx_glow(text_lf, args.sharpen, args.fxaa, args.vibrance)
        else:
            patched = build_liquid(text_lf)
        readable = work / f"{name}.patched.wgsl"
        readable.write_text(patched, encoding="utf-8", newline="\n")
        embedded = strip_comments(patched).replace("\n", "\r\n").encode("ascii")
        emb_path = work / f"{name}.embedded.wgsl"
        emb_path.write_bytes(embedded)
        if slot["validate"] is not None:
            checks[name] = [{"defs": d, "result": validate(emb_path, d)} for d in slot["validate"]]
        built[name] = embedded
        sources[name] = {"patched_lf_sha256": sha256(patched.encode()), "embedded_sha256": sha256(embedded)}

    out, changed, info = patch_exe(data, built, args.relocate)
    proof = verify(data, out, changed, built, info)
    exe_path = work / "AzerothSurvivors.exe"
    with open(exe_path, "xb") as f:
        f.write(out)
    report = {
        "schema": 1, "candidate": args.name, "built_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_exe": {"path": str(SOURCE_EXE), "sha256": SOURCE_SHA256, "bytes": SOURCE_SIZE},
        "output_exe": {"sha256": sha256(out), "bytes": len(out)},
        "settings": {"sharpen": args.sharpen, "fxaa": args.fxaa, "vibrance": args.vibrance,
                     "water": args.water, "relocate": args.relocate},
        "shaders": {k: {**info[k], **sources[k], "static_validation": checks.get(k, "not run")} for k in built},
        "changed_intervals": changed, "verification": proof,
        "limits": ["Shader-only change; no code, binding, pass or pipeline change.",
                   "Static validation = naga_oil 0.20 compose + naga 27 validate + SPIR-V and HLSL SM6 lowering.",
                   "Runtime acceptance, visual quality and frame cost are recorded separately."],
    }
    (work / "build-report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(f"built {exe_path} sha256 {report['output_exe']['sha256']}")
    for k in built:
        print(f"  {k}: {info[k]['method']} {info[k]['embedded_bytes']}/{info[k]['slot_bytes']} B; "
              f"validation {checks.get(k, 'not run')}")
    if args.build_only:
        return 0

    preview = PROJECT / "previews" / args.name
    if os.path.lexists(preview):
        raise RuntimeError(f"preview exists: {preview}")
    stage = Path(tempfile.mkdtemp(prefix=f".{args.name}-stage-", dir=PROJECT / "previews"))
    try:
        for sub in ("config", "saves", "diagnostics"):
            (stage / sub).mkdir()
        shutil.copy2(exe_path, stage / "AzerothSurvivors.exe")
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
            "v0.103.0 with only embedded shader text changed (see manifest.json). Config and saves here are copies; "
            "progress stays separate from normal play. Game data is read from the main install's Data folder.\n",
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
