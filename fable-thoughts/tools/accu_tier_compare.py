"""accu_tier_compare.py — ACCU barrier: public quote vs authenticated quote, right now.

WHY
ACCU_PHASE_LATTICE.md §6 recorded a PASS-B for the Crash accumulator phase
lattice on public-barrier data, then withdrew it the same day in the correction
immediately below:

    CRASH1000 4%:  2.3454e-6 public vs 2.28724e-6 authenticated
    CRASH500  4%:  4.7141e-6 public vs 4.598554e-6 authenticated
    45/85 cells tighter than public
    re-analysis: 0 tradable cells (A3), C1 = 0.98949 fails A5,
                 CRASH500 frozen-band replay -8.78%/trade, verdict INCONCLUSIVE

That is the same tier split found in the digit book, and it is worth re-checking
periodically because the barrier can be retuned at any time. This script quotes
every Boom/Crash growth rate on both tiers and prints the ratio, plus whether the
knockout lattice parameter w = b * spot / pip lands in a different integer band.

Read-only. Proposals only, no orders.

Run: python3 accu_tier_compare.py
     python3 accu_tier_compare.py --symbols CRASH1000 CRASH500 BOOM300N
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from deriv_api import DerivWS

SYMBOLS = ['CRASH1000', 'CRASH500', 'CRASH300N', 'BOOM1000', 'BOOM500',
           'BOOM300N', 'BOOM50', 'CRASH50', 'BOOM150N', 'CRASH150N']
RATES = [0.01, 0.02, 0.03, 0.04, 0.05]


def quote(ws, sym, rate, stake=1.0):
    r = ws.call({'proposal': 1, 'amount': stake, 'basis': 'stake',
                 'contract_type': 'ACCU', 'currency': 'USD',
                 'underlying_symbol': sym, 'growth_rate': rate})
    p = r.get('proposal')
    if not p:
        return None
    cd = p.get('contract_details', {}) or {}
    return {
        'b': cd.get('tick_size_barrier'),
        'max_ticks': cd.get('maximum_ticks'),
        'spot': p.get('spot'),
        'tsb_pct': cd.get('tick_size_barrier_percentage'),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--symbols', nargs='+', default=SYMBOLS)
    a = ap.parse_args()

    pub = DerivWS(token='')
    auth = DerivWS()
    acct = getattr(auth, 'account', {})
    print(f"authenticated session: {acct.get('account_id')} "
          f"type {acct.get('account_type')}")
    print()

    tighter = equal = looser = 0
    rows = []
    for sym in a.symbols:
        print(f"--- {sym} " + '-' * 52)
        for g in RATES:
            qp = quote(pub, sym, g)
            qa = quote(auth, sym, g)
            if qp is None and qa is None:
                continue
            bp = float(qp['b']) if qp and qp.get('b') else None
            ba = float(qa['b']) if qa and qa.get('b') else None
            spot = (qa or qp or {}).get('spot')
            if bp is None or ba is None:
                which = 'public only' if ba is None else 'auth only'
                print(f"  g={g:<4} public={bp} auth={ba}   ({which})")
                continue
            ratio = ba / bp
            tag = 'same'
            if ratio < 0.9999:
                tag = f'AUTH TIGHTER {(1-ratio)*100:.3f}%'
                tighter += 1
            elif ratio > 1.0001:
                tag = f'AUTH LOOSER {(ratio-1)*100:.3f}%'
                looser += 1
            else:
                equal += 1
            # knockout lattice parameter in pips (pip 0.001 for these symbols)
            wp = bp * float(spot) * 1000 if spot else None
            wa = ba * float(spot) * 1000 if spot else None
            band = ''
            if wp and wa:
                band = (f"  w: {wp:.3f} (K={int(wp)}) -> {wa:.3f} (K={int(wa)})"
                        + ('  BAND CHANGED' if int(wp) != int(wa) else ''))
            rows.append((sym, g, bp, ba, ratio, float(spot or 0), wp, wa))
            print(f"  g={g:<4} public={bp:.7g} auth={ba:.7g} "
                  f"ratio {ratio:.5f}  {tag}{band}")
    pub.close()
    auth.close()

    print()
    print('=' * 70)
    print(f"cells compared: {tighter + equal + looser}   "
          f"auth TIGHTER: {tighter}   equal: {equal}   looser: {looser}")
    if tighter:
        print("  => the tier split is live on the ACCU book too. Any ACCU edge")
        print("     measured on public quotes is not evidence; the authenticated")
        print("     barrier is the only one you can buy, and a tighter barrier")
        print("     moves w = b*spot/pip to a different integer K, which is")
        print("     precisely the parameter the phase lattice trades on.")
    else:
        print("  => both tiers agree today on these symbols and rates.")
    band_changes = [r for r in rows if r[6] and r[7] and int(r[6]) != int(r[7])]
    print(f"  cells where the integer K band changed: {len(band_changes)}")
    for r in band_changes:
        print(f"     {r[0]} g={r[1]}  K {int(r[6])} -> {int(r[7])}")


if __name__ == '__main__':
    main()
