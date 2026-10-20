"""Pinned, bounded project-local CascLib configure/build runner."""

from __future__ import annotations

import ctypes
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid


ROOT = Path(__file__).resolve().parent
TOOL_ROOT = ROOT
SOURCE_ROOT = TOOL_ROOT / "source"
BUILD_ROOT = TOOL_ROOT / "build-x64"
RUNTIME_ROOT = TOOL_ROOT / "build-logs"
MANIFEST_PATH = TOOL_ROOT / "source-manifest.json"


def find_cmake() -> Path:
    """CMake from $CMAKE, Visual Studio 2022 (any edition), or PATH."""
    import shutil
    candidates = []
    if os.environ.get("CMAKE"):
        candidates.append(Path(os.environ["CMAKE"]))
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")):
        if base:
            for edition in ("BuildTools", "Community", "Professional", "Enterprise"):
                candidates.append(Path(base) / "Microsoft Visual Studio" / "2022" / edition / "Common7" / "IDE"
                                  / "CommonExtensions" / "Microsoft" / "CMake" / "CMake" / "bin" / "cmake.exe")
    found = shutil.which("cmake")
    if found:
        candidates.append(Path(found))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return Path("cmake.exe")


CMK = find_cmake()
EXPECTED_COMMIT = "4971d363e665551ac4142f541e5f2d71f1cda653"
EXPECTED_ARCHIVE_SHA256 = "527920b8a48febb7da8fa72beee187cbd0a65cd377400b3160298d37fed34413"
EXPECTED_CACHE = (
    "CASC_BUILD_SHARED_LIB:BOOL=ON",
    "CASC_BUILD_STATIC_LIB:BOOL=OFF",
    "CASC_BUILD_TESTS:BOOL=OFF",
    "CASC_UNICODE:BOOL=ON",
    "CMAKE_DISABLE_FIND_PACKAGE_ZLIB:UNINITIALIZED=ON",
    "CMAKE_GENERATOR:INTERNAL=Visual Studio 17 2022",
    "CMAKE_GENERATOR_PLATFORM:INTERNAL=x64",
)
CREATE_NO_WINDOW = 0x08000000
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
PROCESS_TIMEOUT_SECONDS = 600


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_below_normal_parent() -> None:
    if os.name != "nt":
        raise RuntimeError("The CascLib build runner requires Windows")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    kernel.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel.SetPriorityClass.restype = ctypes.c_int
    if not kernel.SetPriorityClass(kernel.GetCurrentProcess(), BELOW_NORMAL_PRIORITY_CLASS):
        raise ctypes.WinError(ctypes.get_last_error(), "Could not set the build runner to BelowNormal")


def verify_inputs() -> dict:
    if not MANIFEST_PATH.is_file() or not CMK.is_file():
        raise RuntimeError("Pinned source manifest or CMake (set CMAKE, install VS 2022 Build Tools with the CMake component, or put cmake on PATH) is missing")
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if manifest.get("commit") != EXPECTED_COMMIT or manifest.get("archive_sha256") != EXPECTED_ARCHIVE_SHA256:
        raise RuntimeError("Unexpected CascLib source pin in the manifest")
    expected = {}
    for item in manifest.get("source_files", []):
        relative = item["path"]
        if relative in expected:
            raise RuntimeError(f"Duplicate source manifest entry: {relative}")
        path = SOURCE_ROOT / Path(relative)
        if not path.is_file() or sha256(path) != item["sha256"]:
            raise RuntimeError(f"CascLib source hash mismatch: {relative}")
        expected[relative.replace("\\", "/")] = item["sha256"]
    actual = {path.relative_to(SOURCE_ROOT).as_posix() for path in SOURCE_ROOT.rglob("*") if path.is_file()}
    if actual != set(expected):
        raise RuntimeError(f"CascLib source file set mismatch: missing={sorted(set(expected)-actual)[:5]} extra={sorted(actual-set(expected))[:5]}")
    return manifest


def validate_cache() -> None:
    cache_path = BUILD_ROOT / "CMakeCache.txt"
    if not BUILD_ROOT.exists():
        return
    if not cache_path.is_file():
        files = [path for path in BUILD_ROOT.rglob("*") if path.is_file()]
        directories = {path.relative_to(BUILD_ROOT).as_posix() for path in BUILD_ROOT.rglob("*") if path.is_dir()}
        if files or not directories.issubset({"CMakeFiles", "CMakeFiles/pkgRedirects"}):
            raise RuntimeError(f"Preserving unrecognized incomplete build directory: {BUILD_ROOT}")
        return
    cache = cache_path.read_text(encoding="utf-8", errors="replace")
    for entry in EXPECTED_CACHE:
        if entry not in cache:
            raise RuntimeError(f"Existing CMake cache has unexpected setting: {entry}")


def run_process(command: list[str], stdout_path: Path, stderr_path: Path) -> int:
    flags = CREATE_NO_WINDOW | BELOW_NORMAL_PRIORITY_CLASS
    with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            creationflags=flags,
        )
        try:
            return_code = process.wait(timeout=PROCESS_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            taskkill = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "taskkill.exe"
            subprocess.run(
                [str(taskkill), "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
                creationflags=CREATE_NO_WINDOW,
                check=False,
            )
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            raise RuntimeError(f"Process exceeded {PROCESS_TIMEOUT_SECONDS} seconds; stopped owned process tree {process.pid}")
        return int(return_code)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def write_record(path: Path, record: dict) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(record, indent=2) + "\n")


def main() -> int:
    if os.name != "nt":
        print("error: Windows is required", file=sys.stderr)
        return 1
    configure_only = "--configure-only" in sys.argv[1:]
    build_only = "--build-only" in sys.argv[1:]
    if configure_only and build_only:
        print("error: --configure-only and --build-only are mutually exclusive", file=sys.stderr)
        return 2
    if set(sys.argv[1:]) - {"--configure-only", "--build-only"}:
        print("usage: build_runner.py [--configure-only | --build-only]", file=sys.stderr)
        return 2
    try:
        require_below_normal_parent()
        RUNTIME_ROOT.mkdir(exist_ok=True)
        RUNTIME_ROOT.mkdir(exist_ok=True)
        manifest = verify_inputs()
        validate_cache()
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:12]
    phases = ("build",) if build_only else ("configure", "build")
    logs = {
        (name, stream): RUNTIME_ROOT / f"cmake-{name}-{run_id}.{stream}.log"
        for name in phases for stream in ("stdout", "stderr")
    }
    record_path = RUNTIME_ROOT / f"build-record-{run_id}.json"
    record = {
        "schema_version": 1,
        "run_id": run_id,
        "started_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source_commit": manifest["commit"],
        "archive_sha256": manifest["archive_sha256"],
        "source_manifest_sha256": sha256(MANIFEST_PATH),
        "cmake": str(CMK),
        "generator": "Visual Studio 17 2022",
        "architecture": "x64",
        "options": {
            "CASC_BUILD_SHARED_LIB": "ON",
            "CASC_BUILD_STATIC_LIB": "OFF",
            "CASC_BUILD_TESTS": "OFF",
            "CASC_UNICODE": "ON",
            "CMAKE_DISABLE_FIND_PACKAGE_ZLIB": "ON",
        },
        "priority_class": "BelowNormal",
        "parallel": 1,
        "timeout_seconds": PROCESS_TIMEOUT_SECONDS,
        "logs": {f"{key[0]}_{key[1]}": str(value) for key, value in logs.items()},
    }
    try:
        if not build_only:
            configure = [
                str(CMK), "-S", str(SOURCE_ROOT), "-B", str(BUILD_ROOT),
                "-G", "Visual Studio 17 2022", "-A", "x64",
                "-DCASC_BUILD_SHARED_LIB=ON", "-DCASC_BUILD_STATIC_LIB=OFF",
                "-DCASC_BUILD_TESTS=OFF", "-DCASC_UNICODE=ON",
                "-DCMAKE_DISABLE_FIND_PACKAGE_ZLIB=ON",
            ]
            configure_code = run_process(configure, logs["configure", "stdout"], logs["configure", "stderr"])
            record["configure_exit_code"] = configure_code
            configure_text = read_text(logs["configure", "stdout"]) + read_text(logs["configure", "stderr"])
            if configure_code != 0:
                raise RuntimeError(f"CMake configure returned {configure_code}")
            if "Configuring done" not in configure_text or "Generating done" not in configure_text:
                raise RuntimeError("CMake configure returned success without completion markers")
            validate_cache()
        else:
            record["configure_skipped"] = "existing cache validated against all required settings"

        if configure_only:
            record["status"] = "configure-only-success"
        else:
            build = [str(CMK), "--build", str(BUILD_ROOT), "--config", "Release", "--target", "casc", "--parallel", "1"]
            build_code = run_process(build, logs["build", "stdout"], logs["build", "stderr"])
            record["build_exit_code"] = build_code
            if build_code != 0:
                raise RuntimeError(f"CMake build returned {build_code}")
            dll_path = BUILD_ROOT / "Release" / "CascLib.dll"
            if not dll_path.is_file():
                raise RuntimeError(f"Expected DLL output is missing: {dll_path}")
            record["dll_path"] = str(dll_path)
            record["dll_bytes"] = dll_path.stat().st_size
            record["dll_sha256"] = sha256(dll_path)
            record["status"] = "build-success"
        record["finished_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
        write_record(record_path, record)
        print(json.dumps({"status": record["status"], "configure_exit_code": record.get("configure_exit_code"), "build_exit_code": record.get("build_exit_code"), "dll_path": record.get("dll_path"), "dll_sha256": record.get("dll_sha256"), "record": str(record_path)}, indent=2))
        return 0
    except Exception as exc:
        record["status"] = "failed"
        record["error"] = str(exc)
        record["finished_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
        try:
            write_record(record_path, record)
        except Exception as record_exc:
            print(f"could not write build record: {record_exc}", file=sys.stderr)
        print(f"error: {exc}; record: {record_path}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
