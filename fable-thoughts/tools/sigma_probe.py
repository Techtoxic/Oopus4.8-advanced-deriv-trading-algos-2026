"""sigma_probe.py — one-shot: measure tick sigma in pips for any symbol.
Read-only. Usage: python sigma_probe.py R_100 40000
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np

from deriv_api import DerivWS
from derivfetch import fetch_ticks

sym = sys.argv[1] if len(sys.argv) > 1 else '1HZ100V'
n = int(sys.argv[2]) if len(sys.argv) > 2 else 40000

ws = DerivWS()
t, p, pip = fetch_ticks(ws, sym, n, verbose=False)
ws.close()
dp = np.diff(p)
pips = np.round(dp * (10 ** pip))
s = float(np.std(pips[np.abs(pips) < 200]))

out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   '..', 'results', 'sigma_table.jsonl')
import json as _json
with open(out, 'a') as _f:
    _f.write(_json.dumps({'symbol': sym, 'sigma_pips': round(float(s), 3),
                          'n': int(len(p)), 'pip': pip,
                          'last': float(p[-1]), 'epoch': int(t[-1])}) + '\n')
print(f"[*] appended {out}")

print(f"SIGMA_{sym} {s:.2f} pips  N={len(p)} pip={pip} last={p[-1]}")
