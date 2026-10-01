"""H5: accumulator exact RTP. Per (symbol, growth_rate): get tick_size_barrier from a live
proposal, then compute empirical survive-prob p = P(|tick return| <= tsb) from harvested ticks.
Decision number: G = (1+g) x p  (>1 = money printer; Deriv targets just under 1).
Also cross-checks p implied by Deriv's own ticks_stayed_in stats.
Run: python3 accumulator_scan.py R_100 1HZ100V 1HZ10V
Out: ../results/accumulator_rtp.md
"""
import sys, gzip, math, time
import numpy as np
from deriv_api import DerivWS

RATES = [0.01, 0.02, 0.03, 0.04, 0.05]

def load_prices(sym):
    ts, ps = [], []
    with gzip.open(f"../data/{sym}.csv.gz", "rt") as f:
        f.readline()
        for line in f:
            t, p = line.strip().split(",")
            ts.append(int(t)); ps.append(float(p))
    # dedupe by timestamp, keep order
    seen = {}
    for t, p in zip(ts, ps): seen[t] = p
    tss = sorted(seen)
    return np.array([seen[t] for t in tss])

def wilson(k, n, z=2.576):
    p = k / n; den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return c - h, c + h

def main():
    syms = sys.argv[1:] or ["R_100", "1HZ100V", "1HZ10V"]
    ws = DerivWS(token="")
    md = ["# Accumulator RTP — exact, from live barriers + big tick samples\n\n",
          "G = (1+growth) x P(survive tick). G>1 compounds in your favor. Wilson 99% bounds.\n\n",
          "| symbol | rate | tick_size_barrier | n ticks | p_hat | Wilson99 | G=(1+g)p | G_hi | Deriv ticks_stayed_in implied p |\n",
          "|---|---:|---:|---:|---:|---|---:|---:|---:|\n"]
    best = None
    for sym in syms:
        try:
            ps = load_prices(sym)
        except FileNotFoundError:
            print("no data for", sym); continue
        rel = np.abs(np.diff(ps) / ps[:-1])
        for g in RATES:
            r = ws.call({"proposal": 1, "amount": 100, "basis": "stake", "contract_type": "ACCU",
                         "currency": "USD", "underlying_symbol": sym, "growth_rate": g})
            if "proposal" not in r:
                continue
            cd = r["proposal"]["contract_details"]
            tsb = float(cd["tick_size_barrier"])
            tsi = cd.get("ticks_stayed_in", [])
            mt = float(np.mean(tsi)) if tsi else float("nan")
            p_deriv = mt / (1 + mt) if tsi else float("nan")
            k = int((rel <= tsb).sum()); n = len(rel)
            p = k / n
            lo, hi = wilson(k, n)
            G = (1 + g) * p; Ghi = (1 + g) * hi
            md.append(f"| {sym} | {g:.2f} | {tsb:.6e} | {n} | {p:.5f} | [{lo:.5f},{hi:.5f}] | "
                      f"**{G:.5f}** | {Ghi:.5f} | {p_deriv:.5f} |\n")
            if best is None or G > best[0]: best = (G, sym, g, p, hi)
            time.sleep(0.15)
    md.append(f"\nBest cell: {best[1]} @ {best[2]} -> G={best[0]:.5f} "
              f"(even the Wilson-99 upper bound {(1+best[2])*best[4]:.5f} {'>' if (1+best[2])*best[4]>1 else '<'} 1).\n")
    md.append("\nInterpretation: hold-N-ticks-then-sell has EV factor G^N. G<1 for every cell means "
              "no holding/selling/take-profit pattern can be +EV; the closer G is to 1 the slower the bleed. "
              "Re-run this scan periodically: if Deriv ever mis-sets a barrier after a vol change, G>1 shows up here first.\n")
    open("../results/accumulator_rtp.md", "w").write("".join(md))
    print("".join(md))
    ws.close()

if __name__ == "__main__":
    main()
