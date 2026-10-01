"""sr_levels.py — do support/resistance zones predict reversals on synthetics?

THE QUESTION
Every structural test in this repo says the tick process is memoryless: no autocorrelation
to lag 50, no volatility clustering (R^2 3e-5), entry/offset chi2 below its null. If
increments are independent then SUMS of increments are independent too, so aggregating to
4h bars cannot manufacture memory that is not in the ticks.

But we only ever probed to lag 50 TICKS. A 4h bar on a 2s symbol is 7,200 ticks. That is
three orders of magnitude beyond anything measured, so at that horizon it is genuinely
untested rather than proven dead.

The testable claim behind support/resistance is precise:

    price reverses at previously-touched levels MORE OFTEN than at arbitrary levels

That is falsifiable and needs no interpretation of what a "zone" is.

METHOD — and the controls are the whole point
  1. Find swing highs/lows on resampled bars (fractal: extreme of a 2k+1 window), exactly
     the way a chart marks them.
  2. Wait for price to RETURN to that level later, having left a buffer.
  3. Record whether it reverses (moves R away from the level in the opposite direction
     before penetrating it by R) or breaks through.
  4. CONTROL A — arbitrary levels: run the identical test on random price levels drawn from
     the same range. If real levels reverse no more than random ones, the level carried no
     information.
  5. CONTROL B — shuffled increments: rebuild a synthetic path from the SAME step
     distribution in random order. This destroys any time structure while preserving the
     distribution exactly. Any "reversal rate" surviving here is a property of the
     geometry, not of memory.

Control B matters most. On ANY random walk, a level that price has touched is a level price
is currently NEAR, so it will be re-touched and will sometimes reverse. That produces an
impressive-looking hit rate from nothing at all. Only the difference against the controls
counts.

  6. Convert the measured reversal rate into P&L at the real spread (16.56 points at
     ~48,000 = 0.035%), because a 55% reversal rate with a 1:1 target is not the same
     question as whether it beats costs.

Model-free throughout. Read-only.

Run: python3 sr_levels.py --symbol R_75
     python3 sr_levels.py --symbol R_75 --bar-ticks 7200 --ticks 4000000
"""
import argparse, math
import numpy as np
from deriv_api import DerivWS
from derivfetch import fetch_ticks, native_interval, wilson


def resample(v, k):
    n = len(v) // k
    if n < 50:
        return None
    b = v[:n * k].reshape(n, k)
    return dict(o=b[:, 0], h=b.max(1), l=b.min(1), c=b[:, -1])


def swings(h, l, w):
    """Fractal swing highs/lows: extreme of a 2w+1 window. Returns (index, level, kind)."""
    out = []
    for i in range(w, len(h) - w):
        if h[i] == h[i - w:i + w + 1].max():
            out.append((i, float(h[i]), "R"))
        if l[i] == l[i - w:i + w + 1].min():
            out.append((i, float(l[i]), "S"))
    return out


def test_levels(v, levels, R, cooldown, max_wait):
    """
    For each level, find the first return to it after a cooldown, then classify:
      reversal = price moves R away in the rejecting direction before penetrating by R
    Returns (n_tests, n_reversals).
    """
    n = tests = rev = 0
    for idx, lvl, kind in levels:
        start = idx + cooldown
        if start >= len(v):
            continue
        # find first touch of the level after the cooldown
        seg = v[start:start + max_wait]
        if len(seg) < 10:
            continue
        hit = np.flatnonzero(np.abs(seg - lvl) <= R * 0.2)
        if len(hit) == 0:
            continue
        j = start + int(hit[0])
        fwd = v[j:j + max_wait]
        if len(fwd) < 10:
            continue
        if kind == "R":      # resistance: reversal means going DOWN
            down = np.flatnonzero(fwd <= lvl - R)
            up = np.flatnonzero(fwd >= lvl + R)
        else:                # support: reversal means going UP
            down = np.flatnonzero(fwd >= lvl + R)
            up = np.flatnonzero(fwd <= lvl - R)
        d0 = down[0] if len(down) else 10 ** 9
        u0 = up[0] if len(up) else 10 ** 9
        if d0 == u0 == 10 ** 9:
            continue
        tests += 1
        if d0 < u0:
            rev += 1
    return tests, rev


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="R_75")
    ap.add_argument("--ticks", type=int, default=2000000)
    ap.add_argument("--bar-ticks", type=int, default=7200,
                    help="ticks per bar; 7200 = 4h on a 2s symbol")
    ap.add_argument("--swing-w", type=int, default=5,
                    help="fractal half-width in bars")
    ap.add_argument("--spread-frac", type=float, default=0.00035,
                    help="round-trip spread as a fraction of price (16.56/48000)")
    ap.add_argument("--out", default="../results/sr_levels.md")
    a = ap.parse_args()

    ws = DerivWS(token="")
    print(f"fetching {a.ticks} unique ticks of {a.symbol}...")
    t, p, pip = fetch_ticks(ws, a.symbol, a.ticks)
    ws.close()
    iv = native_interval(t)
    v = p.astype(float)
    days = (t[-1] - t[0]) / 86400
    print(f"  {len(v)} ticks, {iv}s cadence, {days:.1f} days, "
          f"price {v.min():.0f}-{v.max():.0f}")

    bars = resample(v, a.bar_ticks)
    if bars is None:
        print("  not enough data for that bar size"); return
    nb = len(bars["c"])
    print(f"  {nb} bars of {a.bar_ticks} ticks "
          f"({a.bar_ticks*iv/3600:.1f}h each)\n")

    lv = swings(bars["h"], bars["l"], a.swing_w)
    print(f"  {len(lv)} swing levels found (fractal width {2*a.swing_w+1} bars)")

    # R = typical bar range, so the test is scaled to the instrument
    R = float(np.median(bars["h"] - bars["l"]))
    print(f"  R (median bar range) = {R:.1f} points = {R/v.mean()*100:.3f}% of price")
    print(f"  spread = {a.spread_frac*100:.3f}% -> R/spread = "
          f"{(R/v.mean())/a.spread_frac:.1f}x\n")

    # map bar-index levels into tick space
    tick_levels = [(i * a.bar_ticks, lvl, k) for i, lvl, k in lv]
    cooldown = a.bar_ticks * 3
    max_wait = a.bar_ticks * 20

    print("=" * 72)
    print("REVERSAL RATE AT SWING LEVELS vs CONTROLS")
    print("=" * 72)

    n1, r1 = test_levels(v, tick_levels, R, cooldown, max_wait)
    p1 = r1 / n1 if n1 else float("nan")
    lo1, hi1 = wilson(r1, n1) if n1 else (0, 1)
    print(f"  {'real swing levels':28} n={n1:>6}  reversal {p1*100:>6.2f}%  "
          f"99% CI [{lo1*100:.2f}, {hi1*100:.2f}]")

    # CONTROL A: arbitrary levels at the same times
    rng = np.random.default_rng(0)
    rand_levels = []
    for i, lvl, k in lv:
        j = i * a.bar_ticks
        lo = max(0, j - a.bar_ticks * 10)
        window = v[lo:j + 1]
        if len(window) < 10:
            continue
        rand_levels.append((j, float(rng.uniform(window.min(), window.max())), k))
    n2, r2 = test_levels(v, rand_levels, R, cooldown, max_wait)
    p2 = r2 / n2 if n2 else float("nan")
    lo2, hi2 = wilson(r2, n2) if n2 else (0, 1)
    print(f"  {'CONTROL A: random levels':28} n={n2:>6}  reversal {p2*100:>6.2f}%  "
          f"99% CI [{lo2*100:.2f}, {hi2*100:.2f}]")

    # CONTROL B: same step distribution, shuffled order
    st = np.diff(v)
    sh = st.copy(); rng.shuffle(sh)
    vs = np.concatenate([[v[0]], v[0] + np.cumsum(sh)])
    bars_s = resample(vs, a.bar_ticks)
    lv_s = swings(bars_s["h"], bars_s["l"], a.swing_w)
    tick_levels_s = [(i * a.bar_ticks, lvl, k) for i, lvl, k in lv_s]
    n3, r3 = test_levels(vs, tick_levels_s, R, cooldown, max_wait)
    p3 = r3 / n3 if n3 else float("nan")
    lo3, hi3 = wilson(r3, n3) if n3 else (0, 1)
    print(f"  {'CONTROL B: shuffled walk':28} n={n3:>6}  reversal {p3*100:>6.2f}%  "
          f"99% CI [{lo3*100:.2f}, {hi3*100:.2f}]")

    print(f"\n  real - random   = {(p1-p2)*100:+.2f}pp")
    print(f"  real - shuffled = {(p1-p3)*100:+.2f}pp")
    se = math.sqrt(p1*(1-p1)/max(n1,1) + p3*(1-p3)/max(n3,1))
    print(f"  SE of the difference {se*100:.2f}pp -> t = "
          f"{(p1-p3)/se if se>0 else float('nan'):+.2f}")

    # ---- economics -------------------------------------------------------
    print(f"\n{'='*72}")
    print("DOES IT BEAT THE SPREAD?")
    print("=" * 72)
    cost = a.spread_frac * v.mean()
    print(f"  trade: enter at the level, target R={R:.1f}, stop R={R:.1f}")
    print(f"  round-trip cost {cost:.1f} points ({a.spread_frac*100:.3f}%)")
    be = 0.5 + cost / (2 * R)
    print(f"  breakeven reversal rate = {be*100:.2f}%")
    print(f"  measured {p1*100:.2f}%  ->  edge {(p1-be)*100:+.2f}pp")
    ev = p1 * R - (1 - p1) * R - cost
    print(f"  EV per trade {ev:+.2f} points = {ev/v.mean()*100:+.4f}% of notional")
    print(f"  99% lower bound on EV: {(lo1*R-(1-lo1)*R-cost):+.2f} points")

    print(f"\n{'='*72}")
    print("VERDICT")
    print("=" * 72)
    if n1 < 100:
        print("  too few tests — increase --ticks or reduce --bar-ticks")
    elif lo1 > be:
        print(f"  Swing levels reverse at {p1*100:.2f}%, above the {be*100:.2f}% breakeven")
        print("  even at the 99% lower bound. Check against the controls before")
        print("  believing it: if CONTROL B is similar, this is walk geometry, not memory.")
    else:
        print(f"  Reversal rate {p1*100:.2f}% vs breakeven {be*100:.2f}%.")
        if abs(p1 - p3) < 3 * se:
            print(f"  And it is statistically indistinguishable from a SHUFFLED walk")
            print(f"  ({p3*100:.2f}%), which has no time structure by construction.")
            print("  The levels carry no information: any apparent hit rate is the")
            print("  geometry of a random walk revisiting where it has been.")
        print("  Support/resistance does not predict reversals on this instrument.")

    with open(a.out, "w") as f:
        f.write(f"# Support/resistance test — {a.symbol}\n\n"
                f"{len(v)} ticks, {days:.1f} days, {nb} bars of {a.bar_ticks} ticks.\n\n"
                f"| set | n | reversal rate | 99% CI |\n|---|---|---|---|\n"
                f"| real swing levels | {n1} | {p1*100:.2f}% | [{lo1*100:.2f}, {hi1*100:.2f}] |\n"
                f"| random levels | {n2} | {p2*100:.2f}% | [{lo2*100:.2f}, {hi2*100:.2f}] |\n"
                f"| shuffled walk | {n3} | {p3*100:.2f}% | [{lo3*100:.2f}, {hi3*100:.2f}] |\n\n"
                f"breakeven {be*100:.2f}%, EV {ev:+.2f} points.\n")
    print(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
