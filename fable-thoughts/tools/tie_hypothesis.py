"""tie_hypothesis.py — H7: Rise/Fall tie mispricing on low-sigma pip lattices.

Physics: JD100 quotes live on a 0.01 grid with sigma ~4 pips per 1s tick. P(next tick price
EXACTLY equals current) ~ phi(0)/sigma ~ 9.7%. A 1-tick CALL (strictly higher) then has
P(win) ~ (1 - P(tie))/2 ~ 0.45, while CALLE (higher or equal) has ~0.55. If Deriv's payout
grid treats ties generically (tuned for symbols where P(tie) ~ 0), CALLE is structurally +EV
exactly like the digit window contracts. Test: live proposals for CALL/PUT/CALLE/PUTE at
1..10 ticks on every low-sigma symbol + empirical tie/up/down rates from history.

Run: python3 tie_hypothesis.py JD100 R_100 1HZ100V stpRNG stpRNG2
"""
import sys, time, gzip
import numpy as np
from deriv_api import DerivWS

def empirical(ws, sym, n=20000):
    times, prices, pip = ws.history_paged(sym, n, sleep=0.2)
    v = np.round(np.array(prices) * (10 ** int(pip))).astype(np.int64)
    lags = {}
    for lag in (1, 2, 3, 5, 10):
        d = v[lag:] - v[:-lag]
        lags[lag] = (float((d > 0).mean()), float((d == 0).mean()), float((d < 0).mean()))
    return lags, len(v), int(pip)

def main():
    syms = sys.argv[1:] or ["JD100"]
    ws = DerivWS(token="")
    out = ["# H7 — Rise/Fall tie structure vs live CALL/CALLE payouts\n"]
    for sym in syms:
        lags, n, pip = empirical(ws, sym, 20000)
        out.append(f"\n## {sym} (n={n}, pip=1e-{pip})\n\n")
        out.append("| dur(t) | P(up) | P(tie) | P(down) | CALL M | CALLE M | PUT M | PUTE M | "
                   "EV CALL | EV CALLE | EV PUT | EV PUTE |\n|" + "---:|" * 12 + "\n")
        for dur in (1, 2, 3, 5, 10):
            ms = {}
            for ct in ("CALL", "CALLE", "PUT", "PUTE"):
                r = ws.proposal(amount=10, basis="stake", contract_type=ct, currency="USD",
                                duration=dur, duration_unit="t", underlying_symbol=sym)
                if "proposal" in r:
                    ms[ct] = float(r["proposal"]["payout"]) / 10
                else:
                    ms[ct] = None
                time.sleep(0.08)
            pu, pt, pd = lags[dur]
            def ev(p, m): return None if m is None else p * m - 1
            evs = [ev(pu, ms["CALL"]), ev(pu + pt, ms["CALLE"]), ev(pd, ms["PUT"]), ev(pd + pt, ms["PUTE"])]
            fmt = lambda x: "—" if x is None else f"{x:.3f}"
            fme = lambda x: "—" if x is None else f"{x*100:+.2f}%"
            out.append(f"| {dur} | {pu:.4f} | {pt:.4f} | {pd:.4f} | {fmt(ms['CALL'])} | {fmt(ms['CALLE'])} | "
                       f"{fmt(ms['PUT'])} | {fmt(ms['PUTE'])} | {fme(evs[0])} | {fme(evs[1])} | {fme(evs[2])} | {fme(evs[3])} |\n")
        print("".join(out[-8:]))
    open("../results/tie_hypothesis.md", "w").write("".join(out))

if __name__ == "__main__":
    main()
