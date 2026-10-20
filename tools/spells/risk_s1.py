"""Compare sequence tables (m2seq) of native vs candidate models in s1.MPQ; flag timing/loop changes."""
import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(r".")
A = ROOT / "analysis/spells"
INSPECT = str(ROOT / "tools/mpq-inspect.exe")
man = json.load(open(A / "s1/manifest.json"))


def seq(path, model):
    p = subprocess.run([INSPECT, path, "m2seq", model.replace("\\", "/")], capture_output=True, text=True)
    rows = []
    for ln in p.stdout.splitlines():
        m = re.match(r"\s*(\d+)\s+(\d+)\s+(\w+)\s+([\d.]+)", ln)
        if m:
            rows.append((int(m.group(2)), m.group(3), float(m.group(4))))
    return rows


def one(m):
    return m, seq(str(A / "baseline-data"), m), seq(str(A / "s1/s1.MPQ"), m)


with ThreadPoolExecutor(3) as ex:
    res = list(ex.map(one, sorted(man["models"])))
out = {}
for m, n, c in res:
    flag = []
    if [(a, l) for a, l, d in n] != [(a, l) for a, l, d in c]:
        flag.append("sequence-ids/loop differ")
    nd = {a: d for a, l, d in n}
    cd = {a: d for a, l, d in c}
    for a in nd:
        if a in cd and abs(nd[a] - cd[a]) > 0.05:
            flag.append(f"anim {a} dur {nd[a]:.2f}->{cd[a]:.2f}")
    out[m] = {"native": n, "cand": c, "flags": flag}
    print(m, "OK" if not flag else flag, "native", n[:3], "cand", c[:3])
json.dump(out, open(A / "s1/risk.json", "w"), indent=1)
