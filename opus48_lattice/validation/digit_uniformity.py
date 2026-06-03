"""
digit_uniformity.py — is any index's digit distribution non-uniform AND stable
AND big enough to beat the house edge?

A one-off chi-square rejection is not an edge: it must (a) be statistically
real, (b) be STABLE out-of-sample (reproduce on an independent half), and (c)
exceed the contract's house edge after using MEASURED live payouts. We check all
three:

  1. chi-square uniformity on the full 24h sample.
  2. split-half stability: per-digit frequency deviation in first vs second half;
     a real bias has correlated deviations across halves (we report Pearson r and
     the sign-agreement count). Noise has r ~ 0.
  3. measured EV: empirical win rate x live payout - 1 for every contract; the
     single best (least negative) contract per index, and whether ANY is > 0.
"""
import os, sys, math
import numpy as np
from scipy import stats
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lattice import common


def best_contract_ev(dig, payouts, symbol):
    best = None
    for (kind, barrier) in common.contract_universe():
        po = common.payout_lookup(payouts, symbol, kind, barrier)
        if po is None:
            continue
        wins = np.mean([common.resolves_win(kind, barrier, d) for d in dig])
        ev = wins * po - 1.0
        if best is None or ev > best[3]:
            best = (kind, barrier, wins, ev, po)
    return best


def run(symbol, payouts, out_lines):
    e, pr = common.load(symbol)
    dig, places = common.last_digit(pr)
    n = len(dig)
    p = out_lines.append
    counts = np.bincount(dig, minlength=10)
    chi2, pv = stats.chisquare(counts)
    # split-half
    half = n // 2
    c1 = np.bincount(dig[:half], minlength=10) / half
    c2 = np.bincount(dig[half:], minlength=10) / (n - half)
    dev1 = c1 - 0.1; dev2 = c2 - 0.1
    r = np.corrcoef(dev1, dev2)[0, 1]
    sign_agree = int(np.sum(np.sign(dev1) == np.sign(dev2)))
    best = best_contract_ev(dig, payouts, symbol)
    p(f"\n=== {symbol} [{places}dp, n={n}] ===")
    p(f"  counts={counts.tolist()}")
    p(f"  chi2 uniform: chi2={chi2:.2f} df=9 p={pv:.3f}  "
      f"{'NON-UNIFORM' if pv < 0.05 else 'uniform OK'}")
    p(f"  split-half stability: Pearson r(dev1,dev2)={r:+.3f}  "
      f"sign-agree={sign_agree}/10  {'STABLE bias' if r > 0.5 else 'noise (unstable)'}")
    if best:
        k, b, wr, ev, po = best
        p(f"  best contract by MEASURED EV: {k}{'' if b is None else ' '+str(b)}  "
          f"emp_win={wr:.4f} payout={po} EV/$={ev:+.4f}  "
          f"{'POSITIVE EV' if ev > 0 else 'negative'}")
    return (pv < 0.05 and r > 0.5), (best[3] > 0 if best else False)


def main():
    payouts = common.load_payouts()
    syms = ["1HZ100V", "1HZ10V", "1HZ75V", "1HZ25V", "R_100", "R_10", "JD100"]
    L = ["=" * 70, "DIGIT UNIFORMITY + SPLIT-HALF STABILITY + MEASURED EV", "=" * 70]
    stable_bias = pos_ev = False
    for s in syms:
        try:
            sb, pe = run(s, payouts, L)
            stable_bias |= sb; pos_ev |= pe
        except Exception as ex:
            L.append(f"\n{s}: failed ({ex})")
    L.append("\n" + "=" * 70)
    L.append(f"OVERALL: stable exploitable bias = {stable_bias}; "
             f"any positive-EV contract = {pos_ev}")
    text = "\n".join(L); print(text)
    rp = os.path.join(os.path.dirname(__file__), "..", "results", "digit_uniformity.txt")
    os.makedirs(os.path.dirname(rp), exist_ok=True)
    open(rp, "w").write(text + "\n")


if __name__ == "__main__":
    main()
