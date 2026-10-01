"""jump_lattice_scan.py — does the digit lattice edge survive on the UNPRICED Jump indices?

WHY THIS EXISTS
grid_snapshot.py found that Deriv's digit book is cut on JD100 ONLY:

    JD100   OVER4=1.33  OVER8=2.86  MATCH0=2.86   (OVER0/1, UNDER8/9, DIFF0 delisted)
    JD10    OVER4=1.95  OVER8=8.93  MATCH0=8.93
    JD25    OVER4=1.95  OVER8=8.93  MATCH0=8.93
    JD50    OVER4=1.95  OVER8=8.93  MATCH0=8.93
    JD75    OVER4=1.95  OVER8=8.93  MATCH0=8.93
    1HZ100V OVER4=1.92  OVER8=8.33  MATCH0=8.33   (all vol indices intact)

And payout_probe.py showed the proposal endpoint does NOT lie: on JD100, six
same-tick pairs gave executed/proposal ratio exactly 1.0000 (8/8 matched). The
"PROPOSAL LIES" rows in results/payout_audit.md compared a proposal from tick t
against a fill from tick t+1 across a repricing in flight, and mislabelled it.

So the JD100 edge did not die because of a lying endpoint. It died because
Deriv re-priced ONE symbol. The sibling Jump indices were left alone.

THE TEST
The lattice edge is drift-vs-pip: with tick sigma in pips far below 1 the digit
is sticky and P(d_{t+1} | d_t) is strongly non-uniform, which is what the +25%
policy exploited. JD100 has sigma ~1-2 pips. The other Jump indices may not.
So for each Jump index: quote the live grid, fetch real ticks, measure sigma,
derive the optimal policy from the quoted grid, and validate 50/50 out of
sample with lag-2 and permutation controls.

Read-only apart from the quotes (no buys). ~50k ticks per symbol.

Run: python3 jump_lattice_scan.py --ticks 50000
"""

import argparse
import json
import math
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from deriv_api import DerivWS
from derivfetch import fetch_ticks
from grid_snapshot import snapshot

JUMP = ['JD10', 'JD25', 'JD50', 'JD75', 'JD100']


def grid_to_payouts(s):
    """Quoted proposal grid -> {(ctype, barrier): multiplier}."""
    out = {}
    for b, v in s['over'].items():
        out[('OVER', int(b))] = float(v)
    for b, v in s['under'].items():
        out[('UNDER', int(b))] = float(v)
    return out
def digit_wins(d_curr, d_next, ctype, barrier):
    """Vectorised win test for a digit OVER/UNDER contract."""
    if ctype == 'OVER':
        return d_next > barrier
    return d_next < barrier




def derive_policy(digits, payouts):
    """Per entry digit, pick the max-EV contract from the QUOTED grid."""
    d_curr = digits[:-1]
    d_next = digits[1:]
    policy = {}
    for entry in range(10):
        mask = (d_curr == entry)
        if not np.any(mask):
            continue
        nd = d_next[mask]
        best = None
        for (ctype, bar), pay in payouts.items():
            win = digit_wins(d_curr[mask], nd, ctype, bar)
            p = float(np.mean(win))
            ev = p * pay - 1.0
            if best is None or ev > best['ev']:
                best = {'contract': (ctype, bar, pay), 'p_win': p, 'ev': ev,
                        'n': int(np.sum(mask))}
        policy[entry] = best
    return policy


def replay(d_curr, d_next, policy):
    """Return per-trade PnL array for a policy applied to consecutive ticks."""
    pnl = np.empty(len(d_curr), dtype=float)
    for i in range(len(d_curr)):
        ctype, bar, pay = policy[int(d_curr[i])]['contract']
        win = digit_wins(np.array([d_curr[i]]), np.array([d_next[i]]),
                         ctype, bar)[0]
        pnl[i] = (pay - 1.0) if win else -1.0
    return pnl


def stats(pnl):
    m = float(np.mean(pnl))
    se = float(np.std(pnl)) / math.sqrt(len(pnl)) if len(pnl) > 1 else 0.0
    return m, se, (m / se if se > 0 else 0.0)


def scan_symbol(ws, sym, ticks, do_oos=True):
    s = snapshot(ws, sym)
    payouts = grid_to_payouts(s)
    t, p, pip = fetch_ticks(ws, sym, ticks, verbose=False)
    dp = np.diff(p)
    pips = np.round(dp * (10 ** pip))
    normal = pips[np.abs(pips) < 20]
    sigma = float(np.std(normal))
    digits = (np.round(p * (10 ** pip)).astype(int)) % 10

    d_curr = digits[:-1]
    d_next = digits[1:]
    policy = derive_policy(digits, payouts)
    pnl_is = replay(d_curr, d_next, policy)
    ev_is, se_is, t_is = stats(pnl_is)

    res = {
        'symbol': sym, 'ticks': int(len(p)), 'pip': pip, 'sigma_pips': sigma,
        'n_contracts': len(payouts), 'grid': {f'{k[0]}{k[1]}': v
                                             for k, v in payouts.items()},
        'policy': {int(k): {'contract': [v['contract'][0], v['contract'][1]],
                            'payout': v['contract'][2], 'p_win': v['p_win'],
                            'ev': v['ev'], 'n': v['n']}
                   for k, v in policy.items()},
        'mean_ev': ev_is, 'se': se_is, 't': t_is,
    }

    if do_oos:
        mid = len(digits) // 2
        pol_is = derive_policy(digits[:mid], payouts)
        # fill any digit unseen in-sample with the full-sample choice
        for k, v in policy.items():
            pol_is.setdefault(k, v)
        oos_c = digits[mid:-1]
        oos_n = digits[mid + 1:]
        pnl_oos = replay(oos_c, oos_n, pol_is)
        but_lag2_curr = digits[:-2]
        but_lag2_next = digits[2:]
        pnl_lag2 = replay(but_lag2_curr, but_lag2_next, pol_is)
        sh = np.random.permutation(digits)
        pnl_shuff = replay(sh[:-1], sh[1:], pol_is)
        ev_o, se_o, t_o = stats(pnl_oos)
        res['oos'] = {
            'n': int(len(pnl_oos)),
            'mean_ev': ev_o, 'se': se_o, 't': t_o,
            'ci99': [ev_o - 2.58 * se_o, ev_o + 2.58 * se_o],
            'lag2_ev': float(np.mean(pnl_lag2)),
            'perm_ev': float(np.mean(pnl_shuff)),
        }
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--symbols', nargs='*', default=JUMP)
    ap.add_argument('--ticks', type=int, default=50000)
    ap.add_argument('--json-out', default='../results/jump_lattice_scan.json')
    a = ap.parse_args()

    ws = DerivWS()
    results = []
    print('=' * 88)
    print('JUMP INDEX LATTICE SCAN — live quoted grid vs empirical digit lattice')
    print('=' * 88)
    for sym in a.symbols:
        print(f"\n[*] {sym}: quoting grid then fetching {a.ticks} ticks...")
        try:
            r = scan_symbol(ws, sym, a.ticks)
        except Exception as e:
            print(f"    FAILED: {type(e).__name__}: {e}")
            continue
        results.append(r)
        o = r.get('oos')
        print(f"    ticks={r['ticks']}  pip={r['pip']}  sigma={r['sigma_pips']:.2f}"
              f" pips  grid_contracts={r['n_contracts']}")
        print(f"    in-sample  EV = {r['mean_ev']*100:+.2f}%  (t={r['t']:.1f})")
        if o:
            print(f"    OOS        EV = {o['mean_ev']*100:+.2f}%"
                  f"  99% CI [{o['ci99'][0]*100:+.2f}%, {o['ci99'][1]*100:+.2f}%]"
                  f"  t={o['t']:.1f}  n={o['n']}")
            print(f"    controls   lag2 = {o['lag2_ev']*100:+.2f}%   "
                  f"permutation = {o['perm_ev']*100:+.2f}%")
        print('    ' + '-' * 70)
        for d in sorted(r['policy']):
            v = r['policy'][d]
            print(f"      digit {d}: {v['contract'][0]:<5} {v['contract'][1]:<2}"
                  f" pay={v['payout']:.4f}  p={v['p_win']*100:5.2f}%"
                  f"  n={v['n']:>6}  EV={v['ev']*100:+.2f}%")
        time.sleep(0.3)
    ws.close()

    if a.json_out:
        os.makedirs(os.path.dirname(os.path.abspath(a.json_out)), exist_ok=True)
        with open(a.json_out, 'w') as f:
            json.dump(results, f, indent=2)
        print(f"\n[*] wrote {a.json_out}")

    print('\n' + '=' * 88)
    print('SUMMARY — OOS EV under the LIVE quoted grid')
    print('=' * 88)
    print(f"{'symbol':<8}{'sigma':>8}{'grid':>7}{'IS EV':>10}{'OOS EV':>10}"
          f"{'OOS t':>8}  verdict")
    for r in results:
        o = r.get('oos')
        if not o:
            continue
        grid = 'CUT' if r['n_contracts'] < 18 else 'full'
        verdict = ('POSITIVE EDGE' if o['ci99'][0] > 0 else
                   ('negative' if o['ci99'][1] < 0 else 'inconclusive'))
        print(f"{r['symbol']:<8}{r['sigma_pips']:>8.2f}{grid:>7}"
              f"{r['mean_ev']*100:>9.2f}%{o['mean_ev']*100:>9.2f}%"
              f"{o['t']:>8.1f}  {verdict}")


if __name__ == '__main__':
    main()
