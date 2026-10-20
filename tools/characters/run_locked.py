#!/usr/bin/env python3
"""Run one command under coordination/locks/heavy.lock at BelowNormal priority (rule 4 of PLAN.md).

  python run_locked.py --what "pack characters MPQ" --wait 1500 -- python pack-mpq.py src out

Creates the lock atomically (O_EXCL) with owner/pid/what/started, waits if a live PID holds it,
clears a stale (dead PID) lock, always removes only its own lock afterwards. Cap: --cap seconds
(default 600; the child is killed at the cap).
"""
import argparse
import ctypes
import json
import os
import subprocess
import sys
import time
from pathlib import Path

LOCK = Path(r"locks/heavy.lock")


def alive(pid: int) -> bool:
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.restype = ctypes.c_void_p
    h = k.OpenProcess(0x1000, False, pid)
    if not h:
        return False
    code = ctypes.c_ulong()
    k.GetExitCodeProcess(ctypes.c_void_p(h), ctypes.byref(code))
    k.CloseHandle(ctypes.c_void_p(h))
    return code.value == 259


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--what", required=True)
    ap.add_argument("--wait", type=int, default=1500)
    ap.add_argument("--cap", type=int, default=600)
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    a = ap.parse_args()
    cmd = a.cmd[1:] if a.cmd and a.cmd[0] == "--" else a.cmd
    deadline = time.time() + a.wait
    tmp = LOCK.with_name(f"heavy.lock.characters.{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"owner": "characters", "pid": os.getpid(), "what": a.what,
                               "started": time.strftime("%Y-%m-%dT%H:%M:%S")}))
    while True:
        try:
            os.link(tmp, LOCK)      # atomic create-if-absent with complete content
            break
        except FileExistsError:
            try:
                held = json.loads(LOCK.read_text())
                if not alive(int(held.get("pid", 0))):
                    LOCK.unlink(missing_ok=True)
                    continue
            except Exception:
                pass
            if time.time() > deadline:
                print("lock wait timed out", file=sys.stderr)
                tmp.unlink(missing_ok=True)
                return 3
            time.sleep(5)
    tmp.unlink(missing_ok=True)
    proc = subprocess.Popen(cmd, creationflags=0x00004000)  # BELOW_NORMAL_PRIORITY_CLASS
    try:
        rc = proc.wait(timeout=a.cap)
    except subprocess.TimeoutExpired:
        proc.kill()
        print("cap reached; child killed", file=sys.stderr)
        rc = 4
    finally:
        try:
            if json.loads(LOCK.read_text()).get("owner") == "characters":
                LOCK.unlink()
        except Exception:
            pass
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
