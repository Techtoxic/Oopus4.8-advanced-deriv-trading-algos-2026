"""universe_scan.py — enumerate EVERYTHING Deriv offers, so the hunt stops
missing markets.

WHAT WE KNOW (2026-09-27, all verified live on the authenticated tier)
- Digit OVER/UNDER on JD100: restricted ladder (14 contracts, best OOS cell -4.6%).
- Digit on JD10/25/50/75 + all 10 vol indices: full grid, but sigma ~11 pips, no
  lattice, OOS -2.5% to -3.7%.
- ACCU barriers on Crash/Boom: authenticated tier 1.3-5.9% tighter, K band flipped
  on 7 cells. 0 tradable cells.
- Step indices: reject barrier offsets (NEXT_TESTS T4). Rise/Fall: zero drift, no
  autocorrelation (FINDINGS.md S3).

WHAT THIS MAPS
For every active synthetic symbol: which contract families Deriv lists
(contracts_for), digit-capability, and pip size. Anything digit-capable outside
the 15 scanned indices, or any contract family the repo never priced (Touch,
Asian, Reset, Ends, HighLow, CallSpread/Turbo, Multiplier commission), becomes a
candidate for an authenticated EV scan.

Read-only. No orders.

Run: python3 universe_scan.py
     python3 universe_scan.py --json-out ../results/universe.json
"""

import argparse
import json
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from deriv_api import DerivWS

DIGIT_FAMILY = {'DIGITMATCH', 'DIGITDIFF', 'DIGITOVER', 'DIGITUNDER',
                'DIGITEVEN', 'DIGITODD'}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--json-out', default='../results/universe.json')
    ap.add_argument('--detail', action='store_true',
                    help='also probe contracts_for per symbol (slower, not default)')
    a = ap.parse_args()

    ws = DerivWS(token='')
    r = ws.call({'active_symbols': 'brief'})
    syms = r.get('active_symbols', [])
    print(f"active symbols: {len(syms)}")
    print()

    rows = []
    for s in syms:
        if s.get('market') != 'synthetic_index' or s.get('is_trading_suspended'):
            continue
        rows.append({
            'symbol': s.get('underlying_symbol'),
            'display': s.get('underlying_symbol_name'),
            'submarket': s.get('submarket'),
            'pip': s.get('pip_size'),
            'spot_proxy': None,
            'digit_contracts': [],
            'other_contracts': [],
        })
    rows.sort(key=lambda r: (r['submarket'] or '', r['symbol'] or ''))

    sub = {}
    for r in rows:
        sub.setdefault(r['submarket'], []).append(r['symbol'])
    for k in sorted(sub):
        print(f"  submarket {k}: {len(sub[k])} symbols")
        print(f"    {' '.join(sub[k])}")

    if a.detail:
        print()
        for r in rows:
            try:
                cf = ws.call({'contracts_for': r['symbol']})
            except Exception as e:
                print(f"  ! {r['symbol']}: {type(e).__name__}: {e}")
                continue
            avail = (cf.get('contracts_for', {}) or {}).get('available', [])
            fams = sorted({x.get('contract_type') for x in avail
                           if x.get('contract_type')})
            r['digit_contracts'] = sorted(DIGIT_FAMILY & set(fams))
            r['other_contracts'] = sorted(set(fams) - DIGIT_FAMILY)
            r['n_digit'] = len(r['digit_contracts'])
            print(f"  {r['symbol']:<12} digit={r['n_digit']} "
                  f"{','.join(r['other_contracts'])}")
    ws.close()

    known = {'JD10', 'JD25', 'JD50', 'JD75', 'JD100', 'R_10', 'R_25', 'R_50',
             'R_75', 'R_100', '1HZ10V', '1HZ25V', '1HZ50V', '1HZ75V', '1HZ100V'}
    if a.detail:
        new = [r['symbol'] for r in rows
               if r.get('n_digit') and r['symbol'] not in known]
        print()
        print(f"NEW digit-capable symbols never scanned: {new or 'none'}")
    if a.json_out:
        os.makedirs(os.path.dirname(os.path.abspath(a.json_out)) or '.',
                    exist_ok=True)
        with open(a.json_out, 'w') as f:
            json.dump(rows, f, indent=2)
        print(f"[*] wrote {a.json_out}")


if __name__ == '__main__':
    main()
