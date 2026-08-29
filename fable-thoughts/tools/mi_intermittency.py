"""mi_intermittency.py — is the 0.0023-bit bound an average hiding rare predictable windows?

THE HOLE THIS TESTS
info_bound.py measured I(past ; next) = 0.0023 bits over 4M ticks and closed every model
that is a function of the past. But that is a SAMPLE AVERAGE. If predictability is
intermittent — most windows near zero, rare windows well above breakeven — the mean is
unchanged and a strategy that trades only the spikes is not bounded by it.

This is the one structural gap in the information argument. Everything else proposed against
it (Koopman modes, random matrix spacing, p-adic valuations, RG coarse-graining, spin-glass
overlaps, subword complexity) is still a function g(past), and by the data processing
inequality I(g(past); next) <= I(past; next). Those tests can DETECT structure; they cannot
exceed the bound. Intermittency is different because it attacks the averaging itself.

THE TRAP, AND WHY A NAIVE VERSION FAILS
Windowed MI is noisy. On w = 50,000 ticks with 10 contexts the estimator's own scatter
produces windows that look far more predictable than the mean purely by chance. Ranking
windows by MI and trading the top ones is textbook selection bias — it is exactly how the
entry-digit result died (2.42pp -> 0.94pp -> 0.36pp as n grew).

So the question is NOT "are some windows higher than others" — they always are. It is:

    is the DISTRIBUTION of windowed MI wider than the same statistic on shuffled data?

Shuffling destroys time structure while preserving the digit marginal exactly. If real and
shuffled produce the same spread of windowed MI, the variation is estimator noise and there
are no predictable regimes. If real has a heavier right tail, some windows genuinely carry
more information.

AND THE SECOND TEST THAT MATTERS
Even a real spike is useless unless it is IDENTIFIABLE IN ADVANCE. So windows are split
into first and second halves: does MI measured on the first half predict MI on the second?
If not, you cannot know you are in a good regime until it has passed.

  1. windowed MI across a sweep of window sizes, real vs shuffled
  2. right-tail comparison: what fraction of windows exceed the breakeven requirement
  3. persistence: corr(MI in first half, MI in second half) of each window
  4. economic translation: the win rate achievable in the best windows, if any

Read-only. No trades.

Run: python3 mi_intermittency.py --symbol JD100 --ticks 4000000
"""
import argparse, math
import numpy as np
from deriv_api import DerivWS
from derivfetch import fetch_ticks, native_interval


def h2(p):
    if p <= 0 or p >= 1:
        return 0.0
    return -p * math.log2(p) - (1 - p) * math.log2(1 - p)


def edge_for_bits(I):
    lo, hi = 0.0, 0.5
    for _ in range(60):
        mid = (lo + hi) / 2
        if 1 - h2(0.5 + mid) < I:
            lo = mid
        else:
            hi = mid
    return lo


def mi_mm(ctx, nxt, n_ctx=10, n_sym=10):
    """Miller-Madow corrected plug-in MI in bits."""
    n = len(nxt)
    if n < 200:
        return float("nan")
    j = np.zeros((n_ctx, n_sym))
    np.add.at(j, (ctx, nxt), 1)
    pj = j / n
    px, py = pj.sum(1, keepdims=True), pj.sum(0, keepdims=True)
    with np.errstate(divide="ignore", invalid="ignore"):
        t = pj * np.log2(pj / (px @ py))
    raw = float(np.nansum(t))
    ox = int((j.sum(1) > 0).sum())
    oy = int((j.sum(0) > 0).sum())
    cells = max(ox * oy - ox - oy + 1, 0)
    return max(raw - cells / (2 * n * math.log(2)), 0.0)


def windowed(dig, w):
    """MI per non-overlapping window of w ticks, using k=1 context."""
    ctx, nxt = dig[:-1], dig[1:]
    n = len(nxt) // w
    return np.array([mi_mm(ctx[i*w:(i+1)*w], nxt[i*w:(i+1)*w]) for i in range(n)])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="JD100")
    ap.add_argument("--ticks", type=int, default=4000000)
    ap.add_argument("--windows", type=int, nargs="+",
                    default=[10000, 25000, 50000, 100000, 250000])
    ap.add_argument("--payout", type=float, default=1.80)
    ap.add_argument("--out", default="../results/mi_intermittency.md")
    a = ap.parse_args()

    ws = DerivWS(token="")
    print(f"fetching {a.ticks} unique ticks of {a.symbol}...")
    t, p, pip = fetch_ticks(ws, a.symbol, a.ticks)
    ws.close()
    v = np.round(p * (10 ** pip)).astype(np.int64)
    dig = (v % 10).astype(np.int64)
    days = (t[-1] - t[0]) / 86400
    print(f"  {len(dig)} ticks, {native_interval(t)}s, {days:.1f} days\n")

    rng = np.random.default_rng(0)
    sh = dig.copy(); rng.shuffle(sh)

    be = 1.0 / a.payout
    need_bits = 1 - h2(be)
    print("=" * 80)
    print("1. IS THE SPREAD OF WINDOWED MI WIDER THAN NOISE?")
    print("=" * 80)
    print(f"  breakeven {be*100:.2f}% needs {need_bits:.6f} bits in a window to be tradable")
    print()
    print(f"  {'window':>9}{'n_win':>7}{'mean(real)':>12}{'sd(real)':>11}"
          f"{'mean(shuf)':>12}{'sd(shuf)':>11}{'sd ratio':>10}{'>need':>8}")
    print("  " + "-" * 78)

    rows = []
    for w in a.windows:
        mr = windowed(dig, w)
        ms = windowed(sh, w)
        if len(mr) < 5:
            continue
        ratio = mr.std() / ms.std() if ms.std() > 0 else float("nan")
        over = int((mr > need_bits).sum())
        rows.append((w, len(mr), mr, ms, ratio, over))
        print(f"  {w:>9}{len(mr):>7}{mr.mean():>12.6f}{mr.std():>11.6f}"
              f"{ms.mean():>12.6f}{ms.std():>11.6f}{ratio:>10.3f}{over:>8}")

    if not rows:
        print("  no usable windows"); return

    print()
    ratios = [r[4] for r in rows if np.isfinite(r[4])]
    print(f"  sd ratio real/shuffled across window sizes: "
          f"{min(ratios):.3f} to {max(ratios):.3f}")
    wide = max(ratios) > 1.25
    print(f"  -> {'REAL IS WIDER — some windows carry genuinely more information'
                 if wide else 'same spread as noise: the variation IS estimator scatter'}")

    # ---- 2. right tail -----------------------------------------------------
    print(f"\n{'='*80}")
    print("2. RIGHT TAIL — do any windows clear the breakeven requirement?")
    print("=" * 80)
    print(f"  {'window':>9}{'p90(real)':>12}{'p99(real)':>12}{'max(real)':>12}"
          f"{'max(shuf)':>12}{'need':>11}")
    for w, nw, mr, ms, ratio, over in rows:
        print(f"  {w:>9}{np.percentile(mr,90):>12.6f}{np.percentile(mr,99):>12.6f}"
              f"{mr.max():>12.6f}{ms.max():>12.6f}{need_bits:>11.6f}")
    any_over = any(r[5] > 0 for r in rows)
    print(f"\n  windows above the breakeven bit requirement: "
          f"{sum(r[5] for r in rows)} of {sum(r[1] for r in rows)}")
    print(f"  (shuffled data would also produce some by chance — compare max columns)")

    # ---- 3. persistence ----------------------------------------------------
    print(f"\n{'='*80}")
    print("3. PERSISTENCE — is a high-MI window identifiable BEFORE it ends?")
    print("=" * 80)
    print("  a spike is worthless unless the first half predicts the second half")
    print(f"\n  {'window':>9}{'n':>7}{'corr(h1,h2) real':>19}{'shuffled':>12}")
    for w, nw, mr, ms, ratio, over in rows:
        if nw < 20:
            continue
        ctx, nxt = dig[:-1], dig[1:]
        h = w // 2
        a1, a2 = [], []
        for i in range(nw):
            seg_c = ctx[i*w:(i+1)*w]; seg_n = nxt[i*w:(i+1)*w]
            a1.append(mi_mm(seg_c[:h], seg_n[:h]))
            a2.append(mi_mm(seg_c[h:], seg_n[h:]))
        a1, a2 = np.array(a1), np.array(a2)
        c_real = float(np.corrcoef(a1, a2)[0, 1]) if a1.std() > 0 else float("nan")
        sctx, snxt = sh[:-1], sh[1:]
        b1, b2 = [], []
        for i in range(nw):
            seg_c = sctx[i*w:(i+1)*w]; seg_n = snxt[i*w:(i+1)*w]
            b1.append(mi_mm(seg_c[:h], seg_n[:h]))
            b2.append(mi_mm(seg_c[h:], seg_n[h:]))
        c_shuf = float(np.corrcoef(np.array(b1), np.array(b2))[0, 1])
        print(f"  {w:>9}{nw:>7}{c_real:>19.4f}{c_shuf:>12.4f}")

    # ---- verdict -----------------------------------------------------------
    print(f"\n{'='*80}")
    print("VERDICT")
    print("=" * 80)
    if wide and any_over:
        print("  INTERMITTENCY DETECTED and some windows clear the bit requirement.")
        print("  Before believing it: check section 3. If the first half does not predict")
        print("  the second, the spikes are only visible in hindsight and untradable.")
    elif wide:
        print("  Real windows are wider than shuffled, so information is unevenly")
        print("  distributed — but no window reaches the bits needed for breakeven.")
        print("  Real structure, still below the payout.")
    else:
        print("  The spread of windowed MI matches shuffled data at every window size.")
        print("  The variation is estimator scatter, not regime change. The 0.0023-bit")
        print("  bound is uniform, not an average over good and bad periods.")
        print()
        print("  This closes the last gap in the information argument: predictability")
        print("  is not intermittent, so there is no subset of time worth trading.")

    with open(a.out, "w") as f:
        f.write(f"# MI intermittency — {a.symbol}\n\n{len(dig)} ticks, {days:.1f} days. "
                f"Breakeven needs {need_bits:.6f} bits.\n\n"
                "| window | n | mean real | sd real | mean shuf | sd shuf | sd ratio |\n"
                "|---|---|---|---|---|---|---|\n")
        for w, nw, mr, ms, ratio, over in rows:
            f.write(f"| {w} | {nw} | {mr.mean():.6f} | {mr.std():.6f} | "
                    f"{ms.mean():.6f} | {ms.std():.6f} | {ratio:.3f} |\n")
    print(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
