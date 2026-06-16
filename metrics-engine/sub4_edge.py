"""sub4_edge.py — measure the genuine sub-4.0 digit structure from the independent
monitor ticks (large N, no bot selection). Answers: is there a real edge below the
4.0 table floor, on which contracts, and how big after the payout grid?
"""
import csv, os, sys, math
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "fable-thoughts", "tools"))
from sigma_model import winset, CONTRACTS, GRID  # noqa

rows = [r for r in csv.DictReader(open(os.path.join(HERE, "live", "sigma_ticks.csv")))
        if r["digit"] and r["sigma"]]
dig = np.array([int(r["digit"]) for r in rows])
sig = np.array([float(r["sigma"]) for r in rows])
nxt, cur, s = dig[1:], dig[:-1], sig[:-1]
off = (nxt - cur) % 10

def boot_ci(x, n=2000):
    x = np.asarray(x, float)
    rng = np.random.default_rng(7)
    b = rng.choice(x, size=(n, len(x)), replace=True).mean(axis=1)
    return float(np.percentile(b, 2.5)), float(np.percentile(b, 97.5))

print(f"monitor ticks usable: {len(rows)}  sigma {sig.min():.2f}-{sig.max():.2f}\n")

for lo, hi in [(3.5, 4.0)]:
    m = (s >= lo) & (s < hi)
    o = off[m]; c = cur[m]; nx = nxt[m]; n = int(m.sum())
    pmf = np.array([(o == k).mean() for k in range(10)])
    print(f"=== sigma {lo}-{hi}  (n={n}) ===")
    print("offset PMF (0=repeat):", [round(x, 4) for x in pmf])
    print("  peaked at 0,+/-1? offsets 0,1,9 =", round(pmf[0], 4), round(pmf[1], 4), round(pmf[9], 4),
          " vs far 4,5,6 =", round(pmf[4], 4), round(pmf[5], 4), round(pmf[6], 4))

    # realised EV of every contract, conditioned on current digit, using actual next digit
    print("\n  realised EV per contract type (best barrier shown), large-N, real grid:")
    best_by_type = {}
    for (t, bar) in CONTRACTS:
        ws = winset(t, bar)
        payout = GRID[(t, bar)]
        # realised win indicator for betting this fixed (t,bar) every eligible tick
        win = np.array([1.0 if nn in ws else 0.0 for nn in nx])
        pnl = win * (payout - 1) - (1 - win)  # stake=1
        ev = pnl.mean()
        key = t
        if key not in best_by_type or ev > best_by_type[key][0]:
            best_by_type[key] = (ev, bar, win.mean(), payout, pnl)
    for t, (ev, bar, wr, payout, pnl) in sorted(best_by_type.items(), key=lambda kv: -kv[1][0]):
        lo_ci, hi_ci = boot_ci(pnl)
        star = "  <-- +EV CI>0" if lo_ci > 0 else ""
        print(f"    {t:11}{('' if bar is None else bar)!s:>3}  EV={ev*100:+6.2f}%/trade  win={wr:.4f} "
              f"payout={payout:.3f}  CI95=[{lo_ci*100:+.2f},{hi_ci*100:+.2f}]%{star}")

    # the specific exploit: DIGITMATCH on the *current* digit each tick
    matchcur = (nx == c).astype(float)
    pnl_mc = matchcur * (GRID[("DIGITMATCH", 0)] - 1) - (1 - matchcur)
    lo_ci, hi_ci = boot_ci(pnl_mc)
    print(f"\n  STRATEGY 'DIGITMATCH current digit every tick': hit={matchcur.mean():.4f} "
          f"(breakeven .1120)  EV={pnl_mc.mean()*100:+.2f}%/trade  CI95=[{lo_ci*100:+.2f},{hi_ci*100:+.2f}]%  n={n}")
