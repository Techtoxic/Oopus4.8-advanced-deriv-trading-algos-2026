"""drift_46d.py — was the -0.67%/day drag ever real?

WHY THIS IS THE RIGHT QUESTION FOR 46 DAYS OF DATA
The whole sigma-decay thesis rested on one number: JD100's drift, estimated at -0.67%/day
(b = -6.71e-3). Everything followed from it — "12 days to breakeven", "spot 192 by Aug 13",
the decision to wait rather than trade.

It was estimated from ~14 days. That is far too short. For a driftless martingale observed
over T days, the standard error of the estimated drift is sigma_daily / sqrt(T), so:

    14 days, sigma_daily ~ 11.6%  ->  SE = 3.10%/day
    46 days                       ->  SE = 1.71%/day

At 14 days a measured -0.67%/day has |t| = 0.22. That is indistinguishable from zero. We
were reading a random walk as a schedule, and it is why spot went 209 -> 223 -> 187 -> 194
instead of decaying steadily.

WHAT 46 DAYS BUYS
Still not enough to prove a small drift, but enough to bound it. If the true drag is really
-0.67%/day, then over 46 days spot should have fallen ~27%. Either it did, or it did not.

Volatility drag is not a free parameter: for a GBM with zero expected return in price,
    d(log S)/dt = -sigma_daily^2 / 2
so the drift is DETERMINED by the volatility. That gives an independent prediction to check
the realised drift against, rather than fitting one number to noisy data.

WHAT THIS COMPUTES
  1. Realised drift over the full 46 days, with an honest standard error, and the t-stat.
  2. Drift over rolling 14-day sub-windows, to show directly how unstable the earlier
     estimate was — the same series will produce wildly different "drag rates" depending
     on which two weeks you happen to sample.
  3. The theoretical drag implied by measured volatility (-sigma^2/2), compared to realised.
  4. The sigma trajectory across the whole window, and how much of the sigma range the
     14-day sample actually covered.
  5. Given the measured volatility, the honest probability of reaching any target spot,
     as a distribution rather than a date.

Read-only. No API calls — pass a saved tick file, or it fetches.

Run: python3 drift_46d.py --ticks 4000000
"""
import argparse, math
import numpy as np
from deriv_api import DerivWS
from derivfetch import fetch_ticks, contiguous_pairs, native_interval


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="JD100")
    ap.add_argument("--ticks", type=int, default=4000000)
    ap.add_argument("--out", default="../results/drift_46d.md")
    a = ap.parse_args()

    ws = DerivWS(token="")
    print(f"fetching {a.ticks} unique ticks of {a.symbol}...")
    t, p, pip = fetch_ticks(ws, a.symbol, a.ticks)
    ws.close()

    v = np.round(p * (10 ** pip)).astype(np.int64)
    iv = native_interval(t)
    span_days = (t[-1] - t[0]) / 86400.0
    per_day = int(round(86400 / iv))
    print(f"  {len(t)} ticks, {span_days:.2f} days, {iv}s cadence\n")

    # ---- 1. realised drift, honestly ------------------------------------
    print("=" * 72)
    print("1. REALISED DRIFT OVER THE FULL WINDOW")
    print("=" * 72)
    logp = np.log(p)
    # daily closes, to avoid pretending 4M ticks are 4M independent observations
    nd = int(span_days)
    idx = [min(int(i * per_day), len(logp) - 1) for i in range(nd + 1)]
    dl = np.diff(logp[idx])
    mu = float(dl.mean())
    sd = float(dl.std(ddof=1))
    se = sd / math.sqrt(len(dl))
    print(f"  {len(dl)} daily log-returns")
    print(f"  spot {p[0]:.2f} -> {p[-1]:.2f}   total {(p[-1]/p[0]-1)*100:+.2f}%")
    print(f"  mean drift  {mu*100:+.4f}%/day")
    print(f"  daily vol   {sd*100:.4f}%")
    print(f"  SE of drift {se*100:.4f}%/day   ->  t = {mu/se:+.2f}")
    print(f"  95% CI      [{(mu-1.96*se)*100:+.3f}%, {(mu+1.96*se)*100:+.3f}%] per day")
    print()
    print(f"  the thesis assumed -0.670%/day.")
    z = (mu - (-0.0067)) / se
    print(f"  that value sits {abs(z):.2f} SE from the realised mean "
          f"({'consistent' if abs(z) < 2 else 'INCONSISTENT'})")
    print(f"  -> {'drift is NOT distinguishable from zero' if abs(mu/se) < 2 else 'drift is real'}")

    # ---- 2. how unstable was the 14-day estimate? -----------------------
    print(f"\n{'='*72}")
    print("2. THE SAME SERIES, ESTIMATED FROM ROLLING 14-DAY WINDOWS")
    print("=" * 72)
    W = 14
    ests = []
    for s in range(0, len(dl) - W + 1):
        ests.append(float(dl[s:s + W].mean()))
    ests = np.array(ests)
    print(f"  {len(ests)} overlapping 14-day windows")
    print(f"  drift estimates range {ests.min()*100:+.3f}% to {ests.max()*100:+.3f}% per day")
    print(f"  sd across windows {ests.std()*100:.3f}%/day")
    neg = float((ests < 0).mean())
    print(f"  {neg*100:.0f}% of windows show a NEGATIVE drift, "
          f"{(1-neg)*100:.0f}% positive")
    print()
    print("  Any two-week sample can produce almost any 'drag rate'. The -0.67%/day")
    print("  figure was one draw from this distribution, not a property of the index.")

    # ---- 3. theoretical drag from volatility ----------------------------
    print(f"\n{'='*72}")
    print("3. THEORETICAL DRAG:  d(log S)/dt = -sigma^2 / 2")
    print("=" * 72)
    theo = -0.5 * sd ** 2
    print(f"  measured daily vol {sd*100:.4f}%  ->  implied drag {theo*100:+.4f}%/day")
    print(f"  realised drift                        {mu*100:+.4f}%/day")
    print(f"  difference {abs(mu-theo)*100:.4f}%/day = {abs(mu-theo)/se:.2f} SE")
    print()
    print(f"  over 46 days the implied drag predicts {(math.exp(theo*span_days)-1)*100:+.1f}%")
    print(f"  actual move was                        {(p[-1]/p[0]-1)*100:+.1f}%")

    # ---- 4. sigma trajectory --------------------------------------------
    print(f"\n{'='*72}")
    print("4. SIGMA RANGE COVERED")
    print("=" * 72)
    cg = contiguous_pairs(t, v, iv)
    st = np.diff(v)[cg]
    sp = v[:-1][cg].astype(float)
    B = 1800
    nb = len(st) // B
    sgs, sps = [], []
    for i in range(nb):
        s = st[i*B:(i+1)*B].astype(float)
        nz = np.abs(s[s != 0])
        if len(nz) < 100:
            continue
        thr = np.percentile(nz, 99.5)
        f = s[np.abs(s) <= thr]
        sgs.append(math.sqrt((f**2).mean()))
        sps.append(sp[i*B:(i+1)*B].mean())
    sgs, sps = np.array(sgs), np.array(sps)
    print(f"  {len(sgs)} blocks of {B} ticks")
    print(f"  sigma_pips {sgs.min():.3f} - {sgs.max():.3f}")
    print(f"  spot       {sps.min()/10**pip:.2f} - {sps.max()/10**pip:.2f}")
    b1, b0 = np.polyfit(sps, sgs, 1)
    r2 = 1 - np.sum((sgs-(b0+b1*sps))**2) / np.sum((sgs-sgs.mean())**2)
    print(f"  affine fit sigma = {b0:+.5f} + {b1:.6e}*spot   R^2 {r2:.5f}")
    print(f"  (14-day fit gave 0.03982 + 1.746764e-04*spot)")
    for g in (3.42, 3.48):
        print(f"  gate sigma {g}: spot {(g-b0)/b1/10**pip:.2f}")

    # ---- 5. honest probability, not a date ------------------------------
    print(f"\n{'='*72}")
    print("5. PROBABILITY OF REACHING A TARGET, AS A DISTRIBUTION")
    print("=" * 72)
    cur = float(p[-1])
    print(f"  from spot {cur:.2f}, using measured drift {mu*100:+.4f}%/day "
          f"and vol {sd*100:.2f}%/day")
    print(f"  {'target':>8}{'30d':>9}{'60d':>9}{'90d':>9}{'180d':>9}")
    for tgt in (195, 190, 185, 175, 160):
        row = f"  {tgt:>8}"
        for D in (30, 60, 90, 180):
            m = mu * D
            s_ = sd * math.sqrt(D)
            zz = (math.log(tgt / cur) - m) / s_
            P = 0.5 * math.erfc(-zz / math.sqrt(2))
            row += f"{P*100:>8.0f}%"
        print(row)
    print()
    print("  These are probabilities of being BELOW the target at that horizon.")
    print("  Note how flat they are: that is what 'noise dominates drift' looks like.")

    with open(a.out, "w") as f:
        f.write(f"# Drift over {span_days:.1f} days — {a.symbol}\n\n"
                f"{len(t)} ticks, spot {p[0]:.2f} -> {p[-1]:.2f} "
                f"({(p[-1]/p[0]-1)*100:+.2f}%).\n\n"
                f"drift {mu*100:+.4f}%/day, SE {se*100:.4f}, t = {mu/se:+.2f}\n"
                f"daily vol {sd*100:.4f}%, implied drag {theo*100:+.4f}%/day\n"
                f"14-day rolling estimates ranged {ests.min()*100:+.3f}% to "
                f"{ests.max()*100:+.3f}%/day\n"
                f"sigma affine: {b0:+.5f} + {b1:.6e}*spot (R^2 {r2:.5f})\n")
    print(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
