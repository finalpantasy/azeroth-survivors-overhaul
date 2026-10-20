"""Convert one effect model from a Retail/Forever dump and show the engine's own decode of the result.
Usage: fx_test.py <native model path like spells/fireball_missile_high.m2> <retail|forever> [tool: m2part|m2anim|m2batch|m2seq]
"""
import json
import shutil
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(r".")
sys.path.insert(0, str(ROOT / "tools/spells"))
sys.path.insert(0, str(ROOT / "tools/skinned-converter"))
import fx_convert as fx  # noqa: E402
import m2ir  # noqa: E402
import skinned_convert as sc  # noqa: E402

BS = chr(92)
A = ROOT / "analysis/spells"


def load_lf():
    lf = {}
    for ln in open(ROOT / "analysis/asset-coverage/work/listfile-filtered.csv", encoding="utf8"):
        i, _, p = ln.rstrip("\n").partition(";")
        lf[int(i)] = p
    return lf


def convert_one(name, prof, lf, fb, tex_path=None):
    ids = json.load(open(A / "casc/model-fdids.json"))
    key = name.replace("/", BS)
    fd = ids[key]
    mb = (A / f"casc/models-{prof}/{fd}.bin").read_bytes()
    ch = m2ir.split_chunks(mb)
    sk = struct.unpack("<%dI" % (len(ch["SFID"]) // 4), ch["SFID"])[0]
    skin = (A / f"casc/skins-{prof}/{sk}.bin").read_bytes()
    tmpl = (A / "baseline/m2" / key.replace(BS, "__")).read_bytes()
    rep = {"model": key, "profile": prof, "fdid": fd}
    tp = tex_path or (lambda slot, t: "Spells" + BS + lf.get(t["fdid"], str(t["fdid"])).split("/")[-1])
    out = fx.convert(mb, skin, {}, key.split(BS)[-1][:-3], tmpl, fb, tp, rep)
    return out, rep


def main():
    name, prof = sys.argv[1], sys.argv[2]
    tool = sys.argv[3] if len(sys.argv) > 3 else "m2part"
    lf = load_lf()
    work = A / "work"
    work.mkdir(exist_ok=True)
    fb = sc.load_animation_fallbacks(A / "baseline-data", work)
    out, rep = convert_one(name, prof, lf, fb)
    tree = work / "fxtest-tree"
    if tree.exists():
        shutil.rmtree(tree)
    p = tree / name.replace(BS, "/")
    p.parent.mkdir(parents=True)
    p.write_bytes(out)
    mpq = work / "fxtest.mpq"
    if mpq.exists():
        mpq.unlink()
    subprocess.run([sys.executable, str(ROOT / "tools/pack-mpq.py"), str(tree), str(mpq)], check=True, capture_output=True)
    print(json.dumps(rep, indent=1)[:1200])
    r = subprocess.run([str(ROOT / "tools/mpq-inspect.exe"), str(mpq), tool, name.replace("/", BS)], capture_output=True, text=True)
    print(r.stdout[:6000], r.stderr[:500])


if __name__ == "__main__":
    main()
