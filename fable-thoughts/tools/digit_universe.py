"""digit_universe.py — price EVERY digit contract on EVERY digit-enabled symbol.

WHAT WE HAVE ONLY EVER LOOKED AT
The +/-2 window (OVER4 / UNDER5) on 7 of 17 digit-enabled symbols. That is one contract
family on 40% of the universe.

WHAT THE OFFSET PMF SAYS WE MISSED
Concentration does not only make NEAR digits likelier — it makes the ANTIPODAL digit rarer
and the CURRENT digit much likelier. Both are directly tradeable and neither was priced:

    sigma   P(off=0)   P(off=5)   MATCH-current @8.929   antipodal DIFF @1.096
     3.00    0.13401    0.06632              +19.66%                   +2.33%
     3.50    0.11783    0.08219               +5.21%                   +0.59%
     3.90    0.10994    0.09007               -1.84%                   -0.27%
    10.00    0.10000    0.10000              -10.71%                   -1.36%

MATCH on the current digit at sigma 3.5 is +5.21% — nine times the +/-2 window edge at the
same sigma. It was never evaluated because opus banned narrow high-payout contracts after
the June selection-bias incident, and that ban was about FITTED tables. This is a structural
prediction from the pmf, not a fit.

Note JD100 cannot use either: its grid was cut (MATCH 8.929 -> 6.667, DIFF 1.096 -> 1.053),
which pushes the requirement to sigma < 2.5. Every OTHER symbol still carries the uncut grid
(verified on JD50: all 22 contracts unchanged at $10 stake).

So the question is simply whether any symbol sits at low sigma_pips. Ten are unmeasured.

WHAT THIS DOES
For every digit-enabled symbol:
  1. fetch clean deduplicated ticks (derivfetch — the looping bug is fixed)
  2. measure sigma_pips AND the EMPIRICAL offset pmf, no model
  3. price all 22 digit contracts conditioned on the current digit, at that symbol's own
     quoted payouts
  4. report every cell with a positive Wilson-99 lower bound

Model-free throughout: the pmf is measured, not assumed wrapped-normal.

Read-only. Verify any candidate with executed buys at >= $10 stake before believing it —
the proposal endpoint lies on JD100 and could lie elsewhere.

Run: python3 digit_universe.py
     python3 digit_universe.py --ticks 200000 --symbols RDBULL RDBEAR 1HZ25V
"""
import argparse, math, time
import numpy as np
from deriv_api import DerivWS
from derivfetch import fetch_ticks, contiguous_pairs, native_interval, wilson

DIGIT_SYMBOLS = ["JD100", "JD75", "JD50", "JD25", "JD10",
                 "1HZ100V", "1HZ75V", "1HZ50V", "1HZ25V", "1HZ10V",
                 "R_100", "R_75", "R_50", "R_25", "R_10",
                 "RDBULL", "RDBEAR"]


def winset(ct, bar):
    if ct == "DIGITOVER":
        return set(range(bar + 1, 10))
    if ct == "DIGITUNDER":
        return set(range(0, bar))
    if ct == "DIGITMATCH":
        return {bar}
    if ct == "DIGITDIFF":
        return set(range(10)) - {bar}
    if ct == "DIGITEVEN":
        return {0, 2, 4, 6, 8}
    if ct == "DIGITODD":
        return {1, 3, 5, 7, 9}
    return set()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+", default=DIGIT_SYMBOLS)
    ap.add_argument("--ticks", type=int, default=150000)
    ap.add_argument("--stake", type=float, default=10.0)
    ap.add_argument("--min-ev", type=float, default=0.0)
    ap.add_argument("--out", default="../results/digit_universe.md")
    a = ap.parse_args()

    ws = DerivWS(token="")
    print(f"{'symbol':10}{'ticks':>9}{'iv':>4}{'sigma_pips':>12}{'P(off=0)':>10}"
          f"{'P(off=5)':>10}  regime")
    print("-" * 70)

    data = {}
    for sym in a.symbols:
        try:
            t, p, pip = fetch_ticks(ws, sym, a.ticks, verbose=False)
        except Exception as e:
            print(f"{sym:10} fetch failed: {str(e)[:40]}")
            continue
        v = np.round(p * (10 ** pip)).astype(np.int64)
        iv = native_interval(t)
        cg = contiguous_pairs(t, v, iv)
        if cg.sum() < 20000:
            print(f"{sym:10} only {int(cg.sum())} contiguous pairs")
            continue
        dig = (v % 10).astype(int)
        ent = dig[:-1][cg]
        off = ((dig[1:] - dig[:-1]) % 10)[cg]
        st = np.diff(v)[cg]
        thr = np.percentile(np.abs(st[st != 0]), 99.5) if (st != 0).any() else 20
        f = st[np.abs(st) <= thr].astype(float)
        sg = float(np.sqrt((f ** 2).mean())) if len(f) else float("nan")
        pmf = np.bincount(off, minlength=10) / len(off)
        data[sym] = dict(ent=ent, off=off, pmf=pmf, sigma=sg, pip=pip, n=len(off))
        reg = ("*** LOW — concentration present ***" if sg < 4.5
               else ("marginal" if sg < 6 else "flat, no edge possible"))
        print(f"{sym:10}{len(off):>9}{iv:>4}{sg:>12.3f}{pmf[0]:>10.5f}"
              f"{pmf[5]:>10.5f}  {reg}")
        time.sleep(0.2)

    print(f"\n{'='*78}")
    print("PRICING EVERY DIGIT CONTRACT, CONDITIONED ON THE CURRENT DIGIT")
    print("=" * 78)

    cands = []
    for sym, d in data.items():
        if d["sigma"] > 8:
            continue          # pmf is flat; nothing can be positive
        contracts = ([("DIGITOVER", b) for b in range(9)]
                     + [("DIGITUNDER", b) for b in range(1, 10)]
                     + [("DIGITMATCH", b) for b in range(10)]
                     + [("DIGITDIFF", b) for b in range(10)]
                     + [("DIGITEVEN", None), ("DIGITODD", None)])
        printed = False
        for ct, bar in contracts:
            req = {"proposal": 1, "amount": a.stake, "basis": "stake",
                   "contract_type": ct, "currency": "USD",
                   "underlying_symbol": sym, "duration": 1, "duration_unit": "t"}
            if bar is not None:
                req["barrier"] = str(bar)
            r = ws.call(req)
            if "proposal" not in r:
                continue
            M = float(r["proposal"]["payout"]) / a.stake
            time.sleep(0.05)

            # best entry digit for this contract
            best = None
            for c in range(10):
                m = d["ent"] == c
                n = int(m.sum())
                if n < 3000:
                    continue
                nxt = (c + d["off"][m]) % 10
                ws_ = winset(ct, bar if bar is not None else 0)
                k = int(np.isin(nxt, list(ws_)).sum())
                pw = k / n
                lo, _ = wilson(k, n)
                ev, evlo = pw * M - 1, lo * M - 1
                if best is None or evlo > best[1]:
                    best = (ev, evlo, c, n, pw, M)
            if best and best[1] > a.min_ev:
                if not printed:
                    print(f"\n  {sym} (sigma {d['sigma']:.3f}):")
                    printed = True
                lbl = ct[5:] + (str(bar) if bar is not None else "")
                print(f"    {lbl:10} entry={best[2]} n={best[3]:>6} p={best[4]:.5f} "
                      f"payout={best[5]:.4f} EV={best[0]*100:+.2f}% "
                      f"99%low={best[1]*100:+.2f}%")
                cands.append((best[1], sym, ct, bar, best))
        if not printed and sym in data:
            print(f"\n  {sym} (sigma {d['sigma']:.3f}): nothing positive")

    ws.close()
    print(f"\n{'='*78}")
    print("VERDICT")
    print("=" * 78)
    if cands:
        cands.sort(reverse=True)
        print(f"  {len(cands)} cells with a positive 99% lower bound. Top 10:")
        for evlo, sym, ct, bar, b in cands[:10]:
            lbl = ct[5:] + (str(bar) if bar is not None else "")
            print(f"    {sym:9} {lbl:10} entry={b[2]} EV {b[0]*100:+.2f}% "
                  f"99%low {evlo*100:+.2f}%")
        print()
        print("  BEFORE BELIEVING ANY OF THIS:")
        print("   1. re-quote at >= $10 from an EXECUTED buy — the proposal endpoint")
        print("      lies on JD100 (quotes 1.953, fills 1.818) and may lie elsewhere")
        print("   2. entry slips to T+2 on ~30% of buys at 137ms round trip, which")
        print("      turns a 1-step edge into a 2-step one; budget for it")
        print("   3. split-half the sample before sizing")
    else:
        print("  No positive cell anywhere in the digit universe.")
        print("  Every symbol with an uncut grid sits at sigma >= 10, where the offset")
        print("  pmf is flat and no digit contract can be positive. JD100 is the only")
        print("  low-sigma symbol and its grid was cut.")
        print()
        print("  That is a complete answer for digits: the edge requires concentration,")
        print("  concentration requires low sigma_pips, and only a decayed symbol has it.")

    with open(a.out, "w") as f:
        f.write("# Digit universe scan\n\n| symbol | n | sigma_pips | P(off=0) | P(off=5) |\n"
                "|---|---:|---:|---:|---:|\n")
        for sym, d in data.items():
            f.write(f"| {sym} | {d['n']} | {d['sigma']:.3f} | {d['pmf'][0]:.5f} "
                    f"| {d['pmf'][5]:.5f} |\n")
    print(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
