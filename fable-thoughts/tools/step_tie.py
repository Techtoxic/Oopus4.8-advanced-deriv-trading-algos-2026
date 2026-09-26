"""step_tie.py — is the Rise+Fall hedge on Step Index actually +EV?

THE SETUP
Step 100 Index, Rise/Fall, duration 15 SECONDS, "Allow equals" OFF, stake $1 each side.
Screenshot shows payout 2.46 per leg (the +1.46 on the chart is PROFIT, not payout).

    $2 staked, one leg wins  -> $2.46 back = +$0.46  (+23%)
    tie (exit == entry)      -> both lose = -$2.00

Breakeven tie rate = (M-2)/M = 18.70%.
Deriv's implied P(rise) = 1/2.46 = 40.65%, so they are pricing P(tie) at ~18.70%.
That is not generosity — the whole 2.46 exists BECAUSE they expect ties.

WHY IT IS NOT OBVIOUS
Step indices move a FIXED step every tick. Over an ODD number of ticks parity says the
price CANNOT return to its starting value, so P(tie) = 0 exactly. opus measured stpRNG:
P(tie) = 0.000 on odd tick durations, 0.498 on even.

But this contract is priced in SECONDS, not ticks. If the number of ticks inside 15s is
always odd, P(tie) = 0 and the hedge is +23% per round — free money. If the tick count
varies, some rounds land even and ties happen. Deriv pricing ~18.7% says they expect a lot.

So: measure the tick rate, measure the tick count inside each duration, and measure the
realised tie rate directly.

WHAT THIS MEASURES
  1. Native tick interval and step size on the symbol.
  2. Distribution of tick COUNT inside each candidate duration (odd vs even).
  3. Realised P(up)/P(tie)/P(down) over that duration, from real ticks.
  4. Hedge EV at the quoted payout, with a Wilson bound on the tie rate.
  5. The same across durations, since parity predicts alternation.

Read-only. Places no trades.

Run: python3 step_tie.py
     python3 step_tie.py --symbol stpRNG --durations 5 10 15 20 25 30
"""
import argparse, math
import numpy as np
from deriv_api import DerivWS
from derivfetch import fetch_ticks, native_interval, wilson


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="stpRNG")
    ap.add_argument("--ticks", type=int, default=300000)
    ap.add_argument("--durations", type=int, nargs="+",
                    default=[5, 10, 15, 20, 25, 30, 60])
    ap.add_argument("--payout", type=float, default=2.46,
                    help="quoted payout per leg (screenshot: 2.46 at 15s)")
    ap.add_argument("--out", default="../results/step_tie.md")
    a = ap.parse_args()

    ws = DerivWS(token="")
    print(f"fetching {a.ticks} unique ticks of {a.symbol}...")
    t, p, pip = fetch_ticks(ws, a.symbol, a.ticks)
    ws.close()

    v = np.round(p * (10 ** pip)).astype(np.int64)
    iv = native_interval(t)
    steps = np.diff(v)
    nz = steps[steps != 0]
    print(f"\n  native tick interval : {iv}s")
    print(f"  pip size             : 1e-{pip}")
    if len(nz):
        u, c = np.unique(np.abs(nz), return_counts=True)
        top = sorted(zip(c, u), reverse=True)[:4]
        print(f"  |step| distribution  : "
              + ", ".join(f"{int(uu)}pips {cc/len(steps)*100:.1f}%" for cc, uu in top))
    print(f"  zero steps           : {(steps==0).mean()*100:.3f}% "
          f"({'moves every tick' if (steps==0).mean() < 0.01 else 'CAN stay flat'})")
    print(f"  ticks in 15s         : {15/iv:.1f}")

    # index by epoch for exact time lookups
    pos = {int(e): i for i, e in enumerate(t)}

    print(f"\n{'='*84}")
    print(f"REALISED OUTCOMES BY DURATION  (payout {a.payout} per leg, "
          f"breakeven tie {(a.payout-2)/a.payout*100:.2f}%)")
    print("=" * 84)
    print(f"{'dur(s)':>7}{'ticks':>7}{'par':>5}{'n':>9}{'P(up)':>9}{'P(tie)':>9}"
          f"{'P(dn)':>9}{'hedge EV':>11}{'99% CI on EV':>22}")

    rows = []
    for D in a.durations:
        ups = ties = dns = 0
        for i, e in enumerate(t):
            j = pos.get(int(e) + D)
            if j is None:
                continue
            d = v[j] - v[i]
            if d > 0:
                ups += 1
            elif d < 0:
                dns += 1
            else:
                ties += 1
        n = ups + ties + dns
        if n < 2000:
            continue
        pt = ties / n
        ev = (a.payout * (1 - pt) - 2) / 2
        lo, hi = wilson(ties, n)
        ev_lo = (a.payout * (1 - hi) - 2) / 2      # more ties = worse
        ev_hi = (a.payout * (1 - lo) - 2) / 2
        nticks = D / iv
        par = "odd" if abs(nticks - round(nticks)) < 1e-9 and int(round(nticks)) % 2 else \
              ("even" if abs(nticks - round(nticks)) < 1e-9 else "frac")
        rows.append((D, nticks, par, n, ups/n, pt, dns/n, ev, ev_lo, ev_hi))
        print(f"{D:>7}{nticks:>7.1f}{par:>5}{n:>9}{ups/n:>9.5f}{pt:>9.5f}"
              f"{dns/n:>9.5f}{ev*100:>10.2f}%   [{ev_lo*100:>+7.2f}%,{ev_hi*100:>+7.2f}%]")

    print(f"\n{'='*84}")
    print("VERDICT")
    print("=" * 84)
    good = [r for r in rows if r[8] > 0]      # 99% lower bound above zero
    if good:
        print("  DURATIONS WITH A POSITIVE 99% LOWER BOUND:")
        for D, nt, par, n, pu, pt, pd, ev, lo, hi in good:
            print(f"    {D}s ({nt:.1f} ticks, {par}): P(tie)={pt:.5f}, "
                  f"EV {ev*100:+.2f}% [99% low {lo*100:+.2f}%]")
        print()
        print("  NEXT, BEFORE BELIEVING IT:")
        print("   1. the payout is per-duration — re-quote at $10 stake for EACH duration")
        print("      that looks positive, from an EXECUTED buy, not the displayed payout")
        print("   2. the two legs fill on different ticks; leg skew can turn a would-be")
        print("      tie into a win or a loss. paper-trade the PAIR, not the legs")
        print("   3. check whether Deriv caps opposing positions on one symbol")
    else:
        print("  No duration has a positive 99% lower bound.")
        print("  The 2.46 payout is priced for the tie rate, which is what it looks like:")
        print(f"  implied P(tie) = 1 - 2/{a.payout} = {(1-2/a.payout)*100:.2f}%.")
        if rows:
            b = max(rows, key=lambda r: r[8])
            print(f"  closest: {b[0]}s at P(tie)={b[5]:.5f}, EV {b[7]*100:+.2f}% "
                  f"[99% low {b[8]*100:+.2f}%]")

    if rows:
        odd = [r for r in rows if r[2] == "odd"]
        even = [r for r in rows if r[2] == "even"]
        if odd and even:
            print(f"\n  parity check: mean P(tie) on ODD tick counts "
                  f"{np.mean([r[5] for r in odd]):.5f}, EVEN "
                  f"{np.mean([r[5] for r in even]):.5f}")
            print("  (a fixed-step process cannot tie on an odd number of steps)")

    open(a.out, "w").write(
        f"# Step tie rates — {a.symbol}\n\ninterval {iv}s, payout {a.payout}, "
        f"breakeven tie {(a.payout-2)/a.payout*100:.2f}%\n\n"
        "| dur | ticks | parity | n | P(up) | P(tie) | P(dn) | EV | 99% low |\n"
        "|---|---|---|---|---|---|---|---|---|\n"
        + "".join(f"| {D} | {nt:.1f} | {par} | {n} | {pu:.5f} | {pt:.5f} | {pd:.5f} | "
                 f"{ev*100:+.2f}% | {lo*100:+.2f}% |\n"
                 for D, nt, par, n, pu, pt, pd, ev, lo, hi in rows))
    print(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
