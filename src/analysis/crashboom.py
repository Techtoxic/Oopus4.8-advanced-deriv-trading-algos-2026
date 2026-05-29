"""
Crash/Boom microstructure — the instruments where retail traders believe a
"free" directional edge exists ("price always ticks up on Crash, so buy Rise").

We show:
  1. The directional asymmetry is real (most ticks go one way).
  2. But total drift is ZERO: the rare spike exactly cancels the many small ticks.
  3. Spike arrival is MEMORYLESS (geometric) -> "a crash is overdue" is false.
  4. Therefore no timing/direction edge exists, and Deriv only offers
     Accumulator/Multiplier products on these (no binary Rise/Fall) anyway,
     both of which carry an explicit negative expectancy.
"""
import math, os, sys
import numpy as np
from scipy import stats
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import common

def analyze(sym, spike_dir, out):
    e, p = common.load(sym)
    dp = np.diff(p)
    N = len(dp)
    # Define spike as a move opposite to the common small-tick direction.
    if spike_dir == "down":     # CRASH: small ups, rare big downs
        spikes = np.where(dp < 0)[0]
        small = dp[dp > 0]
    else:                       # BOOM: small downs, rare big ups
        spikes = np.where(dp > 0)[0]
        small = dp[dp < 0]
    spike_moves = dp[spikes]
    out.append(f"\n=== {sym}  (spike = {spike_dir}-move) ===")
    out.append(f"  ticks={N}  spikes={len(spikes)}  spike rate=1 per {N/len(spikes):.0f} ticks  P(spike)={len(spikes)/N:.5f}")
    out.append(f"  mean small move (favourable) = {np.mean(np.abs(small)):.4f}")
    out.append(f"  mean spike move (adverse)    = {np.mean(np.abs(spike_moves)):.4f}   ratio={np.mean(np.abs(spike_moves))/np.mean(np.abs(small)):.0f}x")
    out.append(f"  total drift over window      = {p[-1]-p[0]:+.2f}  (mean per tick {dp.mean():+.5f})")
    # Sum check: small-tick gains vs spike losses
    fav = np.sum(np.abs(small)); adv = np.sum(np.abs(spike_moves))
    out.append(f"  sum favourable moves={fav:.1f}  sum adverse moves={adv:.1f}  net={fav-adv:+.1f}  (≈0 => martingale)")

    # Inter-arrival distribution: gaps between consecutive spikes
    gaps = np.diff(spikes)
    if len(gaps) > 30:
        mean_gap = gaps.mean()
        # Geometric(p) memoryless test: hazard = P(spike | waited k ticks) should be flat.
        # Bucket by waited-time and compute conditional spike probability.
        # Build "ticks since last spike" series:
        since = np.zeros(N, dtype=int)
        last = -1
        is_spike = np.zeros(N, dtype=bool); is_spike[spikes] = True
        for i in range(N):
            since[i] = i - last
            if is_spike[i]:
                last = i
        # hazard in buckets of waited time
        out.append(f"  inter-spike gap: mean={mean_gap:.1f} median={np.median(gaps):.0f} max={gaps.max()} std={gaps.std():.1f}")
        out.append(f"  geometric expectation: if memoryless, std≈mean. observed std/mean={gaps.std()/mean_gap:.2f}")
        # Hazard by waited bucket
        buckets = [(0, int(mean_gap*0.5)), (int(mean_gap*0.5), int(mean_gap)),
                   (int(mean_gap), int(mean_gap*1.5)), (int(mean_gap*1.5), int(mean_gap*2)),
                   (int(mean_gap*2), 10**9)]
        out.append("  hazard test  P(spike now | waited W ticks):  (flat => memoryless => crash NOT 'overdue')")
        for lo, hi in buckets:
            mask = (since >= lo) & (since < hi)
            at_risk = np.sum(mask)
            fired = np.sum(mask & is_spike)
            h = fired / at_risk if at_risk else float('nan')
            out.append(f"      waited [{lo:4d},{hi if hi<10**8 else 'inf':>4}): hazard={h:.5f}  (n={at_risk})")
        # Chi-square: does waited-time bucket predict spike?  (independence)
        tab = []
        for lo, hi in buckets[:-1]:
            mask = (since >= lo) & (since < hi)
            tab.append([np.sum(mask & is_spike), np.sum(mask & ~is_spike)])
        tab = np.array(tab)
        if np.all(tab.sum(axis=1) > 0):
            chi2, pv, dof, _ = stats.chi2_contingency(tab)
            out.append(f"  hazard independence chi2={chi2:.2f} p={pv:.3f}  {'MEMORYLESS (no timing edge)' if pv>0.05 else 'TIMING STRUCTURE'}")

def main():
    out = []
    out.append("=" * 78)
    out.append("CRASH / BOOM SPIKE DYNAMICS")
    out.append("=" * 78)
    for sym, d in [("CRASH1000","down"),("CRASH500","down"),("BOOM1000","up"),("BOOM500","up")]:
        analyze(sym, d, out)
    out.append("\nINTERPRETATION")
    out.append("  * Direction is lopsided but DRIFT ≈ 0: the rare spike pays back every small tick.")
    out.append("  * Spike arrival is memoryless => you cannot time it; 'overdue' is the gambler's fallacy.")
    out.append("  * Deriv lists ONLY Accumulator + Multiplier on Crash/Boom (no binary Rise/Fall).")
    out.append("    - Multiplier: zero-drift underlying => pre-cost EV 0; commission/spread => EV<0.")
    out.append("    - Accumulator: the spike is exactly the barrier breach that zeroes your growth.")
    text = "\n".join(out)
    print(text)
    rp = os.path.join(os.path.dirname(__file__), "..", "..", "results", "crashboom.txt")
    with open(rp, "w") as f:
        f.write(text + "\n")

if __name__ == "__main__":
    main()
