#!/usr/bin/env python3
"""NPC-contamination test rig: builds a patch-9.MPQ that turns every Kobold display into a native-model human
male NPC with a CreatureDisplayInfoExtra (so the standard Elwynn autoplay shows humanoid NPCs on the NATIVE
model), staged on top of (A) the main install Data chain (native textures) or (B) the characters lab Data chain
(HD CharSections rows). Compare frames A vs B (and later C with the fix).

  python make_npc_test.py --base main|chars --out previews/<name>
Never touches the main install or the preview the player is in game (reads hardlinks only).
"""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dbclib import DBC  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
WS = ROOT.parent
ND = ROOT / "analysis" / "characters" / "native-dbc"
PAYLOAD = ROOT / "mod-source" / "characters-hd" / "payload"
SCRATCH = ROOT / "analysis" / "characters" / "npc-test"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", choices=["main", "chars"], required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--baked", action="store_true", help="pick human-male extras that HAVE a baked texture in the data chain (the NPCs the game really shows)")
    ap.add_argument("--extra-patch", help="optional dir of extra payload files merged into patch-9 (e.g. the fix)")
    a = ap.parse_args()
    out = WS / "previews" / a.out
    if out.exists():
        raise SystemExit(f"exists: {out}")
    base_data = ROOT / "Data" if a.base == "main" else WS / "previews" / "characters-lab-v0.103.0" / "Data"
    cdi_src = ND / "CreatureDisplayInfo.dbc" if a.base == "main" else PAYLOAD / "DBFilesClient" / "CreatureDisplayInfo.dbc"
    cdi = DBC(cdi_src.read_bytes())
    cmd = DBC((ND / "CreatureModelData.dbc").read_bytes())
    kob = {r[0] for r in cmd.rows if "kobold" in cmd.string(r[2]).lower()}
    ext = DBC((ND / "CreatureDisplayInfoExtra.dbc").read_bytes())
    # human male extras with facial hair, spread over skin colors
    if a.baked:
        names = {l.strip().split(chr(92))[-1].lower() for l in open(SCRATCH / "baked-list.txt")}
        cands = [r for r in ext.rows if r[1] == 1 and r[2] == 0 and r[18] and ext.string(r[18]).lower() in names]
    else:
        cands = [r for r in ext.rows if r[1] == 1 and r[2] == 0 and r[7] > 0]
    cands.sort(key=lambda r: (r[3], r[4]))
    step = max(1, len(cands) // 14)
    picks = cands[::step][:14]
    print('picks', len(cands), [(p[0], p[3], p[4], p[5], p[6], p[7]) for p in picks])
    k = 0
    for r in cdi.rows:
        if r[1] in kob:
            r[1] = 49          # native HumanMale model
            r[3] = picks[k % len(picks)][0]
            r[4] = 1065353216
            k += 1
    SCRATCH.mkdir(parents=True, exist_ok=True)
    src = SCRATCH / f"payload-{a.base}"
    if src.exists():
        shutil.rmtree(src)
    (src / "DBFilesClient").mkdir(parents=True)
    (src / "DBFilesClient" / "CreatureDisplayInfo.dbc").write_bytes(cdi.pack())
    if a.extra_patch:
        shutil.copytree(a.extra_patch, src, dirs_exist_ok=True)
    mpq = SCRATCH / f"patch-9-{a.base}.MPQ"
    if mpq.exists():
        mpq.unlink()
    subprocess.run([sys.executable, str(ROOT / "tools" / "pack-mpq.py"), str(src), str(mpq)], check=True)
    # stage
    (out / "Data").mkdir(parents=True)
    for f in base_data.iterdir():
        if f.is_file():
            os.link(f, out / "Data" / f.name)
    shutil.copy2(mpq, out / "Data" / "patch-9.MPQ")
    exe_src = WS / "previews" / "characters-lab-v0.103.0" / "AzerothSurvivors.exe"
    shutil.copy2(exe_src, out / "AzerothSurvivors.exe")
    shutil.copytree(WS / "previews" / "characters-lab-v0.103.0" / "config", out / "config")
    shutil.copytree(WS / "previews" / "characters-lab-v0.103.0" / "saves", out / "saves")
    print("staged", out, "kobold displays remapped:", k, "extras:", [p[0] for p in picks])


if __name__ == "__main__":
    main()
