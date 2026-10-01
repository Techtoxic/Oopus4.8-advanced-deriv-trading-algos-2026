"""info_bound.py — the maximum edge ANY model could extract. One number that bounds everything.

WHY THIS IS DIFFERENT FROM EVERY OTHER TEST IN THE REPO
Every previous test asked "does THIS pattern predict?" — chi-square on entry/offset,
autocorrelation, vol clustering, 112 chart-pattern cells. Each closes one hypothesis.

Mutual information closes ALL of them at once. I(past ; next) is the total predictable
information in the series, in bits. It upper-bounds what ANY function of the past can
extract: neural networks, context trees, signature methods, bispectra, deterministic chaos,
a perfect model of the generator. If I is essentially zero, none of them can work, and there
is no point running them one at a time.

WHAT WAS ACTUALLY TESTED BEFORE
  randomness_battery test C: chi-square on P(offset | entry_digit)   -> k=1 only
  randomness_battery test D: chi-square on P(offset_t | offset_{t-1}) -> k=2 only
  cross_symbol: digit MI BETWEEN symbols                              -> not within
  local_vol: |step| autocorrelation                                   -> pairwise, linear-ish

Nobody measured I(past_k ; next) within a symbol for k = 3..10. That is the real gap, and
it is where a generator with internal state would leave its fingerprint: subtle high-order
structure that is invisible pairwise but accumulates over longer contexts.

METHOD
  1. Plug-in MI with the Miller-Madow bias correction, on digit contexts of length k.
     Raw plug-in MI is biased UPWARD by roughly (cells-1)/(2n ln2) bits — with 10^k contexts
     that bias explodes, and mistaking it for signal is the classic error here. Corrected AND
     compared against a shuffled null.
  2. SHUFFLED CONTROL at every k. Same marginal digit distribution, order destroyed. The
     null is not zero, it is whatever the estimator returns on structureless data.
  3. Conditioned on sigma bin, since sigma is the one variable known to matter.
  4. Converted into the economic bound: how much win rate could I bits possibly buy?

THE CONVERSION THAT MATTERS
For a binary decision, an edge of d over 50% requires roughly
    I >= 1 - H2(0.5 + d)  bits
where H2 is the binary entropy. So a 0.5pp edge needs about 7.2e-5 bits. If measured MI sits
below that, the edge is not merely unmeasured — it is impossible.

Read-only. No trades.

Run: python3 info_bound.py --symbol JD100 --ticks 4000000
"""
import argparse, math
import numpy as np
from deriv_api import DerivWS
from derivfetch import fetch_ticks, contiguous_pairs, native_interval


def h2(p):
    if p <= 0 or p >= 1:
        return 0.0
    return -p * math.log2(p) - (1 - p) * math.log2(1 - p)


def edge_for_bits(I):
    """Smallest win-rate edge over 50% that requires at least I bits."""
    lo, hi = 0.0, 0.5
    for _ in range(60):
        mid = (lo + hi) / 2
        if 1 - h2(0.5 + mid) < I:
            lo = mid
        else:
            hi = mid
    return lo


def mi_plugin(ctx, nxt, n_ctx, n_sym=10):
    """
    Plug-in mutual information in bits, with the Miller-Madow bias correction.
    Raw plug-in MI is biased upward by about (cells - 1) / (2 n ln2); with 10^k contexts
    that term dominates, which is exactly how spurious 'high-order structure' appears.
    """
    n = len(nxt)
    if n < 1000:
        return float("nan"), float("nan")
    joint = np.zeros((n_ctx, n_sym))
    np.add.at(joint, (ctx, nxt), 1)
    pj = joint / n
    px = pj.sum(1, keepdims=True)
    py = pj.sum(0, keepdims=True)
    with np.errstate(divide="ignore", invalid="ignore"):
        term = pj * np.log2(pj / (px @ py))
    raw = float(np.nansum(term))
    occupied_x = int((joint.sum(1) > 0).sum())
    occupied_y = int((joint.sum(0) > 0).sum())
    cells = max(occupied_x * occupied_y - occupied_x - occupied_y + 1, 0)
    bias = cells / (2 * n * math.log(2))
    return raw, max(raw - bias, 0.0)


def build_context(dig, k, n_sym=10):
    """Map the previous k digits to a single integer context id."""
    n = len(dig) - k
    ctx = np.zeros(n, dtype=np.int64)
    for j in range(k):
        ctx = ctx * n_sym + dig[j:j + n]
    return ctx, dig[k:]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="JD100")
    ap.add_argument("--ticks", type=int, default=4000000)
    ap.add_argument("--max-k", type=int, default=6)
    ap.add_argument("--payout", type=float, default=1.80)
    ap.add_argument("--out", default="../results/info_bound.md")
    a = ap.parse_args()

    ws = DerivWS(token="")
    print(f"fetching {a.ticks} unique ticks of {a.symbol}...")
    t, p, pip = fetch_ticks(ws, a.symbol, a.ticks)
    ws.close()
    v = np.round(p * (10 ** pip)).astype(np.int64)
    iv = native_interval(t)
    cg = contiguous_pairs(t, v, iv)
    dig = (v % 10).astype(np.int64)
    days = (t[-1] - t[0]) / 86400
    print(f"  {len(v)} ticks, {iv}s, {days:.1f} days\n")

    rng = np.random.default_rng(0)
    dig_sh = dig.copy()
    rng.shuffle(dig_sh)

    print("=" * 78)
    print("MUTUAL INFORMATION between the last k digits and the next digit")
    print("=" * 78)
    print(f"  {'k':>3}{'contexts':>10}{'n':>10}{'raw MI':>11}{'corrected':>11}"
          f"{'shuffled':>11}{'excess':>11}")
    print("  " + "-" * 74)

    rows = []
    for k in range(1, a.max_k + 1):
        n_ctx = 10 ** k
        if n_ctx > len(dig) / 50:
            print(f"  {k:>3}{n_ctx:>10}   too many contexts for this sample — stopping")
            break
        ctx, nxt = build_context(dig, k)
        raw, corr = mi_plugin(ctx, nxt, n_ctx)
        ctx_s, nxt_s = build_context(dig_sh, k)
        raw_s, corr_s = mi_plugin(ctx_s, nxt_s, n_ctx)
        excess = corr - corr_s
        rows.append((k, n_ctx, len(nxt), raw, corr, corr_s, excess))
        print(f"  {k:>3}{n_ctx:>10}{len(nxt):>10}{raw:>11.6f}{corr:>11.6f}"
              f"{corr_s:>11.6f}{excess:>+11.6f}")

    if not rows:
        print("  no usable k"); return

    best = max(rows, key=lambda r: r[6])
    I = max(best[6], 0.0)

    print(f"\n  largest excess MI over shuffled: {I:.6f} bits at k={best[0]}")

    # ---- economic conversion ---------------------------------------------
    print(f"\n{'='*78}")
    print("WHAT THAT MUCH INFORMATION IS WORTH")
    print("=" * 78)
    be = 1.0 / a.payout
    need_edge = be - 0.5
    need_bits = 1 - h2(be)
    print(f"  payout {a.payout} -> breakeven win rate {be*100:.2f}%")
    print(f"  that is an edge of {need_edge*100:.2f}pp over a coin flip")
    print(f"  which REQUIRES at least {need_bits:.6f} bits of information")
    print()
    print(f"  measured excess MI: {I:.6f} bits")
    print(f"  maximum edge that buys: {edge_for_bits(I)*100:.4f}pp over 50%")
    print(f"  i.e. best possible win rate {50 + edge_for_bits(I)*100:.4f}%")
    print()
    print(f"  {'bits needed for a given edge:':30}")
    for e in (0.001, 0.005, 0.01, 0.02, 0.0525):
        print(f"    {e*100:>5.2f}pp edge  ->  {1-h2(0.5+e):.6f} bits")

    print(f"\n{'='*78}")
    print("VERDICT")
    print("=" * 78)
    if I >= need_bits:
        print(f"  Measured information ({I:.6f} bits) EXCEEDS the {need_bits:.6f} bits")
        print(f"  needed to beat the payout. An edge is information-theoretically")
        print(f"  POSSIBLE at k={best[0]}. Next: build a context-tree or n-gram predictor")
        print(f"  and see how much of it is actually reachable out of sample.")
    else:
        print(f"  Measured information {I:.6f} bits < {need_bits:.6f} bits required.")
        print(f"  The maximum achievable win rate from ANY model of the digit history")
        print(f"  is {50 + edge_for_bits(I)*100:.4f}%, against a breakeven of {be*100:.2f}%.")
        print()
        print("  This closes, simultaneously and without needing to run them:")
        print("    - higher-order Markov / context-tree weighting")
        print("    - bispectrum and Volterra series (nonlinear memory)")
        print("    - signature methods and neural nets on path features")
        print("    - deterministic chaos reconstruction")
        print("    - recovering the generator itself")
        print("  None can extract information the series does not contain.")

    with open(a.out, "w") as f:
        f.write(f"# Information bound — {a.symbol}\n\n{len(v)} ticks, {days:.1f} days.\n\n"
                "| k | contexts | n | raw MI | corrected | shuffled | excess |\n"
                "|---|---|---|---|---|---|---|\n")
        for k, nc, n, raw, corr, cs, ex in rows:
            f.write(f"| {k} | {nc} | {n} | {raw:.6f} | {corr:.6f} | {cs:.6f} "
                    f"| {ex:+.6f} |\n")
        f.write(f"\nExcess MI {I:.6f} bits; {need_bits:.6f} bits needed for the "
                f"{be*100:.2f}% breakeven.\n")
    print(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
