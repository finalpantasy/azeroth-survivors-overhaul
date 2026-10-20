#!/usr/bin/env python3
"""Reference sizes of the native (classic) item models behind each edited ItemDisplayInfo row, and the uniform scale that makes
the converted Retail model's longest axis match it.  Retail item models are authored larger than the native ones, so unscaled
they look oversized in the hand (lab capture gear-armory-warrior vs main baseline).

  gear_scales.py <build-dir> [<build-dir> ...]   reads display-edits.json + payload, writes <build-dir>/scales.json
"""
import importlib.util
import json
import struct
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]
INSPECT = PROJECT / "tools" / "mpq-inspect.exe"
DATA = PROJECT / "Data"
REF = PROJECT / "analysis" / "asset-coverage" / "work" / "ref-models"
spec = importlib.util.spec_from_file_location("ric", HERE / "rigid_item_convert.py")
ric = importlib.util.module_from_spec(spec)
sys.modules["ric"] = ric
spec.loader.exec_module(ric)


def extents(md20):
    cnt, off = struct.unpack_from("<II", md20, ric.V["vertices"])
    pos = [struct.unpack_from("<3f", md20, off + i * 48) for i in range(cnt)]
    mn = [min(p[a] for p in pos) for a in range(3)]
    mx = [max(p[a] for p in pos) for a in range(3)]
    return [mx[a] - mn[a] for a in range(3)], mn, mx


def ref_model(kind, basename):
    REF.mkdir(parents=True, exist_ok=True)
    dest = REF / (basename[:-4] + ".m2")
    if not dest.is_file():
        name = "Item\\ObjectComponents\\" + kind + "\\" + basename[:-4] + ".m2"
        for a in sorted(DATA.glob("*.MPQ"), reverse=True):
            r = subprocess.run([str(INSPECT), str(a), "extract", name, str(dest)], capture_output=True, text=True)
            if r.returncode == 0 and dest.is_file():
                break
        else:
            return None
    return dest.read_bytes()


FIT_MIN, FIT_MAX = 0.5, 1.15
HELM_REF = "Helm_Plate_DungeonWarrior_A_01"     # native helm whose per-race/sex bounding boxes the calibration helm is fitted to
HELM_CAL = "helm_plate_dungeonplate_c_07"       # retail helm used to calibrate every race/sex variant (a plain dome: its box is the head)


def fit(ref, new):
    """Uniform scale (cube root of the bounding-box volume ratio) and the translation that puts the scaled retail model's box
    centre on the native one.  ref/new = (extent, min, max) as returned by extents()."""
    vol = lambda e: e[0] * e[1] * e[2]
    s = max(FIT_MIN, min(FIT_MAX, (vol(ref[0]) / vol(new[0])) ** (1 / 3)))
    cr = [(ref[1][a] + ref[2][a]) / 2 for a in range(3)]
    cn = [(new[1][a] + new[2][a]) / 2 for a in range(3)]
    return {"scale": s, "offset": [cr[a] - s * cn[a] for a in range(3)]}


def fit_main(builds):
    """--fit: helms are fitted per race/sex variant to the native HELM_REF variant; shoulders to the median native box of the displays
    they replace.  Writes <build>/fit.json (id -> scale, offset, ref boxes) from the UNSCALED payload of that build."""
    import statistics as stt
    manifest = json.loads((PROJECT.parent / "previews" / "gear-v0.103.0" / "manifest.json").read_text(encoding="utf8"))
    before = {(e["display"], e["column"]): e["before"] for e in manifest["display_edits"]}
    for b in map(Path, builds):
        b = b.resolve()
        rep = json.loads((b / "gear-report.json").read_text(encoding="utf8"))["results"]
        edits = json.loads((b / "display-edits.json").read_text(encoding="utf8"))
        by_edit = {}
        for e in edits:
            by_edit.setdefault(e["id"], []).append(e)
        out = {}
        for r in rep:
            if not r["ok"] or r["kind"] not in ("Head", "Shoulder"):
                continue
            new = extents((b / "payload" / "Item" / "ObjectComponents" / r["kind"] / (r["name"] + ".m2")).read_bytes())
            if r["kind"] == "Head":
                ref = ref_model("Head", f"{HELM_REF}_{r['name'][-3:]}.mdx")
                ref = extents(ref) if ref else None
            else:
                boxes = []
                for e in by_edit.get(r["id"], []):
                    m = ref_model("Shoulder", before[(e["display"], e["column"])])
                    if m:
                        boxes.append(extents(m))
                ref = None
                if boxes:
                    mid = lambda f: [stt.median(f(x)[a] for x in boxes) for a in range(3)]
                    ref = (mid(lambda x: x[0]), mid(lambda x: x[1]), mid(lambda x: x[2]))
            if not ref:
                continue
            f = fit(ref, new)
            f["ref_extent"] = ref[0]
            f["new_extent"] = new[0]
            out[r["id"]] = f
        # Retail helms of every design share one attachment frame per race/sex, but winged or horned designs have boxes far larger than
        # the head (a box fit shrinks the dome into the skull), so every helm takes the calibration helm's scale/offset for its variant.
        for iid in [i for i in out if i.startswith("helm_") and not i.startswith(HELM_CAL + "-")]:
            cal = out.get(HELM_CAL + "-" + iid.rsplit("-", 1)[1])
            if cal:
                out[iid] = {**out[iid], "scale": cal["scale"], "offset": cal["offset"], "calibrated_from": HELM_CAL}
        (b / "fit.json").write_text(json.dumps(out, indent=1), encoding="utf8")
        print(b.name, len(out), "fitted")


def main():
    manifest = json.loads((PROJECT.parent / "previews" / "gear-v0.103.0" / "manifest.json").read_text(encoding="utf8"))
    before = {e["display"]: e["before"] for e in manifest["display_edits"]}
    for b in map(Path, sys.argv[1:]):
        b = b.resolve()
        edits = json.loads((b / "display-edits.json").read_text(encoding="utf8"))
        out = {}
        for e in edits:
            if e["id"] in out:
                continue
            new = (b / "payload" / "Item" / "ObjectComponents" / e["kind"] / e["new_basename"][:-4]).with_suffix(".m2").read_bytes()
            ne, _, _ = extents(new)
            ref = ref_model(e["kind"], before[e["display"]])
            re_ = extents(ref)[0] if ref else None
            sc = (max(re_) / max(ne)) if re_ else None
            out[e["id"]] = {"display": e["display"], "ref": before[e["display"]], "ref_extent": re_, "new_extent": ne, "scale": sc}
            print(e["id"], before[e["display"]], [round(x, 2) for x in (re_ or [])], [round(x, 2) for x in ne], round(sc, 3) if sc else None)
        (b / "scales.json").write_text(json.dumps(out, indent=1), encoding="utf8")


if __name__ == "__main__":
    if sys.argv[1] == "--fit":
        fit_main(sys.argv[2:])
    else:
        main()
