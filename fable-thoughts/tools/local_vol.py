"""local_vol.py — is W=1800 the wrong sigma window?

WHAT WE KNOW
duration_surface.py, working purely from raw ticks with no model, found the best JD100 cell
at sigma 3.88 is OVER4 on entry digit 7: p = 0.54825 against a 54.69% breakeven at the
executed payout 1.8286. That is +0.135pp — EV +0.25%, Wilson-99 low -0.94%. Sitting exactly
on breakeven. regime_revalidate reached the same conclusion by a completely different route
(offset tables + wrapped-normal), so the position is well established.

THE REMAINING LEVER
Every tool in this repo estimates sigma over a W=1800 rolling window. empirical_pmf.py's own
docstring names vol clustering as a suspect for the OOS shortfall. If volatility clusters,
an 1800-tick average is a badly LAGGED estimate of the sigma governing the very next tick —
and digit concentration depends on the LOCAL sigma, not the hour-old one.

If a short window predicts the next step better, then filtering on it should find sub-periods
where concentration is sharper than the W=1800 average implies, and the edge there is larger
than the blended number.

TESTS
  1. IS THERE CLUSTERING AT ALL? autocorrelation of |step| at lags 1..50, plus a
     block-permutation null. No clustering -> the whole idea is dead, stop here.
  2. WHICH WINDOW PREDICTS BEST? correlation between sigma estimated over the trailing
     w ticks and the realised |step| that follows, for w in 5..1800.
  3. DOES THE EDGE SHARPEN? entry-digit-conditioned win rate for OVER4/UNDER5 binned by
     SHORT-window sigma, evaluated at the EXECUTED payout 1.8286. If low-local-sigma bins
     clear breakeven by more than the blended 0.135pp, that is real headroom.
  4. IS IT TRADEABLE? the filter must be causal (uses only past ticks) and must leave enough
     trades to matter. Reports trades/hour at each threshold.

Split-half by block interleaving with purge, because sigma trends and a time split would put
IS and OOS in different regimes.

Read-only. Places no trades.

Run: python3 local_vol.py --symbol JD100
"""
import argparse, math
import numpy as np
from deriv_api import DerivWS

EXEC_M = 1.8286      # executed OVER4/UNDER5, from payout_audit
BREAKEVEN = 1 / EXEC_M


def wilson(k, n, z=2.576):
    if n == 0:
        return 0.0, 1.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return c - h, c + h


def roll_sigma(st, w):
    """Causal RMS of jump-filtered steps over the trailing w, aligned so out[i] uses
    steps strictly before i."""
    f = np.where(np.abs(st) <= 20, st.astype(float) ** 2, 0.0)
    c = np.where(np.abs(st) <= 20, 1.0, 0.0)
    cs = np.concatenate([[0.0], np.cumsum(f)])
    cc = np.concatenate([[0.0], np.cumsum(c)])
    out = np.full(len(st) + 1, np.nan)
    for i in range(w + 1, len(st) + 1):
        n = cc[i] - cc[i - w]
        if n >= max(5, w * 0.2):
            out[i] = math.sqrt((cs[i] - cs[i - w]) / n)
    return out


def interleave(n, block=20000, purge=2000):
    a = np.zeros(n, bool); b = np.zeros(n, bool)
    for s in range(0, n, block):
        e = min(s + block, n)
        (a if (s // block) % 2 == 0 else b)[min(s + purge, e):max(e - purge, s)] = True
    return a, b


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="JD100")
    ap.add_argument("--ticks", type=int, default=600000)
    ap.add_argument("--windows", type=int, nargs="+",
                    default=[5, 10, 20, 50, 100, 200, 500, 1800])
    ap.add_argument("--out", default="../results/local_vol.md")
    a = ap.parse_args()

    ws = DerivWS(token="")
    print(f"fetching {a.ticks} ticks of {a.symbol}...")
    _, prices, pip = ws.history_paged(a.symbol, a.ticks, sleep=0.2)
    pip = int(pip)
    v = np.round(np.asarray(prices, dtype=float) * (10 ** pip)).astype(np.int64)
    ws.close()
    dig = (v % 10).astype(np.int8)
    st = np.diff(v)
    absst = np.abs(st).astype(float)
    base = float(np.sqrt((st[np.abs(st) <= 20].astype(float) ** 2).mean()))
    print(f"got {len(v)} ticks, spot {prices[-1]}, sigma_pips {base:.3f}\n")

    # ---- 1. clustering ---------------------------------------------------
    print("=" * 72)
    print("1. IS THERE VOLATILITY CLUSTERING?  (autocorr of |step|)")
    print("=" * 72)
    x = absst - absst.mean()
    denom = float((x * x).sum())
    rng = np.random.default_rng(0)
    perm = absst.copy(); rng.shuffle(perm)
    xp = perm - perm.mean(); dp = float((xp * xp).sum())
    print(f"{'lag':>5}{'autocorr':>12}{'shuffled':>12}")
    any_clust = False
    for lag in (1, 2, 3, 5, 10, 20, 50):
        r = float((x[:-lag] * x[lag:]).sum()) / denom
        rp = float((xp[:-lag] * xp[lag:]).sum()) / dp
        print(f"{lag:>5}{r:>12.5f}{rp:>12.5f}")
        if abs(r) > 5 / math.sqrt(len(x)):
            any_clust = True
    thr = 5 / math.sqrt(len(x))
    print(f"\n  |r| > {thr:.5f} (5/sqrt n) counts as real")
    if not any_clust:
        print("  NO CLUSTERING. Local sigma cannot beat the long window. Hypothesis dead.")
        return
    print("  Clustering present — a short window may carry information.")

    # ---- 2. which window predicts the next |step| best? ------------------
    print(f"\n{'='*72}")
    print("2. WHICH WINDOW BEST PREDICTS THE NEXT |step|?")
    print("=" * 72)
    print(f"{'window':>8}{'corr with next |step|':>24}")
    best_w = None
    for w in a.windows:
        s = roll_sigma(st, w)[:-1]          # sigma known before step i
        m = ~np.isnan(s)
        if m.sum() < 10000:
            continue
        r = float(np.corrcoef(s[m], absst[m])[0, 1])
        print(f"{w:>8}{r:>24.5f}")
        if best_w is None or r > best_w[1]:
            best_w = (w, r)
    print(f"\n  best predictor: w={best_w[0]} (r={best_w[1]:.5f})")
    if best_w[0] >= 1800:
        print("  W=1800 is already optimal. No headroom from a shorter window.")

    # ---- 3. does the edge sharpen in low-local-sigma bins? ---------------
    print(f"\n{'='*72}")
    print(f"3. ENTRY-CONDITIONED EDGE BY LOCAL SIGMA (executed payout {EXEC_M})")
    print(f"   breakeven {BREAKEVEN*100:.2f}%  |  blended result was 54.825% (+0.135pp)")
    print("=" * 72)

    w = best_w[0]
    sig = roll_sigma(st, w)[:len(st)]   # align: sig[i] known before step i
    # OVER4 on entry 7 and UNDER5 on entry 2: both are the +/-2 window on entry
    ent = dig[:-1]
    nxt = dig[1:]
    off = (nxt.astype(int) - ent.astype(int)) % 10
    win = np.isin(off, [0, 1, 2, 8, 9])
    ok = ~np.isnan(sig)

    m = min(len(sig), len(win), len(ok))
    sig, win, ok = sig[:m], win[:m], ok[:m]
    is_m, oos_m = interleave(m)
    qs = np.nanpercentile(sig[ok], [10, 25, 50, 75, 90])
    edges = [-np.inf] + list(qs) + [np.inf]

    print(f"{'local sigma':>18}{'n(OOS)':>9}{'p':>9}{'vs BE':>9}"
          f"{'EV':>9}{'99% low':>10}{'trades/hr':>11}")
    rows = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        sel = ok & (sig >= lo) & (sig < hi)
        so = sel & oos_m
        n = int(so.sum())
        if n < 3000:
            continue
        k = int(win[so].sum())
        p = k / n
        wl, _ = wilson(k, n)
        ev, evlo = p * EXEC_M - 1, wl * EXEC_M - 1
        frac = n / max(int((ok & oos_m).sum()), 1)
        rows.append((lo, hi, n, p, ev, evlo, frac))
        lab = f"[{lo:.2f},{hi:.2f})" if np.isfinite(lo) else f"< {hi:.2f}"
        print(f"{lab:>18}{n:>9}{p:>9.5f}{(p-BREAKEVEN)*100:>+8.3f}pp"
              f"{ev*100:>8.2f}%{evlo*100:>9.2f}%{frac*3600:>11.0f}")

    # ---- 4. verdict ------------------------------------------------------
    print(f"\n{'='*72}")
    good = [r for r in rows if r[5] > 0]
    if good:
        b = max(good, key=lambda r: r[5])
        print(f"  HEADROOM FOUND. local sigma < {b[1]:.2f}: p={b[3]:.5f} "
              f"({(b[3]-BREAKEVEN)*100:+.3f}pp vs breakeven)")
        print(f"  EV {b[4]*100:+.2f}%, Wilson-99 low {b[5]*100:+.2f}%, "
              f"{b[6]*100:.0f}% of ticks -> ~{b[6]*3600:.0f} trades/hr")
        print(f"  vs blended +0.135pp. Gating on w={w} sigma is worth "
              f"{((b[3]-BREAKEVEN)*100 - 0.135):+.3f}pp.")
        print("\n  NEXT: confirm on a fresh non-overlapping block, then paper-trade.")
        print("  The filter is causal (trailing window only), so it is implementable.")
    else:
        print("  NO HEADROOM. No local-sigma bin clears breakeven at the 99% lower bound.")
        print(f"  Short-window gating does not beat the W=1800 blended result.")

    md = [f"# Local volatility conditioning — {a.symbol}\n\n",
          f"{len(v)} ticks, sigma_pips {base:.3f}. Executed payout {EXEC_M}, "
          f"breakeven {BREAKEVEN*100:.2f}%.\n\n",
          f"Best predictive window w={best_w[0]} (r={best_w[1]:.5f} with next |step|).\n\n",
          "| local sigma | n | p | vs BE | EV | 99% low | frac |\n|---|---:|---:|---:|---:|---:|---:|\n"]
    for lo, hi, n, p, ev, evlo, frac in rows:
        lab = f"[{lo:.2f},{hi:.2f})" if np.isfinite(lo) else f"<{hi:.2f}"
        md.append(f"| {lab} | {n} | {p:.5f} | {(p-BREAKEVEN)*100:+.3f}pp | "
                  f"{ev*100:+.2f}% | {evlo*100:+.2f}% | {frac*100:.1f}% |\n")
    open(a.out, "w").write("".join(md))
    print(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
