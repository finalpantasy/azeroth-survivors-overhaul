"""Rewrite the PINNED hashes in spells_step.py from the files on disk (run after any converter change)."""
import re, subprocess, sys, json
from pathlib import Path
h = Path(__file__).resolve().parent
pins = json.loads(subprocess.run([sys.executable, str(h / "spells_step.py"), "--print-pins"], capture_output=True, text=True, check=True).stdout)
s = (h / "spells_step.py").read_text(encoding="utf8")
for k, v in pins.items():
    s = re.sub(r'("%s": )"[0-9a-f]*"' % re.escape(k), r'\1"%s"' % v, s)
(h / "spells_step.py").write_text(s, encoding="utf8")
print("pinned", len(pins))
