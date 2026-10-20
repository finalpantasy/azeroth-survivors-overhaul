#!/usr/bin/env python3
"""Generate one lab case per race/gender for the characters preview (Armory front view + each counter stepped twice)."""
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent / "cases"
ROOT = "previews/characters-v0.103.0"
RACES = [("human", 55), ("dwarf", 88), ("nightelf", 121), ("gnome", 153), ("orc", 191), ("undead", 224), ("tauren", 256), ("troll", 289)]
SEX = [("male", 210), ("female", 270)]
FIELDS = ["skin", "face", "hair_style", "hair_color", "facial_hair"]
for race, rx in RACES:
    for sex, sx in SEX:
        taps = [{"x": rx, "y": 146}, {"x": sx, "y": 174}]
        for f in FIELDS:
            taps += [{"target": f + "_next"}] * 2
        # back to start of facial hair etc. not needed; one prev to prove prev path
        case = {"id": f"chars-{race}-{sex}", "description": f"characters-hd preview: {race} {sex}, Armory, each counter next x2",
                "build": {"name": "characters-v0.103.0", "exe": ROOT + "/AzerothSurvivors.exe", "data": ROOT + "/Data"},
                "boot": {"win": "1280x720", "dpi": 1, "seconds": 80}, "mode": {"armory": True}, "lead_noop_taps": 2,
                "taps": taps, "observe": {"armory_counters": True}}
        (OUT / f"{case['id']}.json").write_text(json.dumps(case, indent=1))
print(len(list(OUT.glob("*.json"))), "cases")
