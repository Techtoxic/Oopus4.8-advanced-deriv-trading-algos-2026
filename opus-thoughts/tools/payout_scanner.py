"""H3: full payout-surface scan for every digit-enabled symbol.
- every contract (MATCH/DIFF/OVER/UNDER/EVEN/ODD) x barrier x durations {1,10}
- implied probability 1/M vs fair probability
- exact Dutch-book LP per symbol x duration (superreplicate $1 across all 10 digits)
- duration & cross-symbol dominance tables
Run: python3 payout_scanner.py   (no token needed)
Out: ../results/payout_surface.json + payout_surface.md
"""
import json, time, itertools
from deriv_api import DerivWS

STAKE = 10.0
DURATIONS = [1, 10]
CONTRACTS = (
    [("DIGITMATCH", d, 0.1) for d in range(10)] +
    [("DIGITDIFF", d, 0.9) for d in range(10)] +
    [("DIGITOVER", k, (9 - k) / 10) for k in range(9)] +
    [("DIGITUNDER", k, k / 10) for k in range(1, 10)] +
    [("DIGITEVEN", None, 0.5), ("DIGITODD", None, 0.5)]
)

def wins(ctype, barrier, d):
    if ctype == "DIGITMATCH": return d == barrier
    if ctype == "DIGITDIFF": return d != barrier
    if ctype == "DIGITOVER": return d > barrier
    if ctype == "DIGITUNDER": return d < barrier
    if ctype == "DIGITEVEN": return d % 2 == 0
    if ctype == "DIGITODD": return d % 2 == 1
    raise ValueError(ctype)

def dutch_lp(instruments):
    """min cost portfolio with payoff >= 1 for every digit. instruments: [(label, M, winset)]
    Exact LP via scipy if available, else greedy pair/triple scan."""
    try:
        from scipy.optimize import linprog
        import numpy as np
        A = np.zeros((10, len(instruments)))
        for j, (_, M, ws_) in enumerate(instruments):
            for d in ws_:
                A[d, j] = M
        res = linprog(c=[1.0] * len(instruments), A_ub=-A, b_ub=[-1.0] * 10, method="highs")
        if res.success:
            picks = [(instruments[j][0], round(x, 4)) for j, x in enumerate(res.x) if x > 1e-6]
            return res.fun, picks
    except ImportError:
        pass
    best = (9e9, None)
    for (l1, m1, w1), (l2, m2, w2) in itertools.combinations(instruments, 2):
        if set(w1) | set(w2) == set(range(10)):
            c = 1 / m1 + 1 / m2  # stake to get >=1 in each leg's region
            if c < best[0]: best = (c, [(l1, round(1 / m1, 4)), (l2, round(1 / m2, 4))])
    return best

def main():
    ws = DerivWS(token="")
    syms = [r["symbol"] for r in json.load(open("../results/universe_screen.json"))["symbols"] if r["digits"]]
    surface = {}
    for sym in syms:
        surface[sym] = {}
        for dur in DURATIONS:
            rows = []
            for ctype, barrier, p in CONTRACTS:
                req = dict(amount=STAKE, basis="stake", contract_type=ctype, currency="USD",
                           duration=dur, duration_unit="t", underlying_symbol=sym)
                if barrier is not None: req["barrier"] = str(barrier)
                r = ws.proposal(**req)
                if "proposal" in r:
                    pay = float(r["proposal"]["payout"]); M = pay / STAKE
                    rows.append(dict(type=ctype, barrier=barrier, p=p, M=round(M, 4),
                                     implied=round(1 / M, 4), edge=round(1 - p * M, 4)))
                else:
                    rows.append(dict(type=ctype, barrier=barrier, p=p,
                                     error=r.get("error", {}).get("message")))
                time.sleep(0.12)
            surface[sym][str(dur)] = rows
            ok = [r for r in rows if "M" in r]
            if ok:
                worst = max(ok, key=lambda r: r["edge"]); bestc = min(ok, key=lambda r: r["edge"])
                print(f"{sym} dur={dur}: {len(ok)} priced; edge {bestc['edge']*100:.2f}%..{worst['edge']*100:.2f}% "
                      f"(best {bestc['type']}{bestc['barrier'] if bestc['barrier'] is not None else ''})", flush=True)
        time.sleep(0.2)

    # analysis
    analysis = {"dutch": {}, "duration_spread": {}, "match_best": [], "over0_best": []}
    md = ["# Payout surface — full digit universe\n"]
    for sym in syms:
        for dur in DURATIONS:
            rows = [r for r in surface[sym][str(dur)] if "M" in r]
            if not rows: continue
            instruments = [(f"{r['type']}{r['barrier'] if r['barrier'] is not None else ''}",
                            r["M"], [d for d in range(10) if wins(r["type"], r["barrier"], d)]) for r in rows]
            cost, picks = dutch_lp(instruments)
            analysis["dutch"][f"{sym}:{dur}"] = dict(cost=round(cost, 5), picks=picks)
        # duration spread on a few
        for ctype, barrier in [("DIGITMATCH", 0), ("DIGITOVER", 0), ("DIGITEVEN", None), ("DIGITOVER", 4)]:
            m = {}
            for dur in DURATIONS:
                for r in surface[sym][str(dur)]:
                    if r["type"] == ctype and r.get("barrier") == barrier and "M" in r:
                        m[dur] = r["M"]
            if len(m) == 2 and abs(m[1] - m[10]) > 1e-9:
                analysis["duration_spread"].setdefault(sym, []).append(
                    dict(type=ctype, barrier=barrier, M1=m[1], M10=m[10]))
    # best venue tables
    for sym in syms:
        rows = [r for r in surface[sym]["1"] if "M" in r]
        for r in rows:
            if r["type"] == "DIGITMATCH" and r["barrier"] == 0:
                analysis["match_best"].append((sym, r["M"]))
            if r["type"] == "DIGITOVER" and r["barrier"] == 0:
                analysis["over0_best"].append((sym, r["M"]))
    analysis["match_best"].sort(key=lambda x: -x[1]); analysis["over0_best"].sort(key=lambda x: -x[1])

    with open("../results/payout_surface.json", "w") as f:
        json.dump(dict(surface=surface, analysis=analysis, stake=STAKE, ts=time.time()), f, indent=1)

    md.append("\n## Edge by contract (duration 1 tick) — every symbol\n")
    for sym in syms:
        rows = [r for r in surface[sym]["1"] if "M" in r]
        if not rows: continue
        md.append(f"\n### {sym}\n\n| contract | p | M | implied p | house edge |\n|---|---:|---:|---:|---:|\n")
        for r in rows:
            b = r["barrier"] if r["barrier"] is not None else ""
            md.append(f"| {r['type']}{b} | {r['p']:.1f} | {r['M']:.4f} | {r['implied']:.4f} | {r['edge']*100:.2f}% |\n")
    md.append("\n## Dutch-book LP (min cost to lock $1 across all digits; <1 = arbitrage)\n\n")
    worst_dutch = sorted(analysis["dutch"].items(), key=lambda kv: kv[1]["cost"])[:15]
    md.append("| symbol:dur | min cost | portfolio |\n|---|---:|---|\n")
    for k, v in worst_dutch:
        md.append(f"| {k} | {v['cost']:.5f} | {v['picks']} |\n")
    md.append("\n## Duration spreads (same event, different payout)\n\n")
    for sym, lst in analysis["duration_spread"].items():
        for d in lst:
            md.append(f"- {sym} {d['type']}{d['barrier'] if d['barrier'] is not None else ''}: M(1t)={d['M1']} vs M(10t)={d['M10']}\n")
    md.append("\n## Best venue (highest payout for same event, dur 1)\n\n")
    md.append("MATCH: " + ", ".join(f"{s}={m}" for s, m in analysis["match_best"][:8]) + "\n\n")
    md.append("OVER0: " + ", ".join(f"{s}={m}" for s, m in analysis["over0_best"][:8]) + "\n")
    with open("../results/payout_surface.md", "w") as f:
        f.write("".join(md))
    print("wrote payout_surface.{json,md}")
    ws.close()

if __name__ == "__main__":
    main()
