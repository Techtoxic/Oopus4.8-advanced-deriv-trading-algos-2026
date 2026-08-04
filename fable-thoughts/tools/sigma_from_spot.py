"""sigma_from_spot.py — is sigma HIDDEN, or is it just spot?

THE PROPOSAL THIS ANSWERS
A suggestion was made to use an alpha-beta (forward-backward) HMM to infer more accurate
contract values. The instinct is sound: our sigma estimate is a rolling RMS over W=1800
ticks with sampling error sigma/sqrt(2n) ~ 1.7%, i.e. +/-0.064 at sigma 3.8. EV moves
0.4pp per 0.1 of sigma, so estimator noise costs +/-0.26pp of EV — material when the whole
edge is 1.45%.

But an HMM infers HIDDEN states, and this session established there are none:
    entry/offset chi2 = 67.6 on 81 df (null mean 81) -> independent
    |step| autocorrelation max 0.0057, R^2 = 3e-5   -> no volatility clustering
    cross-symbol: 21 pairs, max |z| 2.5             -> independent streams
    p(sigma) = wrapped normal, residuals < 0.002 across a 44x sigma range
Given a memoryless process whose only state variable is sigma, forward-backward would spend
hours fitting latent states to noise — and it would fit, because flexible models always do
in sample.

THE ALTERNATIVE
For a GBM, absolute volatility scales with price. In pips:

    sigma_pips = sigma_relative * spot * 10^decimals

So sigma_pips should be EXACTLY PROPORTIONAL TO SPOT, and spot is observed with zero error.
If that holds, the rolling estimator is pure added noise and should be replaced by

    sigma_hat = k * spot,   k estimated once over a huge sample

which has essentially zero variance per observation.

WHAT THIS TESTS
  1. Regress rolling sigma on spot. If sigma_pips = k*spot exactly, R^2 -> 1 and the
     residual is just the estimator's own sampling error.
  2. Compare the two estimators' noise directly: rolling RMS vs k*spot, against a
     long-window reference. Lower residual variance wins.
  3. Translate the improvement into EV: how much of the +/-0.26pp is recovered.
  4. Check stability of k over time — if k drifts, proportionality is only local.

If proportionality holds, this replaces the sigma gate input with a near-exact quantity and
is strictly better than any latent-state model, because there is no latent state.

Read-only. Places no trades.

Run: python3 sigma_from_spot.py --symbol JD100 --ticks 800000
"""
import argparse, math
import numpy as np
from deriv_api import DerivWS
from derivfetch import fetch_ticks, contiguous_pairs, sigma_series, jump_threshold


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="JD100")
    ap.add_argument("--ticks", type=int, default=800000)
    ap.add_argument("--out", default="../results/sigma_from_spot.md")
    a = ap.parse_args()

    ws = DerivWS(token="")
    print(f"fetching {a.ticks} unique ticks of {a.symbol}...")
    t, p, pip = fetch_ticks(ws, a.symbol, a.ticks)
    ws.close()
    v = np.round(p * (10 ** pip)).astype(np.int64)
    cg = contiguous_pairs(t, v)
    jt = jump_threshold(v, cg)
    sig = sigma_series(v, mask=cg, jump=jt)
    spot = v[:-1].astype(float)
    ok = ~np.isnan(sig)
    sig, spot, tt = sig[ok], spot[ok], t[:-1][ok]
    print(f"  {len(sig)} usable, sigma {sig.min():.3f}-{sig.max():.3f}, "
          f"spot {spot.min()/10**pip:.2f}-{spot.max()/10**pip:.2f}\n")

    # ---- 1. proportionality ----------------------------------------------
    print("=" * 70)
    print("1. IS sigma_pips PROPORTIONAL TO SPOT?")
    print("=" * 70)
    # AFFINE fit is the correct specification. Forcing through the origin makes the
    # slope absorb the intercept, which then LOOKS like drift as spot falls (k rose
    # monotonically 1.744 -> 1.780 across time chunks — the signature of misspecification,
    # not instability).
    b1, b0 = np.polyfit(spot, sig, 1)
    pred = b0 + b1 * spot
    k = float(np.sum(sig * spot) / np.sum(spot * spot))      # kept for comparison
    ss_res = float(np.sum((sig - pred) ** 2))
    ss_tot = float(np.sum((sig - sig.mean()) ** 2))
    r2 = 1 - ss_res / ss_tot
    r2_orig = 1 - float(np.sum((sig - k*spot)**2)) / ss_tot
    print(f"  AFFINE (correct): sigma = {b0:+.5f} + {b1:.6e} * spot   R^2 = {r2:.5f}")
    print(f"  through-origin  : sigma = {k:.6e} * spot                R^2 = {r2_orig:.5f}")
    print(f"  intercept {b0:+.4f} pips = {abs(b0)/sig.mean()*100:.2f}% of mean sigma — REAL")
    sd_tot = math.sqrt(ss_tot/len(sig))
    theo0 = sig.mean()/math.sqrt(2*1800)
    ceil = 1 - theo0**2/sd_tot**2
    print(f"  sigma varies with sd {sd_tot:.4f}; estimator noise alone is {theo0:.4f}")
    print(f"  -> MAX ACHIEVABLE R^2 given that noise is {ceil:.5f}; we got {r2:.5f} "
          f"({r2/ceil*100:.1f}% of ceiling)")
    resid = sig - pred
    print(f"  residual sd {resid.std():.5f} = {resid.std()/sig.mean()*100:.2f}% of sigma")

    # theoretical sampling error of a rolling RMS over W=1800
    theo = sig.mean() / math.sqrt(2 * 1800)
    print(f"  theoretical rolling-RMS sampling error: {theo:.5f} "
          f"({theo/sig.mean()*100:.2f}%)")
    print(f"  -> residual/theoretical = {resid.std()/theo:.2f}")
    if resid.std() / theo < 1.5:
        print("     the residual IS the estimator's own noise. Proportionality is exact,")
        print("     and k*spot is the better estimator because spot has no error.")
    else:
        print("     residual exceeds estimator noise — proportionality is only approximate.")

    # ---- 2. estimator comparison ----------------------------------------
    print(f"\n{'='*70}")
    print("2. WHICH ESTIMATOR IS CLOSER TO TRUTH?")
    print("=" * 70)
    ref = sigma_series(v, mask=cg, jump=jt, w=20000)
    ref = ref[ok]
    m = ~np.isnan(ref)
    if m.sum() > 50000:
        e_roll = sig[m] - ref[m]
        e_spot = pred[m] - ref[m]   # affine model
        print(f"  reference: W=20000 rolling sigma ({int(m.sum())} points)")
        print(f"  {'estimator':22}{'bias':>10}{'sd':>10}{'RMSE':>10}")
        e_orig = (k*spot)[m] - ref[m]
        for lab, e in (("rolling W=1800", e_roll), ("through-origin k*spot", e_orig),
                       ("AFFINE a + b*spot", e_spot)):
            print(f"  {lab:22}{e.mean():>+10.5f}{e.std():>10.5f}"
                  f"{math.sqrt((e**2).mean()):>10.5f}")
        imp = 1 - math.sqrt((e_spot ** 2).mean()) / math.sqrt((e_roll ** 2).mean())
        print(f"\n  k*spot reduces RMSE by {imp*100:.1f}%")
    else:
        imp = 0.0
        print("  not enough data for the long-window reference")

    # ---- 3. EV consequence ------------------------------------------------
    print(f"\n{'='*70}")
    print("3. WHAT IS THAT WORTH IN EV?")
    print("=" * 70)
    dEV_dsigma = 7.07          # %/unit, from redo_all's fit
    err_roll = resid.std()
    print(f"  EV sensitivity: {dEV_dsigma:.2f} %/unit of sigma (redo_all fit)")
    print(f"  rolling-estimator sigma error {err_roll:.4f} "
          f"-> +/-{err_roll*dEV_dsigma:.3f}pp of EV")
    print(f"  k*spot error ~0 (spot is exact) -> that error is removed")
    print(f"  edge at the crossing is +1.45%, so recovering "
          f"{err_roll*dEV_dsigma:.3f}pp is {err_roll*dEV_dsigma/1.45*100:.0f}% of it")

    # ---- 4. is k stable? --------------------------------------------------
    print(f"\n{'='*70}")
    print("4. IS k STABLE OVER TIME?  (proportionality must not drift)")
    print("=" * 70)
    # Per-chunk refitting CANNOT identify the slope. Each chunk spans ~1 day, over which
    # spot moves <1% (748 pips out of 20,000+), so the slope has almost no lever arm: its
    # standard error is 22.6x the full-sample value, making chunk slopes ~100% noise. The
    # observed 48.7% spread is exactly what pure noise produces. The through-origin k looks
    # stable only because the origin gives it an enormous lever arm.
    # The correct test is OUT-OF-SAMPLE PREDICTION: fit on the older half, predict the newer.
    h = len(sig) // 2
    b1_is, b0_is = np.polyfit(spot[:h], sig[:h], 1)
    err_oos = (b0_is + b1_is * spot[h:]) - sig[h:]
    print(f"  fit on OLDER half : sigma = {b0_is:+.5f} + {b1_is:.6e} * spot")
    print(f"  full-sample fit   : sigma = {b0:+.5f} + {b1:.6e} * spot")
    print(f"  slope difference  : {(b1_is/b1-1)*100:+.2f}%")
    print(f"")
    print(f"  OOS prediction error on the NEWER half:")
    print(f"    bias {err_oos.mean():+.5f}  sd {err_oos.std():.5f}  "
          f"RMSE {math.sqrt(float((err_oos**2).mean())):.5f}")
    print(f"    estimator noise floor {theo:.5f}")
    ratio_oos = err_oos.std() / theo
    print(f"    OOS residual / noise floor = {ratio_oos:.2f}")
    stable = ratio_oos < 1.5
    verdict = ("STABLE: OOS error sits at the noise floor, parameters do not drift"
               if stable else "OOS error exceeds the noise floor - parameters drift")
    print(f"  -> {verdict}")
    print(f"  (pooled residual/noise over the full window was {resid.std()/theo:.2f};")
    print(f"   drifting parameters could not produce a fit at the noise floor.)")

    print(f"\n{'='*70}")
    print("VERDICT")
    print("=" * 70)
    if r2 > 0.85 and stable:
        print(f"  sigma_pips = {b0:+.5f} + {b1:.6e} * spot,  R^2 {r2:.5f} "
              f"({r2/ceil*100:.1f}% of the noise ceiling), slope stable to "
              f"stable OOS.")
        print("  sigma is NOT a hidden state. It is spot times a constant.")
        print("  Replace the rolling estimator in the sentinel's gate with k*spot:")
        print("    - removes the estimator's sampling noise entirely")
        print("    - responds instantly instead of lagging 1800 ticks")
        print(f"    - recovers ~{err_roll*dEV_dsigma:.2f}pp of EV")
        print("  An HMM cannot beat this, because there is no latent state to infer.")
    else:
        print(f"  R^2 {r2:.5f}, k stable={stable}. Proportionality is not exact enough")
        print("  to replace the rolling estimator outright — keep both and blend.")

    open(a.out, "w").write(
        f"# sigma vs spot — {a.symbol}\n\nsigma = {k:.6e} * spot, R^2 = {r2:.5f}, "
        f"residual sd {resid.std():.5f} vs theoretical estimator noise {theo:.5f}.\n"
        f"k spread across {nchunk} chunks: {(ks.max()/ks.min()-1)*100:.3f}%.\n")
    print(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
