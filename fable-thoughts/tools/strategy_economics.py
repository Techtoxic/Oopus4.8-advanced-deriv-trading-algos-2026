"""strategy_economics.py — is this edge worth trading? Answer BEFORE writing any code.

CONTEXT
The whole session established WHETHER an edge exists. It never asked what it is worth.
Current state:

    p(sigma) validated as the wrapped normal across four symbols, sigma 3.7-161,
      per-symbol residual spread 0.00181 (universal_curve.py)
    JD100 executed payout 1.818 on OVER4/UNDER5 (payout_audit at $10 stake)
    breakeven 55.01%; crossing sigma 3.48; ~12 days out at -0.67%/day
    JD100 is the only live symbol — every other one is years away or has positive drag

At +1-2% EV the Kelly fraction near breakeven is close to zero, so "there is an edge" and
"this is worth trading" are genuinely different questions.

WHAT THIS COMPUTES
  1. EV per trade vs sigma, at the CORRECT payout, using the validated curve plus JD100's
     measured -0.00124 offset from it.
  2. Kelly fraction and half-Kelly stake. Near breakeven Kelly collapses, which caps
     returns regardless of how many trades you place.
  3. Monte Carlo of the equity path: max drawdown distribution, probability of ruin,
     and the chance of being DOWN after N trades even when the edge is real. This is the
     number that decides whether it is psychologically and operationally survivable.
  4. Time to know it is working — trades needed for t=2 separation from zero.
  5. $/hour at a realistic trade rate, against the operational cost of running it.

Assumes 1 trade per tick maximum on a 1s symbol, but takes --rate to scale down since the
sentinel only fires on qualifying setups.

Pure computation. No API, no trades.

Run: python3 strategy_economics.py
     python3 strategy_economics.py --sigma 3.3 --bankroll 1000 --rate 0.25
"""
import argparse, math
import numpy as np

M = 1.818          # JD100 executed OVER4/UNDER5, $10-stake audit
BE = 1 / M
JD100_OFFSET = -0.00124   # measured p runs below the wrapped normal on JD100


def wn_win5(sigma):
    tot = sum(sum(math.exp(-0.5 * ((d + 10 * k) / sigma) ** 2) for k in range(-6, 7))
              for d in (-2, -1, 0, 1, 2))
    nrm = sum(sum(math.exp(-0.5 * ((d + 10 * k) / sigma) ** 2) for k in range(-6, 7))
              for d in range(10))
    return tot / nrm


def p_at(sigma):
    return wn_win5(sigma) + JD100_OFFSET


def kelly(p, b):
    """b = net odds (payout - 1). f* = (p*b - q)/b."""
    q = 1 - p
    f = (p * b - q) / b
    return max(f, 0.0)


def simulate_fixed(p, stake, n_trades, n_paths=8000, seed=0, chunk=500):
    """
    FIXED-stake equity path — what you would actually trade. Kelly compounding over
    20k+ trades produces astronomically large numbers (1e48) that assume infinite
    divisibility and no stake caps, so it tells you nothing useful.
    Returns profit in dollars: (final, maxdd_dollars, p_down).
    """
    rng = np.random.default_rng(seed)
    win_amt = stake * (M - 1)
    finals, maxdds = [], []
    for start in range(0, n_paths, chunk):
        m = min(chunk, n_paths - start)
        wins = rng.random((m, n_trades)) < p
        eq = np.cumsum(np.where(wins, win_amt, -stake), axis=1)
        finals.append(eq[:, -1])
        rmax = np.maximum.accumulate(eq, axis=1)
        maxdds.append((rmax - eq).max(axis=1))
        del wins, eq, rmax
    f = np.concatenate(finals)
    return f, np.concatenate(maxdds), float((f < 0).mean())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sigma", type=float, default=None,
                    help="evaluate a single sigma; default sweeps the relevant range")
    ap.add_argument("--bankroll", type=float, default=1000.0)
    ap.add_argument("--rate", type=float, default=0.25,
                    help="fraction of ticks that produce a qualifying trade")
    ap.add_argument("--kelly-frac", type=float, default=0.5,
                    help="fraction of full Kelly to bet (0.5 = half Kelly)")
    ap.add_argument("--hours", type=float, default=24.0)
    a = ap.parse_args()

    print(f"payout {M} (JD100 executed), breakeven {BE*100:.2f}%")
    print(f"p(sigma) = wrapped normal {JD100_OFFSET:+.5f} (JD100 measured offset)\n")

    # ---- 1. EV and Kelly vs sigma ---------------------------------------
    print("=" * 78)
    print("1. EV AND KELLY vs SIGMA")
    print("=" * 78)
    print(f"{'sigma':>7}{'p':>9}{'vs BE':>10}{'EV/trade':>11}{'Kelly f':>10}"
          f"{'half-K stake':>14}{'days out':>10}")
    sigmas = ([a.sigma] if a.sigma else
              [3.60, 3.50, 3.48, 3.40, 3.30, 3.20, 3.10, 3.00])
    cur, drag = 3.76, -6.71e-3
    rows = []
    for s in sigmas:
        p = p_at(s)
        ev = p * M - 1
        f = kelly(p, M - 1)
        d = math.log(s / cur) / drag if s < cur else 0
        rows.append((s, p, ev, f, d))
        print(f"{s:>7.2f}{p:>9.5f}{(p-BE)*100:>+9.3f}pp{ev*100:>10.2f}%"
              f"{f:>10.4f}{f*a.kelly_frac*a.bankroll:>13.2f}$"
              f"{d:>10.0f}")

    print(f"\n  Kelly collapses to zero at breakeven. Even at sigma 3.30 the full-Kelly")
    print(f"  stake is only {kelly(p_at(3.30), M-1)*100:.1f}% of bankroll.")

    # ---- 2. risk of the equity path -------------------------------------
    print(f"\n{'='*78}")
    print(f"2. EQUITY PATH — {a.kelly_frac:.0%} Kelly, {a.hours:.0f}h of trading")
    print("=" * 78)
    n_per_hour = 3600 * a.rate
    n_trades = int(n_per_hour * a.hours)
    print(f"  {a.rate:.0%} of ticks qualify -> {n_per_hour:.0f} trades/hour, "
          f"{n_trades} over {a.hours:.0f}h\n")
    stake = 2.0
    print(f"  fixed ${stake:.2f} stake (Kelly compounding over 20k trades gives 1e48 —")
    print(f"  meaningless without stake caps). profit in dollars:\n")
    print(f"{'sigma':>7}{'EV/trade':>10}{'median P&L':>12}{'5th pct':>11}{'95th pct':>11}"
          f"{'med maxDD':>11}{'worst 5% DD':>13}{'P(down)':>9}")
    for s, p, ev, f, d in rows:
        if ev <= 0:
            print(f"{s:>7.2f}{ev*100:>9.2f}%   -- negative EV, do not trade")
            continue
        final, maxdd, pdown = simulate_fixed(p, stake, n_trades)
        print(f"{s:>7.2f}{ev*100:>9.2f}%{np.median(final):>11.0f}$"
              f"{np.percentile(final,5):>10.0f}${np.percentile(final,95):>10.0f}$"
              f"{np.median(maxdd):>10.0f}${np.percentile(maxdd,95):>12.0f}$"
              f"{pdown*100:>8.1f}%")

    # ---- 3. how long to know it works -----------------------------------
    print(f"\n{'='*78}")
    print("3. HOW LONG BEFORE YOU KNOW IT IS WORKING?")
    print("=" * 78)
    print(f"{'sigma':>7}{'EV/trade':>11}{'trades for t=2':>17}{'hours':>9}{'days':>8}")
    for s, p, ev, f, d in rows:
        if ev <= 0:
            continue
        var = p * (M - 1) ** 2 + (1 - p) * 1 - ev ** 2
        n = (2 * math.sqrt(var) / ev) ** 2
        print(f"{s:>7.2f}{ev*100:>10.2f}%{n:>17,.0f}{n/n_per_hour:>9.1f}"
              f"{n/n_per_hour/24:>8.1f}")

    # ---- 4. is it worth the operating cost? ------------------------------
    print(f"\n{'='*78}")
    print("4. $/HOUR AT FIXED STAKE (not compounding)")
    print("=" * 78)
    print(f"{'sigma':>7}{'EV/trade':>11}{'$1 stake':>11}{'$2 stake':>11}{'$5 stake':>11}")
    for s, p, ev, f, d in rows:
        if ev <= 0:
            continue
        print(f"{s:>7.2f}{ev*100:>10.2f}%"
              + "".join(f"{ev*st*n_per_hour:>10.2f}$" for st in (1, 2, 5)))
    print(f"\n  EC2 t3.small runs ~$15/month = $0.02/hour. Not the binding cost.")
    print(f"  The binding cost is that a losing streak is indistinguishable from a")
    print(f"  broken edge for the number of trades in section 3.")

    print(f"\n{'='*78}")
    print("READ THIS BEFORE DEPLOYING")
    print("=" * 78)
    print("  - Kelly near breakeven is ~0. The edge only becomes sizeable well below")
    print("    sigma 3.4, which is weeks past the crossing point, not days.")
    print("  - Section 2's P(down) is the probability of being underwater after a full")
    print("    day of trading EVEN WITH A REAL EDGE. If that is above ~35%, expect to")
    print("    doubt the strategy while it is working.")
    print("  - Section 3 is the honest answer to 'is it working': if t=2 needs more")
    print("    trades than you will place in a week, live P&L cannot tell you.")
    print("  - Deriv repriced JD100 once already. The window may close before section 3")
    print("    completes, which is the real risk — not drawdown.")


if __name__ == "__main__":
    main()
