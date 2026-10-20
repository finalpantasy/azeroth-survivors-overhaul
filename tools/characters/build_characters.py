#!/usr/bin/env python3
"""Per-race/gender HD character payload builder (generalizes the Human male pipeline).

For every playable race x gender (16 models) it:
  * takes the community VanillaHD/Patch-A M2 (pinned commit), repairs the attachment lookup
    (append a full-length AttachLookup so item attachment ids up to 54 resolve), trims the
    vertex array count to the highest vertex any view references (count-only edit at 0x44),
    and writes it to a private path next to the native one (<Base>HD.m2);
  * appends one CreatureModelData row per model (copy of the native row, new ModelName) and
    repoints ONLY the base player display id's ModelID (every other display keeps the native model);
  * rewrites CharSections cells for all native rows of that race/sex so every skin/face/hair/
    facial-hair/underwear counter value has HD textures, aliased into gender-private paths
    (Character\\<Race>\\<Sex>\\...) so the generic native paths stay untouched;
  * writes coverage/fallback/cost reports.

Nothing is installed and no game data is modified: output is a loose payload tree plus
(optionally, --pack) an MPQ under mod-source/characters-hd/, and reports under analysis/characters/.

  python tools/characters/build_characters.py            # dry run: preflight + reports only
  python tools/characters/build_characters.py --build    # write payload tree
  python tools/characters/build_characters.py --build --pack
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dbclib import DBC  # noqa: E402
from m2lib import M2, trim_vertex_count  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
REPO = ROOT / "analysis" / "characters" / "vanillahd-src" / "repo"
REPO_COMMIT = "dcf3647e80104d03c02e1540c61cce3ca7e24766"
NATIVE_DBC = ROOT / "analysis" / "characters" / "native-dbc"
BASE_PAYLOAD = ROOT / "analysis" / "runtime" / "human-scope-mace-payload"
OUT = ROOT / "mod-source" / "characters-hd"
PAYLOAD = OUT / "payload"
REPORTS = ROOT / "analysis" / "characters"
INSPECT = TOOLS / "mpq-inspect.exe"
PACKER = TOOLS / "pack-mpq.py"
DATA = ROOT / "Data"

# CharSections.dbc baseline hash recorded by the Human male overlay audit
NATIVE_CHARSECTIONS_SHA256 = "376b24e03742fa019053dd58519769442eb09d7ea68ab9dcb305a321710cce26"
# existing verified Human male trim output (regression pin): repaired M2 and count-trimmed M2
HUMAN_REPAIRED_SHA256 = "bd6b2d2bfef402404a2949e082b1a75be7d9d8b09c60bd503d3342159ceff532"
HUMAN_TRIMMED_SHA256 = "7098e96b56a1e75db5b4458482792278b842840e2a2fcf6090c165d16283f15e"

FIRST_PRIVATE_MODEL_ID = 2366

# (race_id, race label, sex_id, base display id) in the order private model ids are allocated
PLAYABLE = [
    (1, "Human", 0, 49), (1, "Human", 1, 50),
    (2, "Orc", 0, 51), (2, "Orc", 1, 52),
    (3, "Dwarf", 0, 53), (3, "Dwarf", 1, 54),
    (4, "NightElf", 0, 55), (4, "NightElf", 1, 56),
    (5, "Scourge", 0, 57), (5, "Scourge", 1, 58),
    (6, "Tauren", 0, 59), (6, "Tauren", 1, 60),
    (7, "Gnome", 0, 1563), (7, "Gnome", 1, 1564),
    (8, "Troll", 0, 1478), (8, "Troll", 1, 1479),
]
SEX = {0: "Male", 1: "Female"}


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def bs(s: str) -> str:
    return s.replace("/", "\\")


# ----------------------------------------------------------------------------------- repo index
class Tree:
    """Case-insensitive index of the VanillaHD working tree (Character/ + DBFilesClient/)."""

    def __init__(self, root: Path):
        self.files: dict[str, Path] = {}
        for sub in ("Character",):
            for r, _, fs in os.walk(root / sub):
                for f in fs:
                    p = Path(r) / f
                    rel = bs(p.relative_to(root).as_posix())
                    self.files[rel.lower()] = p

    def get(self, mpq_path: str) -> Path | None:
        return self.files.get(bs(mpq_path).lower())


# ----------------------------------------------------------------------------------- model prep
def repair_attachment_lookup(data: bytes) -> bytes:
    m = M2(data)
    atts = m.attachments()
    max_id = max(a for a, _ in atts)
    lookup = [0xFFFF] * (max_id + 1)
    for idx, (aid, _bone) in reversed(list(enumerate(atts))):
        lookup[aid] = idx
    cur = m.attachment_lookup()
    # keep the author's existing entries verbatim (four models swap ids 21/22, the spell hand points);
    # they only have to stay inside the attachment table
    for i, v in enumerate(cur):
        if v != 0xFFFF and v >= len(atts):
            raise ValueError(f"existing attachment lookup entry {i} points past the attachment table: {v}")
        lookup[i] = v
    if len(cur) >= max_id + 1:
        return data
    b = bytearray(data)
    while len(b) % 4:
        b.append(0)
    off = len(b)
    b += struct.pack(f"<{len(lookup)}H", *lookup)
    struct.pack_into("<II", b, 0x10C, len(lookup), off)
    return bytes(b)


def prepare_model(data: bytes) -> tuple[bytes, dict]:
    m0 = M2(data)
    full = m0.count("vertices")
    repaired = repair_attachment_lookup(data)
    m1 = M2(repaired)
    extent = m1.used_vertex_extent()
    if extent <= 0:
        raise ValueError("model references no vertices")
    trimmed = trim_vertex_count(repaired, extent) if extent < full else repaired
    info = dict(vertices_full=full, vertices_trimmed=extent, repaired_sha256=sha256_bytes(repaired),
                trimmed_sha256=sha256_bytes(trimmed), repaired_bytes=len(repaired))
    return trimmed, info


# ----------------------------------------------------------------------------------- DBC edits
def load_native(name: str) -> DBC:
    return DBC((NATIVE_DBC / f"{name}.dbc").read_bytes())


def private_name(native_model_path: str) -> str:
    d, base = native_model_path.rsplit("\\", 1)
    stem = re.sub(r"\.(mdx|m2)$", "", base, flags=re.I)
    return f"{d}\\{stem}HD.m2"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build", action="store_true", help="write the payload tree (refuses to overwrite)")
    ap.add_argument("--pack", action="store_true", help="also pack the MPQ (BelowNormal; heavy lock is the caller's job)")
    ap.add_argument("--only", help="comma list of display ids to restrict (debug)")
    args = ap.parse_args()

    head = (REPO / ".git" / "HEAD")
    if not REPO.is_dir():
        raise SystemExit("VanillaHD tree missing; see README.md")
    commit = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    if commit != REPO_COMMIT:
        raise SystemExit(f"VanillaHD commit {commit} != pinned {REPO_COMMIT}")

    tree = Tree(REPO)
    cs_n = load_native("CharSections")
    if sha256_bytes((NATIVE_DBC / "CharSections.dbc").read_bytes()) != NATIVE_CHARSECTIONS_SHA256:
        raise SystemExit("native CharSections.dbc differs from the audited baseline")
    cs_u = DBC((REPO / "DBFilesClient" / "CharSections.dbc").read_bytes())
    cmd = load_native("CreatureModelData")
    cdi = load_native("CreatureDisplayInfo")

    def row_key(r): return (r[1], r[2], r[3], r[4], r[5], r[9])
    up_rows = {row_key(r): r for r in cs_u.rows}
    cdi_by_id = {r[0]: r for r in cdi.rows}
    cmd_by_id = {r[0]: r for r in cmd.rows}

    only = {int(x) for x in args.only.split(",")} if args.only else None
    summary = []
    staged: dict[str, Path | bytes] = {}   # mpq path (lower) -> (mpq path, source)
    staged_names: dict[str, str] = {}

    def stage(mpq_path: str, src: Path | bytes):
        k = mpq_path.lower()
        if k in staged:
            old = staged[k]
            same = (isinstance(old, Path) and isinstance(src, Path) and (old == src or sha256_file(old) == sha256_file(src)))
            same = same or (isinstance(old, bytes) and isinstance(src, bytes) and old == src)
            if not same:
                raise ValueError(f"payload path collision with different content: {mpq_path}")
            return
        staged[k] = src
        staged_names[k] = mpq_path

    new_cmd_rows = []
    new_cdi_edits = {}
    per_model = []
    texmap_rows = []
    cell_report = []

    model_id = FIRST_PRIVATE_MODEL_ID
    for race, rname, sex, disp in PLAYABLE:
        if only and disp not in only:
            model_id += 1
            continue
        c = cdi_by_id[disp]
        native_cmd = cmd_by_id[c[1]]
        native_path = cmd.string(native_cmd[2])
        sex_dir = SEX[sex]
        parts = native_path.split("\\")
        race_dir = parts[1]
        if parts[2].lower() != sex_dir.lower():
            raise ValueError(f"display {disp}: unexpected native path {native_path}")
        priv = private_name(native_path)
        base_stem = parts[-1].rsplit(".", 1)[0]
        src_m2 = tree.get(f"Character\\{race_dir}\\{sex_dir}\\{base_stem}.m2")
        if src_m2 is None:
            raise ValueError(f"no HD model for {native_path}")
        data = src_m2.read_bytes()
        raw_sha = sha256_bytes(data)
        model_bytes, info = prepare_model(data)
        if disp == 49:
            if info["repaired_sha256"] != HUMAN_REPAIRED_SHA256 or info["trimmed_sha256"] != HUMAN_TRIMMED_SHA256:
                raise ValueError("Human male regression check failed: output differs from the verified trim")
        stage(priv, model_bytes)
        new_cmd_rows.append((model_id, native_cmd, priv))
        new_cdi_edits[disp] = model_id

        # ---- textures for this race/sex
        alias_dir = f"Character\\{race_dir}\\{sex_dir}\\"
        n_cells = n_hd = n_native_fallback = n_up = 0
        tex_bytes = 0
        model_rows = [r for r in cs_n.rows if r[1] == race and r[2] == sex]
        new_cells = {}
        for r in model_rows:
            nt = [cs_n.string(x) for x in r[6:9]]
            ut = None
            u = up_rows.get(row_key(r))
            if u is not None:
                cand = [cs_u.string(x) for x in u[6:9]]
                if cand != nt and all((p == "" or tree.get(p) is not None) for p in cand):
                    ut = cand
            final = list(ut if ut is not None else nt)
            if ut is not None:
                n_up += 1
            out = []
            for cell_i, p in enumerate(final):
                if not p:
                    out.append(p)
                    continue
                n_cells += 1
                src = tree.get(p)
                if src is None:
                    n_native_fallback += 1
                    out.append(p)
                    cell_report.append(dict(display=disp, race=rname, sex=sex_dir, row_id=r[0], section=r[3], variation=r[4],
                                            color=r[5], flags=r[9], cell=cell_i, path=p, source="native-fallback"))
                    continue
                low = p.lower()
                base = p.rsplit("\\", 1)[1]
                if low.startswith(alias_dir.lower()):
                    mp = p
                else:
                    mp = alias_dir + base
                stage(mp, src)
                tex_bytes += src.stat().st_size
                n_hd += 1
                out.append(mp)
                texmap_rows.append((disp, p, mp, str(src.relative_to(REPO)).replace("\\", "/")))
            new_cells[r[0]] = out
        for rid, out in new_cells.items():
            pass

        # ---- hardcoded model textures (type 0 with a path): ship same path, from the tree
        m = M2(model_bytes)
        hard = []
        for t_type, t_flags, t_path in m.textures():
            if t_path:
                s = tree.get(t_path)
                hard.append((t_path, s is not None))
                if s is not None:
                    stage(t_path, s)
        per_model.append(dict(
            display=disp, race=rname, sex=sex_dir, private_model_id=model_id, native_model_id=native_cmd[0],
            native_path=native_path, private_path=priv, source_m2=str(src_m2.relative_to(REPO)).replace("\\", "/"),
            source_m2_sha256=raw_sha, **info,
            charsections_rows=len(model_rows), cells=n_cells, hd_cells=n_hd, native_fallback_cells=n_native_fallback,
            rows_using_upstream_triple=n_up, hardcoded_textures=hard, texture_bytes=tex_bytes,
        ))
        # keep the cell edits for the DBC pass
        per_model[-1]["_cells"] = new_cells
        model_id += 1

    # ---- DBC output (built from native tables)
    cmd_out = load_native("CreatureModelData")
    for mid, ncmd, priv in new_cmd_rows:
        row = list(ncmd)
        row[0] = mid
        row[2] = cmd_out.add_string(priv)
        cmd_out.rows.append(row)
    cdi_out = load_native("CreatureDisplayInfo")
    for r in cdi_out.rows:
        if r[0] in new_cdi_edits:
            r[1] = new_cdi_edits[r[0]]
    cs_out = load_native("CharSections")
    cells_by_id = {}
    for pm in per_model:
        cells_by_id.update(pm.pop("_cells"))
    changed_rows = 0
    for r in cs_out.rows:
        if r[0] in cells_by_id:
            new = cells_by_id[r[0]]
            old = [cs_out.string(x) for x in r[6:9]]
            if new != old:
                changed_rows += 1
            for i in range(3):
                r[6 + i] = cs_out.add_string(new[i]) if new[i] else 0
    dbc_bytes = {
        "DBFilesClient\\CreatureModelData.dbc": cmd_out.pack(),
        "DBFilesClient\\CreatureDisplayInfo.dbc": cdi_out.pack(),
        "DBFilesClient\\CharSections.dbc": cs_out.pack(),
    }
    for k, v in dbc_bytes.items():
        stage(k, v)

    # ---- reports
    REPORTS.mkdir(parents=True, exist_ok=True)
    report = dict(repo_commit=REPO_COMMIT, models=per_model, charsections_rows_changed=changed_rows,
                  private_model_ids=[(pm["display"], pm["private_model_id"]) for pm in per_model])
    (REPORTS / "build-report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    with (REPORTS / "native-fallback-cells.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["display", "race", "sex", "row_id", "section", "variation", "color", "flags", "cell", "path", "source"])
        w.writeheader()
        w.writerows(cell_report)
    with (REPORTS / "texture-map.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["display", "dbc_path", "overlay_path", "source_in_repo"])
        w.writerows(texmap_rows)
    print(f"{len(per_model)} models; {len(staged)} payload entries; {changed_rows} CharSections rows changed; "
          f"{len(cell_report)} native-fallback cells")
    for pm in per_model:
        print(f"  disp {pm['display']:>4} {pm['race']:<9}{pm['sex']:<7} model {pm['private_model_id']} verts {pm['vertices_full']}->{pm['vertices_trimmed']} "
              f"cells {pm['hd_cells']}/{pm['cells']} fallback {pm['native_fallback_cells']} up-rows {pm['rows_using_upstream_triple']}")

    if not args.build:
        print("dry run: nothing written (pass --build)")
        return 0

    if PAYLOAD.exists():
        raise SystemExit(f"{PAYLOAD} already exists; refusing to overwrite (remove it first)")
    # base non-character payload (spells, mace, ItemDisplayInfo) as hardlinks
    skip_prefix = ("character/",)
    skip_files = {"dbfilesclient/charsections.dbc", "dbfilesclient/creaturedisplayinfo.dbc", "dbfilesclient/creaturemodeldata.dbc"}
    n_base = 0
    for r, _, fs in os.walk(BASE_PAYLOAD):
        for f in fs:
            p = Path(r) / f
            rel = p.relative_to(BASE_PAYLOAD).as_posix()
            if rel.lower().startswith(skip_prefix) or rel.lower() in skip_files:
                continue
            dest = PAYLOAD / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.link(p, dest)
            n_base += 1
    rows = []
    for k, src in staged.items():
        mp = staged_names[k]
        dest = PAYLOAD.joinpath(*mp.split("\\"))
        if dest.exists():
            raise ValueError(f"payload collision with base payload: {mp}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(src, bytes):
            with dest.open("xb") as f:
                f.write(src)
        else:
            try:
                os.link(src, dest)
            except OSError:
                shutil.copy2(src, dest)
        rows.append((mp, dest.stat().st_size))
    print(f"payload written: {len(rows)} generated entries + {n_base} base entries at {PAYLOAD}")
    (OUT / "payload-listing.json").write_text(json.dumps(rows, indent=0), encoding="utf-8")

    if args.pack:
        mpq = OUT / "characters-hd.MPQ"
        if mpq.exists():
            raise SystemExit(f"{mpq} exists; refusing to overwrite")
        t0 = time.monotonic()
        res = subprocess.run([sys.executable, str(PACKER), str(PAYLOAD), str(mpq)], capture_output=True, text=True)
        if res.returncode:
            raise SystemExit(f"pack failed: {res.stdout}\n{res.stderr}")
        print(f"packed {mpq} ({mpq.stat().st_size:,} bytes) in {time.monotonic() - t0:.0f}s: {res.stdout.strip()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
