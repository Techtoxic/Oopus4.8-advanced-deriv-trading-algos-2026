"""duration_surface.py — the 9/10 of the digit surface nobody has looked at.

THE OVERSIGHT
Every tool in this repo uses duration=1: sentinel_v2, adaptive_sentinel, empirical_pmf, the
offset tables, the walk-forward, payout_audit. But contracts_for says digits trade at
1t through 10t. Nine of the ten available durations have never been tested, on the one
contract family that ever produced an edge.

WHY IT SHOULD MATTER
Digit concentration is driven by sigma_pips. Over N ticks the cumulative move has effective
sigma ~ sigma*sqrt(N), so the concentration decays fast:

    JD100 (sigma_pips 3.88):  N=1 -> 0.103   N=3 -> 0.059   N=5 -> 0.046   N=10 -> 0.033

At N=1 the conditional digit pmf is strongly non-uniform. By N=10 it is nearly flat and the
contract is a coin toss.

THE TESTABLE ASYMMETRY
payout_audit only ever measured duration=1. So we do not know whether Deriv's payout varies
with duration. Two cases, both informative:

  - payout FLAT across N: a fixed price against a probability that provably varies with N
    means at least one duration is mispriced. Find which.
  - payout VARIES with N: they model it, and we learn that in one call rather than assuming.

METHOD
Model-free. For every (contract, barrier, duration) cell, backtest the payoff on real ticks
CONDITIONED ON THE ENTRY DIGIT — because that is how the strategy actually trades: observe
the current digit, then pick a contract. Marginal probabilities would wash the effect out
entirely, which is likely why nobody noticed this axis.

For each cell we report the best entry-digit-conditioned EV and which digit produces it.

Payouts come from proposals for the scan; payout_audit established these serve the pre-cut
grid (proposal 1.886 vs executed 1.8286, graduated lie reaching 25% on the tails), so any
candidate must be re-measured with --execute before it means anything.

Read-only unless --execute. Controls at N=1 must reproduce known results.

Run: python3 duration_surface.py --symbol JD100
     python3 duration_surface.py --symbol JD100 --execute
"""
import argparse, math, time
import numpy as np
from deriv_api import DerivWS

WIN = {
    "DIGITOVER":  lambda d, b: d > b,
    "DIGITUNDER": lambda d, b: d < b,
    "DIGITMATCH": lambda d, b: d == b,
    "DIGITDIFF":  lambda d, b: d != b,
}


def wilson(k, n, z=2.576):
    if n == 0:
        return 0.0, 1.0
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return c - h, c + h


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="JD100")
    ap.add_argument("--ticks", type=int, default=400000)
    ap.add_argument("--durations", type=int, nargs="+", default=list(range(1, 11)))
    ap.add_argument("--stake", type=float, default=1.0)
    ap.add_argument("--min-n", type=int, default=3000)
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--out", default="../results/duration_surface.md")
    a = ap.parse_args()

    ws = DerivWS(token="")
    print(f"fetching {a.ticks} ticks of {a.symbol}...")
    _, prices, pip = ws.history_paged(a.symbol, a.ticks, sleep=0.2)
    pip = int(pip)
    v = np.round(np.asarray(prices, dtype=float) * (10 ** pip)).astype(np.int64)
    dig = (v % 10).astype(np.int8)
    st = np.diff(v)
    sg = float(np.sqrt((st[np.abs(st) <= 20].astype(float) ** 2).mean()))
    print(f"got {len(v)} ticks, spot {prices[-1]}, sigma_pips {sg:.3f}\n")

    # ---- 1. does the payout vary with duration at all? -------------------
    print("=" * 78)
    print("1. DOES THE PAYOUT VARY WITH DURATION?")
    print("=" * 78)
    probe = [("DIGITOVER", "4"), ("DIGITUNDER", "5"), ("DIGITMATCH", "0"),
             ("DIGITDIFF", "0")]
    pay = {}
    print(f"{'contract':16}" + "".join(f"N={n}".rjust(9) for n in a.durations))
    for ct, bar in probe:
        row = []
        for N in a.durations:
            r = ws.call({"proposal": 1, "amount": a.stake, "basis": "stake",
                         "contract_type": ct, "currency": "USD",
                         "underlying_symbol": a.symbol, "duration": N,
                         "duration_unit": "t", "barrier": bar})
            p = float(r["proposal"]["payout"]) / a.stake if "proposal" in r else None
            pay[(ct, bar, N)] = p
            row.append(f"{p:.4f}" if p else "--")
            time.sleep(0.1)
        print(f"{ct+bar:16}" + "".join(s.rjust(9) for s in row))

    vals = [pay[(ct, b, N)] for ct, b in probe for N in a.durations
            if pay.get((ct, b, N))]
    flat = {}
    for ct, b in probe:
        ps = [pay[(ct, b, N)] for N in a.durations if pay.get((ct, b, N))]
        flat[(ct, b)] = (max(ps) - min(ps) < 1e-6) if ps else None
    print()
    for k, f in flat.items():
        print(f"  {k[0]+k[1]:16} {'FLAT across duration' if f else 'varies with duration'}")
    if any(flat.values()):
        print("\n  A flat payout against a probability that provably varies with N means")
        print("  at least one duration is mispriced. Section 2 finds which.")

    # ---- 2. entry-digit-conditioned EV by duration -----------------------
    print(f"\n{'='*78}")
    print("2. BEST ENTRY-DIGIT-CONDITIONED EV, BY CONTRACT AND DURATION")
    print("=" * 78)
    print("(marginal probabilities wash this out — the strategy conditions on entry digit)")
    print()

    contracts = ([("DIGITOVER", b) for b in range(9)]
                 + [("DIGITUNDER", b) for b in range(1, 10)]
                 + [("DIGITMATCH", b) for b in range(10)]
                 + [("DIGITDIFF", b) for b in range(10)])

    best_cells = []
    print(f"{'contract':14}" + "".join(f"N={n}".rjust(8) for n in a.durations))
    print("-" * (14 + 8 * len(a.durations)))

    for ct, bar in contracts:
        # fetch payout per duration for this barrier
        payr = {}
        for N in a.durations:
            key = (ct, str(bar), N)
            if key not in pay:
                r = ws.call({"proposal": 1, "amount": a.stake, "basis": "stake",
                             "contract_type": ct, "currency": "USD",
                             "underlying_symbol": a.symbol, "duration": N,
                             "duration_unit": "t", "barrier": str(bar)})
                pay[key] = float(r["proposal"]["payout"]) / a.stake if "proposal" in r else None
                time.sleep(0.08)
            payr[N] = pay[key]

        cells = []
        for N in a.durations:
            M = payr.get(N)
            if not M:
                cells.append("   --")
                continue
            fn = WIN[ct]
            best = None
            for c in range(10):
                idx = np.where(dig[:-N] == c)[0]
                if len(idx) < a.min_n:
                    continue
                out = dig[idx + N]
                k = int(np.sum([fn(int(x), bar) for x in out]))
                n = len(out)
                p = k / n
                lo, _ = wilson(k, n)
                ev, evlo = p * M - 1, lo * M - 1
                if best is None or ev > best[0]:
                    best = (ev, evlo, c, n, p)
            if best is None:
                cells.append("   --")
                continue
            cells.append(f"{best[0]*100:+6.2f}")
            if best[1] > 0.005:
                best_cells.append(dict(ct=ct, bar=bar, N=N, ev=best[0], evlo=best[1],
                                       digit=best[2], n=best[3], p=best[4], M=M))
        row = "".join(c.rjust(8) for c in cells)
        if any(x.strip() not in ("--", "") and x.strip().startswith("+") for x in cells):
            print(f"{ct[5:]+str(bar):14}{row}")

    # ---- 3. candidates ---------------------------------------------------
    print(f"\n{'='*78}")
    print("3. CANDIDATES (Wilson-99 lower bound on EV > +0.5%)")
    print("=" * 78)
    best_cells.sort(key=lambda r: -r["evlo"])
    if not best_cells:
        print("  none. no (contract, barrier, duration, entry-digit) cell clears the bar.")
    for r in best_cells[:25]:
        print(f"  {r['ct'][5:]+str(r['bar']):10} N={r['N']:<3} entry={r['digit']} "
              f"n={r['n']:>7} p={r['p']:.5f} payout={r['M']:.4f} "
              f"EV={r['ev']*100:+.2f}% 99%low={r['evlo']*100:+.2f}%")
    if best_cells:
        print("\n  payouts are PROPOSALS and serve the pre-cut grid — re-run with --execute.")

    # ---- 4. execute ------------------------------------------------------
    if a.execute and best_cells:
        tr = DerivWS()
        acct = tr.account or {}
        if acct.get("account_type") != "demo":
            print(f"\nNOT demo ({acct.get('account_type')}) — skipping.")
        else:
            print(f"\n{'='*78}")
            print(f"4. EXECUTED PAYOUTS [demo {acct.get('account_id')}]")
            print("=" * 78)
            for r in best_cells[:12]:
                b = tr.call({"buy": 1, "price": round(a.stake * 30, 2), "parameters":
                             dict(amount=a.stake, basis="stake", contract_type=r["ct"],
                                  currency="USD", underlying_symbol=a.symbol,
                                  duration=r["N"], duration_unit="t",
                                  barrier=str(r["bar"]))})
                if "buy" in b:
                    M = float(b["buy"]["payout"]) / float(b["buy"].get("buy_price") or a.stake)
                    ev, evlo = r["p"] * M - 1, None
                    lo, _ = wilson(int(r["p"] * r["n"]), r["n"])
                    evlo = lo * M - 1
                    tag = "SURVIVES" if evlo > 0 else "dies on real fill"
                    print(f"  {r['ct'][5:]+str(r['bar']):10} N={r['N']:<3} entry={r['digit']} "
                          f"proposal {r['M']:.4f} -> executed {M:.4f}  "
                          f"EV {ev*100:+.2f}% 99%low {evlo*100:+.2f}%  {tag}")
                else:
                    print(f"  {r['ct'][5:]+str(r['bar']):10} N={r['N']:<3} buy error: "
                          f"{b.get('error', {}).get('message', '')[:40]}")
                time.sleep(0.35)

    md = [f"# Duration surface — {a.symbol}\n\n",
          f"{len(v)} ticks, spot {prices[-1]}, sigma_pips {sg:.3f}.\n\n",
          "Every prior tool in this repo used duration=1; digits trade 1t-10t.\n\n",
          "| contract | N | entry digit | n | p | payout | EV | 99% low |\n",
          "|---|---:|---:|---:|---:|---:|---:|---:|\n"]
    for r in best_cells[:40]:
        md.append(f"| {r['ct']}{r['bar']} | {r['N']} | {r['digit']} | {r['n']} | "
                  f"{r['p']:.5f} | {r['M']:.4f} | {r['ev']*100:+.2f}% | "
                  f"{r['evlo']*100:+.2f}% |\n")
    open(a.out, "w").write("".join(md))
    print(f"\nwrote {a.out}")
    ws.close()


if __name__ == "__main__":
    main()
