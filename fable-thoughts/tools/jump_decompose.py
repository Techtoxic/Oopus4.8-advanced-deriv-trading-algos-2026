"""jump_decompose.py — is the +0.511 intercept a JUMP component, and is it predictable?

THE INSIGHT THIS TESTS (credit: the HMM suggestion, second round)
sigma_from_spot.py found sigma_pips = 0.51126 + 1.543886e-04 * spot, fitting at 99.8% of
the noise ceiling. The +0.511 intercept was unexplained: pure geometric Brownian motion
predicts zero, and rounding accounts for only ~0.01.

The proposal: this is Merton jump-diffusion, and textbook treatments assume PROPORTIONAL
(log-normal) jumps which scale with price and leave a zero intercept. If Deriv's jump
component has its own ABSOLUTE scale, you get exactly a constant floor. That is a real
physical explanation for the anomaly, and it was the right diagnosis.

WHY IT MATTERS FAR MORE THAN AN ESTIMATOR IMPROVEMENT
Independent components add in VARIANCE, not in standard deviation. So jump-diffusion
predicts

    sigma^2 = sigma_jump^2 + (b * spot)^2        NOT   sigma = a + b * spot

The affine fit we used has no physics behind it — it just happened to fit. At spot 207.84
the two forms give 3.7201 vs 3.2493, easily distinguishable.

And if the jump really is a separate additive component, then on a tick where NO jump occurs
the volatility is the diffusion part alone:

    total sigma      3.7201  ->  EV -1.12%
    diffusion only   3.2088  ->  EV +2.49%     <-- POSITIVE AT TODAY'S SPOT

So if jump ticks can be predicted, trading only non-jump ticks puts the edge live now,
with no waiting for spot at all.

THE CATCH: you must know BEFORE the tick whether it will be a jump. Poisson jumps are
memoryless and unpredictable. But Deriv documents jumps at roughly 20-minute intervals — if
the timing is periodic or even partially regular, it is predictable. Nobody has checked.

WHAT THIS MEASURES
  1. WHICH DECOMPOSITION FITS. Compare sigma = a + b*S against sigma^2 = c + (b*S)^2 on
     clean ticks across the widest available spot range. Variance-additive winning is
     direct evidence for a genuine jump component.
  2. JUMP IDENTIFICATION. The step distribution should be a mixture: a narrow diffusion
     core plus a heavy tail. Fit the mixture and get the jump rate and jump size.
  3. ARE JUMPS PREDICTABLE? Inter-jump interval distribution, autocorrelation of jump
     indicators, and a test against the exponential (memoryless) null. A periodic component
     shows as a spike in the interval histogram and in the FFT of the jump indicator.
  4. WHAT IS THE EDGE ON NON-JUMP TICKS? Measure the digit concentration conditional on the
     next tick not being a jump, which is the upper bound on what perfect jump prediction
     would buy.

Read-only. Places no trades.

Run: python3 jump_decompose.py --ticks 800000
"""
import argparse, math
import numpy as np
from deriv_api import DerivWS
from derivfetch import fetch_ticks, contiguous_pairs, wilson

EXEC_M = 1.818        # JD100 executed OVER4/UNDER5
BE = 1 / EXEC_M
WIN5 = [0, 1, 2, 8, 9]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="JD100")
    ap.add_argument("--ticks", type=int, default=800000)
    ap.add_argument("--out", default="../results/jump_decompose.md")
    a = ap.parse_args()

    ws = DerivWS(token="")
    print(f"fetching {a.ticks} unique ticks of {a.symbol}...")
    t, p, pip = fetch_ticks(ws, a.symbol, a.ticks)
    ws.close()
    v = np.round(p * (10 ** pip)).astype(np.int64)
    cg = contiguous_pairs(t, v)
    st = np.diff(v)[cg]
    spot = v[:-1][cg].astype(float)
    dig = (v % 10).astype(int)
    ent = dig[:-1][cg]
    off = ((dig[1:] - dig[:-1]) % 10)[cg]
    win = np.isin(off, WIN5)
    n = len(st)
    print(f"  {n} contiguous pairs, spot {spot.min()/10**pip:.2f}-{spot.max()/10**pip:.2f}\n")

    # ---- 1. which decomposition? ----------------------------------------
    print("=" * 76)
    print("1. SD-ADDITIVE vs VARIANCE-ADDITIVE")
    print("=" * 76)
    W = 1800
    nb = n // W
    sg, sp = [], []
    for i in range(nb):
        s = st[i*W:(i+1)*W].astype(float)
        thr = np.percentile(np.abs(s[s != 0]), 99.5) if (s != 0).any() else 20
        f = s[np.abs(s) <= thr]
        if len(f) < 500:
            continue
        sg.append(math.sqrt((f**2).mean()))
        sp.append(spot[i*W:(i+1)*W].mean())
    sg, sp = np.array(sg), np.array(sp)
    print(f"  {len(sg)} independent blocks of {W} ticks")

    b1, b0 = np.polyfit(sp, sg, 1)
    r_sd = sg - (b0 + b1*sp)
    # variance form: sigma^2 = c + k*spot^2
    A = np.vstack([np.ones_like(sp), sp**2]).T
    coef, *_ = np.linalg.lstsq(A, sg**2, rcond=None)
    c, k = coef
    pred_var = np.sqrt(np.maximum(c + k*sp**2, 1e-12))
    r_var = sg - pred_var
    print(f"  SD-additive : sigma = {b0:+.5f} + {b1:.6e}*spot     rmse {np.sqrt((r_sd**2).mean()):.5f}")
    print(f"  VAR-additive: sigma^2 = {c:+.5f} + {k:.6e}*spot^2   rmse {np.sqrt((r_var**2).mean()):.5f}")
    var_wins = np.sqrt((r_var**2).mean()) < np.sqrt((r_sd**2).mean())
    print(f"  -> {'VARIANCE-additive fits better: consistent with a real jump component'
                 if var_wins else 'SD-additive fits better: the intercept is NOT a variance floor'}")
    if c > 0:
        print(f"  implied sigma_jump = sqrt({c:.5f}) = {math.sqrt(max(c,0)):.4f} pips")
        print(f"  implied diffusion coefficient = {math.sqrt(max(k,0)):.6e}")

    # ---- 2. identify jumps ----------------------------------------------
    print(f"\n{'='*76}")
    print("2. JUMP IDENTIFICATION (step distribution as a mixture)")
    print("=" * 76)
    ab = np.abs(st).astype(float)
    core = ab[ab <= np.percentile(ab, 95)]
    sd_core = math.sqrt((core**2).mean())
    for mult in (3, 4, 5, 6):
        thr = mult * sd_core
        rate = float((ab > thr).mean())
        if rate > 0:
            print(f"  |step| > {mult}*core_sd ({thr:6.1f} pips): {rate*100:7.4f}% of ticks"
                  f"  = 1 per {1/rate:8.0f} ticks = 1 per {1/rate/60:6.1f} min")
    JT = 4 * sd_core
    isjump = ab > JT
    nj = int(isjump.sum())
    print(f"\n  using {JT:.1f} pips: {nj} jumps in {n} ticks")
    if nj:
        print(f"  mean |jump| {ab[isjump].mean():.1f} pips, "
              f"max {ab[isjump].max():.0f}")
        sd_nojump = math.sqrt((st[~isjump].astype(float)**2).mean())
        sd_all = math.sqrt((st.astype(float)**2).mean())
        print(f"  sigma including jumps {sd_all:.4f}, excluding {sd_nojump:.4f}")

    # ---- 3. are jumps predictable? --------------------------------------
    print(f"\n{'='*76}")
    print("3. ARE JUMPS PREDICTABLE?  (the whole question)")
    print("=" * 76)
    idx = np.flatnonzero(isjump)
    if len(idx) < 30:
        print("  too few jumps to test timing")
    else:
        gaps = np.diff(idx)
        print(f"  {len(gaps)} inter-jump intervals, mean {gaps.mean():.0f} ticks "
              f"({gaps.mean()/60:.1f} min), median {np.median(gaps):.0f}")
        cv = gaps.std() / gaps.mean()
        print(f"  coefficient of variation {cv:.4f}")
        print(f"    exponential (Poisson, memoryless) has CV = 1.000")
        print(f"    perfectly periodic has CV = 0.000")
        if cv < 0.7:
            print("  -> SUB-POISSON: intervals are more regular than random. PREDICTABLE.")
        elif cv > 1.3:
            print("  -> OVER-DISPERSED: jumps cluster.")
        else:
            print("  -> consistent with Poisson. Memoryless, so NOT predictable.")

        ind = isjump.astype(float)
        ind = ind - ind.mean()
        d0 = float((ind*ind).sum())
        thr_ac = 5/math.sqrt(n)
        worst = max(((abs(float((ind[:-l]*ind[l:]).sum())/d0), l)
                     for l in range(1, 200)))
        print(f"  jump-indicator autocorrelation: worst |r| {worst[0]:.5f} at lag "
              f"{worst[1]} (threshold {thr_ac:.5f})")
        m = 1 << int(math.log2(min(len(ind), 262144)))
        f = np.abs(np.fft.rfft(ind[:m]))**2
        pk = int(np.argmax(f[1:]) + 1)
        ratio = f[pk]/f[1:].mean()
        print(f"  spectral peak at period {m/pk:.1f} ticks, power {ratio:.1f}x mean "
              f"(expect < {2*math.log(len(f)):.0f} if noise)")

    # ---- 4. edge on non-jump ticks --------------------------------------
    print(f"\n{'='*76}")
    print(f"4. UPPER BOUND: edge if jump ticks could be avoided (payout {EXEC_M})")
    print("=" * 76)
    for lab, mask in (("all ticks", np.ones(n, bool)),
                      ("next tick is NOT a jump", ~isjump)):
        best = None
        for c in range(10):
            mm = mask & (ent == c)
            nn = int(mm.sum())
            if nn < 3000:
                continue
            k_ = int(win[mm].sum())
            pw = k_/nn
            lo, _ = wilson(k_, nn)
            if best is None or pw > best[0]:
                best = (pw, c, nn, lo)
        if best:
            print(f"  {lab:26} entry={best[1]} n={best[2]:>7} p={best[0]:.5f} "
                  f"EV {(best[0]*EXEC_M-1)*100:+.2f}% "
                  f"99%low {(best[3]*EXEC_M-1)*100:+.2f}%")
    print("\n  This is an UPPER BOUND: it conditions on knowledge you only have")
    print("  after the fact. It is achievable only to the extent section 3 shows")
    print("  jumps are predictable in advance.")

    open(a.out, "w").write(
        f"# Jump decomposition — {a.symbol}\n\n"
        f"SD-additive rmse {np.sqrt((r_sd**2).mean()):.5f}, "
        f"VAR-additive rmse {np.sqrt((r_var**2).mean()):.5f}.\n"
        f"sigma_jump = {math.sqrt(max(c,0)):.4f} pips.\n")
    print(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
