"""edge2_accu_ladder.py — EDGE 2: the ACCU barrier ladder, re-priced on the
CORRECT (authenticated) tier.

WHY THIS EXISTS
The repo's phase-lattice thesis died for the wrong reason in one place and the
right reason in another. The pre-registration assumed the PUBLIC barrier quotes
(quoted with no token at all) were the executable book. They are not:
`accu_tier_compare.py` on 2026-09-27 measured the authenticated barrier 1.3-5.9%
tighter on 13/20 Crash/Boom cells, flipping the integer K band on 7 of them
(CRASH1000 4% K 14->13), which is the entire basis of the withdrawal.
BOOM1000, the symbol with no lattice surplus, is identical on both tiers.

But that comparison was done on 2026-09-27 spot. The ladder parameter is
w = b * spot / pip, and b itself differs per tier. Both tiers move with spot.
The correct re-test is: recompute w on TODAY's spot with TODAY's authenticated
barrier, and ask whether ANY eligible phase band is in band right now.

Read-only. Proposals only, no orders.

Run: python3 edge2_accu_ladder.py                        # full Crash/Boom ladder
     python3 edge2_accu_ladder.py --symbols CRASH1000 CRASH500 BOOM300N
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from deriv_api import DerivWS

SYMS = ['CRASH1000', 'CRASH500', 'CRASH300N', 'CRASH600', 'CRASH900',
        'BOOM1000', 'BOOM500', 'BOOM300N', 'BOOM600', 'BOOM900',
        'CRASH50', 'BOOM50', 'CRASH150N', 'BOOM150N']
RATES = [0.01, 0.02, 0.03, 0.04, 0.05]
# pip scales per symbol class: Crash/Boom 500/1000 are 3dp (pip 0.01),
# the N-series are 4dp. w = b * spot_in_pips with integer-pip prices.
DEC_PIP = {'CRASH1000': 2, 'CRASH500': 2, 'BOOM1000': 2, 'BOOM500': 2,
           'CRASH600': 2, 'CRASH900': 2, 'BOOM600': 2, 'BOOM900': 2}

ELIGIBLE = (0.05, 0.125)   # the phase band from ACCU_PHASE_LATTICE.md S2
KCAP = {0.01: 99, 0.02: 40, 0.03: 22, 0.04: 16, 0.05: 12}  # model K*(g)


def quote_accu(ws, sym, rate, stake=1.0):
    r = ws.call({'proposal': 1, 'amount': stake, 'basis': 'stake',
                 'contract_type': 'ACCU', 'currency': 'USD',
                 'underlying_symbol': sym, 'growth_rate': rate})
    p = r.get('proposal')
    if not p:
        return None
    cd = p.get('contract_details', {}) or {}
    return {'b': cd.get('tick_size_barrier'),
            'max_ticks': cd.get('maximum_ticks'),
            'spot': p.get('spot')}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--symbols', nargs='+', default=SYMS)
    ap.add_argument('--json-out', default='../results/edge2_accu_ladder.json')
    a = ap.parse_args()

    auth = DerivWS()
    acct = getattr(auth, 'account', {}) or {}
    print(f"[*] session {acct.get('account_id')} type {acct.get('account_type')}")

    rows = []
    for sym in a.symbols:
        for g in RATES:
            q = quote_accu(auth, sym, g)
            if not q or not q.get('b') or not q.get('spot'):
                continue
            b = float(q['b'])
            spot = float(q['spot'])
            # repo rule (accu_phase_lib.barrier_levels): w = b * P_prev with P in
            # integer pips, so round the quoted spot to the symbol's pip first.
            pips = 10 ** DEC_PIP.get(sym, 4)
            w = b * round(spot * pips)
            K = int(w)
            phi = w - K
            inband = ELIGIBLE[0] <= phi < ELIGIBLE[1] and K <= KCAP.get(g, 99)
            rows.append({'sym': sym, 'g': g, 'b': b, 'spot': spot, 'w': w,
                         'K': K, 'phi': phi,
                         'max_ticks': q.get('max_ticks'), 'eligible': inband})
    auth.close()

    print(f"\n{'sym':<10}{'g':>5}{'b':>13}{'spot':>12}{'w':>8}{'K':>4}"
          f"{'phi':>8}  flag")
    hits = [r for r in rows if r['eligible']]
    for r in rows:
        flag = '  <-- ELIGIBLE' if r['eligible'] else ''
        print(f"{r['sym']:<10}{r['g']:>5.2f}{r['b']:>13.7g}{r['spot']:>12.4f}"
              f"{r['w']:>8.3f}{r['K']:>4}{r['phi']:>8.4f}{flag}")
    print()
    print(f"quoted cells: {len(rows)}   ELIGIBLE today: {len(hits)}")
    for r in hits:
        print(f"   {r['sym']} g={r['g']:.2f}  K={r['K']} phi={r['phi']:.4f}")
    if a.json_out:
        with open(a.json_out, 'w') as f:
            json.dump(rows, f, indent=2)
        print(f"[*] wrote {a.json_out}")


if __name__ == '__main__':
    main()
