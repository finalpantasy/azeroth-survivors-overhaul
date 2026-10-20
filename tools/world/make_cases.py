#!/usr/bin/env python3
"""W10: write lab cases (one per zone x build) with fixed camera views and a fixed observation schedule.

Camera views A-D are set through CVars (same keys as analysis/runtime/water-overlook-max50-profile.toml) and cycled with
the Home key (PostMessage keys reach the world, per the lab README). Each view is held ~7 s so the periodic lab
frames contain: top-down wide (A), oblique (B), close third-person (C), low horizon (D).
Usage: python -I make_cases.py [--build main|world]   -> tools/world/cases/world-<zone>-<build>.json
"""
import argparse, json
from pathlib import Path

ZONES = ["elwynn", "duskwood", "wsg", "ab", "feralas", "arena", "av", "brm", "aq"]
HERE = Path(__file__).resolve().parent
VIEWS = {"A": (50, 75, 180), "B": (28, 38, 180), "C": (9, 16, 180), "D": (40, 8, 180)}


def toml():
    lines = ['[cvars]', 'SoundListenerAtCharacter = "1"', 'MasterSoundEffects = "0"', 'MasterVolume = "0"',
             'anisotropic = "16"', 'gxMultisample = "4"', 'gxWindow = "1"', 'cameraView = "0"',
             'cameraDistanceMax = "50"', 'cameraDistanceMaxFactor = "1"']
    for k, (d, p, y) in VIEWS.items():
        lines += ['cameraDistance%s = "%d"' % (k, d), 'cameraPitch%s = "%d"' % (k, p), 'cameraYaw%s = "%d"' % (k, y)]
    return "\n".join(lines) + "\n"


def case(zone, build):
    return {
        "id": "world-%s-%s" % (zone, build),
        "description": "W10 world check: %s on %s. Quick Human mage; views A (wide top-down), B, C (close), D (horizon) cycled with Home; frames are compared main vs world preview." % (zone, build),
        "build": "main" if build == "main" else {"name": "world-preview", "exe": "previews/world-v0.103.0/AzerothSurvivors.exe",
                                                   "data": "previews/world-v0.103.0/Data"},
        "boot": {"win": "1280x720", "dpi": 1, "seconds": 50},
        "mode": {"quick": [zone, "mage", "human"]},
        "config_toml": toml(),
        # tap W to start the run (game waits at "Move to begin"), cycle the 4 saved camera views near the spawn, then walk and zoom out
        "os_actions": [{"at": 10, "type": "key", "key": "W", "hold": 0.3},
                       {"at": 13, "type": "key", "key": "HOME"},
                       {"at": 18, "type": "key", "key": "HOME"},
                       {"at": 23, "type": "key", "key": "HOME"},
                       {"at": 28, "type": "key", "key": "HOME"},
                       {"at": 33, "type": "key", "key": "W", "hold": 8},
                       {"at": 44, "type": "wheel", "notches": -8}],
        "auto_levelup": True,
        "observe": {"frame_change": True},
        "expect": [{"nonblank_frames": 15}],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", choices=["main", "world", "both"], default="both")
    a = ap.parse_args()
    out = HERE / "cases"
    out.mkdir(exist_ok=True)
    for z in ZONES:
        for b in (["main", "world"] if a.build == "both" else [a.build]):
            (out / ("world-%s-%s.json" % (z, b))).write_text(json.dumps(case(z, b), indent=1))
    print("wrote", len(list(out.glob("*.json"))), "cases in", out)


if __name__ == "__main__":
    main()
