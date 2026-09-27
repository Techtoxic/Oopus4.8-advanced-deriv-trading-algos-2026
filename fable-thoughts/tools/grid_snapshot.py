"""grid_snapshot.py — read the CURRENT proposal payout grid for digit contracts.

Cheap, read-only: no buys. Prints the full OVER/UNDER/MATCH/DIFF/EVEN/ODD grid
for each symbol so a repricing is visible in one second. The `payout_audit.py`
loop compares proposal and buy from two different ticks and its "PROPOSAL LIES"
label is not safe; this is the clean read of what Deriv is actually quoting now.

Run: python3 grid_snapshot.py JD100 1HZ100V
     python3 grid_snapshot.py --all
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from deriv_api import DerivWS

JUMP = ['JD10', 'JD25', 'JD50', 'JD75', 'JD100']
VOL2S = ['R_10', 'R_25', 'R_50', 'R_75', 'R_100']
VOL1S = ['1HZ10V', '1HZ25V', '1HZ50V', '1HZ75V', '1HZ100V']
ALL = JUMP + VOL2S + VOL1S


def quote(ws, sym, ctype, barrier, stake=1.0):
    p = dict(amount=stake, basis='stake', currency='USD',
             underlying_symbol=sym, contract_type=ctype,
             duration=1, duration_unit='t')
    if barrier is not None:
        p['barrier'] = str(barrier)
    r = ws.call({'proposal': 1, **p})
    prop = r.get('proposal')
    if not prop:
        msg = r.get('error', {}).get('message', 'n/a')
        return None, msg
    return float(prop['payout']) / stake, None


def snapshot(ws, sym):
    out = {'symbol': sym, 'over': {}, 'under': {}, 'even': None,
           'odd': None, 'match0': None, 'diff0': None, 'errors': {}}
    for b in range(0, 9):
        v, err = quote(ws, sym, 'DIGITOVER', b)
        if v is None:
            out['errors'][f'OVER{b}'] = err
        else:
            out['over'][b] = round(v, 4)
    for b in range(1, 10):
        v, err = quote(ws, sym, 'DIGITUNDER', b)
        if v is None:
            out['errors'][f'UNDER{b}'] = err
        else:
            out['under'][b] = round(v, 4)
    for name, ct, bar in (('even', 'DIGITEVEN', None), ('odd', 'DIGITODD', None),
                          ('match0', 'DIGITMATCH', '0'), ('diff0', 'DIGITDIFF', '0')):
        v, err = quote(ws, sym, ct, bar)
        if v is None:
            out['errors'][name] = err
        else:
            out[name] = round(v, 4)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('symbols', nargs='*')
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--json-out', default='')
    a = ap.parse_args()

    syms = ALL if a.all else (a.symbols or ['JD100'])
    ws = DerivWS()
    results = []
    for sym in syms:
        s = snapshot(ws, sym)
        results.append(s)
        print('=' * 72)
        print(f"{sym}   EVEN={s['even']}  ODD={s['odd']}  MATCH0={s['match0']}"
              f"  DIFF0={s['diff0']}")
        print('  ' + '  '.join(f"O{b}={s['over'].get(b, '-')}"
                               for b in range(0, 9)))
        print('  ' + '  '.join(f"U{b}={s['under'].get(b, '-')}"
                               for b in range(1, 10)))
        if s['errors']:
            print(f"  errors: {s['errors']}")
    ws.close()
    if a.json_out:
        with open(a.json_out, 'w') as f:
            json.dump(results, f, indent=2)
        print(f"[*] wrote {a.json_out}")


if __name__ == '__main__':
    main()
