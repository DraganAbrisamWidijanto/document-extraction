"""Run eval_harness on both test sets (CORD 45 + self 5), save summaries to results/."""
import subprocess, os, sys

BASE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(BASE, "results")
os.makedirs(RES, exist_ok=True)

PY = sys.executable

cmds = [
    ("cord", ["--manifest", "data/cord_pool/manifest_test.json", "--pred-dir", "data/predictions", "--gt-format", "cord"]),
    ("self", ["--manifest", "data/self_scan/manifest.json", "--pred-dir", "data/predictions", "--gt-format", "self"]),
]

for name, args in cmds:
    print(f"\n########## {name.upper()} ##########")
    r = subprocess.run([PY, "eval_harness.py"] + args, capture_output=True, text=True, cwd=BASE)
    out = r.stdout
    print(out)
    if r.returncode != 0:
        print("STDERR:", r.stderr[-2000:])
    with open(os.path.join(RES, f"eval_{name}.txt"), "w") as f:
        f.write(out)
