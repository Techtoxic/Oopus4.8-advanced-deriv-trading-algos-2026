"""jd100_counterfactual.py — how much edge did Deriv remove from JD100?

The scan result is a trap:

    symbol  sigma_pips  grid   OOS EV
    JD10       11.29    full   -3.36%
    JD25       11.04    full   -3.09%
    JD50       11.01    full   -3.70%
    JD75       11.26    full   -2.50%
    JD100       2.38    CUT    -9.45%

The lattice edge needs tick sigma in PIPS to be small, because that is what makes
the next digit sticky and P(d_{t+1} | d_t) non-uniform. The four Jump indices
that still carry the full, uncut grid all run at sigma ~11 pips — ten times too
wide for the lattice to bite. The one index whose sigma (2.38 pips) sits INSIDE
the measured edge zone (the walk-forward crossing was sigma 3.56) is precisely
the one Deriv restricted.

So: evaluate the SAME JD100 ticks under BOTH grids and measure the difference.
That isolates Deriv's repricing from the market's microstructure — the pure value
of the constraint.

Run: python3 jd100_counterfactual.py --ticks 60000
"""

import argparse
import json
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from deriv_api import DerivWS
from derivfetch import fetch_ticks
from endpoint_compare import grid
from jump_lattice_scan import derive_policy, replay, stats

INTACT = ({('OVER', k): v for k, v in
           {0: 1.09, 1: 1.23, 2: 1.40, 3: 1.63, 4: 1.95,
            5: 2.43, 6: 3.21, 7: 4.72, 8: 8.93}.items()})
INTACT.update({('UNDER', k): v for k, v in
               {1: 8.93, 2: 4.72, 3: 3.21, 4: 2.43, 5: 1.95,
                6: 1.63, 7: 1.40, 8: 1.23, 9: 1.09}.items()})


def evaluate(digits, payouts, label):
    mid = len(digits) // 2
    d_c, d_n = digits[:-1], digits[1:]
    pol_full = derive_policy(digits, payouts)
    ev_is, se_is, t_is = stats(replay(d_c, d_n, pol_full))
    pol_is = derive_policy(digits[:mid], payouts)
    for k, v in pol_full.items():
        pol_is.setdefault(k, v)
    oc, on = digits[mid:-1], digits[mid + 1:]
    pnl = replay(oc, on, pol_is)
    ev_o, se_o, t_o = stats(pnl)
    sh = np.random.permutation(digits)
    perm = float(np.mean(replay(sh[:-1], sh[1:], pol_is)))
    lag2 = float(np.mean(replay(digits[:-2], digits[2:], pol_is)))
    print(f"\n  [{label}]  contracts available: {len(payouts)}")
    print(f"    in-sample EV  {ev_is*100:+.2f}%  (t={t_is:.1f})")
    print(f"    OOS EV        {ev_o*100:+.2f}%  99% CI "
          f"[{(ev_o-2.58*se_o)*100:+.2f}%, {(ev_o+2.58*se_o)*100:+.2f}%]"
          f"  t={t_o:.1f}  n={len(pnl)}")
    print(f"    controls      lag2 {lag2*100:+.2f}%   permutation "
          f"{perm*100:+.2f}%")
    return {'label': label, 'n_contracts': len(payouts), 'is_ev': ev_is,
            'oos_ev': ev_o, 'oos_se': se_o, 'oos_t': t_o, 'n': len(pnl),
            'lag2': lag2, 'perm': perm,
            'policy': {int(k): {'contract': [v['contract'][0],
                                             v['contract'][1]],
                                'payout': v['contract'][2],
                                'p_win': v['p_win'], 'ev': v['ev']}
                       for k, v in pol_full.items()}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--symbol', default='JD100')
    ap.add_argument('--ticks', type=int, default=60000)
    ap.add_argument('--json-out', default='../results/jd100_counterfactual.json')
    a = ap.parse_args()

    wspub = DerivWS(token='')
    wsauth = DerivWS()
    gpub, epub = grid(wspub, a.symbol)
    gauth, eauth = grid(wsauth, a.symbol)
    wspub.close()

    def to_pay(g):
        out = {}
        for k, v in g.items():
            if k.startswith('DIGITOVER'):
                out[('OVER', int(k[9:]))] = v
            elif k.startswith('DIGITUNDER'):
                out[('UNDER', int(k[10:]))] = v
        return out

    pub_pay = to_pay(gpub)
    auth_pay = to_pay(gauth)

    print(f"{a.symbol}")
    print(f"  public tier        : {len(pub_pay)} digit contracts quoted")
    print(f"  authenticated tier : {len(auth_pay)} digit contracts quoted")
    print(f"  public-only rows   : {sorted(set(pub_pay) - set(auth_pay))}")
    print(f"  auth-only rows     : {sorted(set(auth_pay) - set(pub_pay))}")

    t, p, pip = fetch_ticks(wsauth, a.symbol, a.ticks, verbose=False)
    wsauth.close()
    dp = np.diff(p)
    normal = np.round(dp * (10 ** pip))
    normal = normal[np.abs(normal) < 20]
    sigma = float(np.std(normal))
    digits = (np.round(p * (10 ** pip)).astype(int)) % 10

    print(f"ticks={len(p)}  pip={pip}  sigma={sigma:.2f} pips")
    print("=" * 78)
    print("SAME TICKS, TWO GRIDS — isolating Deriv's repricing from microstructure")
    print("=" * 78)

    r_int = evaluate(digits, INTACT, 'INTACT grid (pre-repricing)')
    r_pub = evaluate(digits, pub_pay, 'PUBLIC tier grid (live quote)')
    r_aut = evaluate(digits, auth_pay, 'AUTH tier grid (what you can trade)')

    print()
    print("=" * 78)
    if r_pub['oos_ev'] > 0 or r_int['oos_ev'] > 0:
        best = max(r_int['oos_ev'], r_pub['oos_ev'])
        print(f"Would the edge be live on an intact JD100 book? "
              f"best OOS {best*100:+.2f}%")
    print(f"On the book you can actually trade OOS EV = "
          f"{r_aut['oos_ev']*100:+.2f}%  -> the index is untradeable as priced.")
    print("=" * 78)

    if a.json_out:
        os.makedirs(os.path.dirname(os.path.abspath(a.json_out)), exist_ok=True)
        with open(a.json_out, 'w') as f:
            json.dump({'symbol': a.symbol, 'sigma_pips': sigma,
                       'ticks': int(len(p)), 'intact': r_int,
                       'public': r_pub, 'auth': r_aut}, f, indent=2)
        print(f"[*] wrote {a.json_out}")


if __name__ == '__main__':
    main()
