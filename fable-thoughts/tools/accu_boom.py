"""accu_boom.py — H5b: accumulator RTP on the 14 BOOM/CRASH indices.

Why these symbols: opus's accumulator_rtp.md scanned only R_100 / 1HZ100V / 1HZ10V and found
G < 1 everywhere (tightest 0.998). Correct for near-Gaussian symbols. BOOM/CRASH were never
scanned, and they are the one family where a variance-calibrated barrier is genuinely hard to
set: returns are BIMODAL. On BOOM1000 roughly 1 tick in 1000 is a large upward spike carrying
most of the variance; the other ~999 are small drift ticks.

An accumulator survives a tick iff |return| <= tick_size_barrier. If Deriv derives tsb from
AGGREGATE variance (which the spike dominates), tsb ends up wide relative to the drift ticks,
so p = P(survive) approaches 1 and you only die on spikes.

    G = (1 + g) * p        G > 1 compounds in your favour; Deriv targets G just under 1.

At p = 0.999 and g = 0.01 you get G = 1.009 — a compounding edge. This script measures whether
that is real. It is READ-ONLY: live proposals for the barrier + tick history for the empirical
survive rate. No trades.

Beyond opus's version this adds:
  - live tick fetch (no ../data/*.csv.gz dependency)
  - spike/drift decomposition: p on drift-only ticks vs the blended rate, and the empirical
    spike period, so you can see WHY a cell passes or fails
  - a hold-N EV curve, since accumulator EV is G^N and the sane exit is "sell before a spike"
  - Wilson 99% bounds, and G_hi as the honest optimistic case

Run: python3 accu_boom.py                     # all 14
     python3 accu_boom.py --symbols BOOM1000 CRASH1000
     python3 accu_boom.py --ticks 50000       # tighter bounds, slower
"""
import argparse, math, time
import numpy as np
from deriv_api import DerivWS

BOOM = ["BOOM50", "BOOM150N", "BOOM300N", "BOOM500", "BOOM600", "BOOM900", "BOOM1000"]
CRASH = ["CRASH50", "CRASH150N", "CRASH300N", "CRASH500", "CRASH600", "CRASH900", "CRASH1000"]
RATES = [0.01, 0.02, 0.03, 0.04, 0.05]


def wilson(k, n, z=2.576):
    if n == 0:
        return 0.0, 1.0
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return c - h, c + h


def fetch(ws, sym, n):
    times, prices, pip = ws.history_paged(sym, n, sleep=0.2)
    seen = {}
    for t, p in zip(times, prices):
        seen[t] = p
    ks = sorted(seen)
    return np.array([seen[k] for k in ks], dtype=float), int(pip)


def decompose(rel, mult=5.0):
    """Split |returns| into drift vs spike using a robust MAD threshold."""
    med = float(np.median(rel))
    mad = float(np.median(np.abs(rel - med))) or 1e-12
    thr = med + mult * 1.4826 * mad
    spike = rel > thr
    n_sp = int(spike.sum())
    period = len(rel) / n_sp if n_sp else float("inf")
    return spike, thr, period


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+", default=BOOM + CRASH)
    ap.add_argument("--ticks", type=int, default=30000)
    ap.add_argument("--out", default="../results/accu_boom.md")
    a = ap.parse_args()

    ws = DerivWS(token="")
    md = ["# H5b — Accumulator RTP on BOOM/CRASH indices\n\n",
          "`G = (1+g) * p_survive`. G>1 compounds in your favour. Wilson 99% bounds on p.\n",
          "`p_drift` = survive rate excluding spike ticks; `spike 1/N` = empirical spike period.\n\n",
          "| symbol | rate | tick_size_barrier | n | p_hat | Wilson99 | p_drift | spike 1/N | "
          "**G** | G_hi | Deriv implied p |\n", "|---|---:|---:|---:|---:|---|---:|---:|---:|---:|---:|\n"]

    rows = []
    print(f"{'symbol':10}{'rate':>6}{'tsb':>12}{'p_hat':>9}{'G':>9}{'G_hi':>9}{'spike':>9}")
    print("-" * 64)

    for sym in a.symbols:
        try:
            ps, pip = fetch(ws, sym, a.ticks)
        except Exception as e:
            print(f"{sym:10} fetch error: {e}")
            continue
        if len(ps) < 2000:
            print(f"{sym:10} only {len(ps)} ticks, skipping")
            continue

        rel = np.abs(np.diff(ps) / ps[:-1])
        spike, thr, period = decompose(rel)
        drift = rel[~spike]

        for g in RATES:
            r = ws.call({"proposal": 1, "amount": 100, "basis": "stake",
                         "contract_type": "ACCU", "currency": "USD",
                         "underlying_symbol": sym, "growth_rate": g})
            if "proposal" not in r:
                continue
            cd = r["proposal"].get("contract_details", {})
            if "tick_size_barrier" not in cd:
                continue
            tsb = float(cd["tick_size_barrier"])
            tsi = cd.get("ticks_stayed_in", [])
            mt = float(np.mean(tsi)) if tsi else float("nan")
            p_deriv = mt / (1 + mt) if tsi else float("nan")

            k = int((rel <= tsb).sum())
            n = len(rel)
            p = k / n
            lo, hi = wilson(k, n)
            p_dr = float((drift <= tsb).mean()) if len(drift) else float("nan")
            G = (1 + g) * p
            Ghi = (1 + g) * hi

            rows.append(dict(sym=sym, g=g, tsb=tsb, n=n, p=p, lo=lo, hi=hi,
                             p_dr=p_dr, period=period, G=G, Ghi=Ghi, pd=p_deriv))
            md.append(f"| {sym} | {g:.2f} | {tsb:.6e} | {n} | {p:.5f} | "
                      f"[{lo:.5f},{hi:.5f}] | {p_dr:.5f} | {period:.0f} | "
                      f"**{G:.5f}** | {Ghi:.5f} | {p_deriv:.5f} |\n")
            print(f"{sym:10}{g:>6.2f}{tsb:>12.3e}{p:>9.5f}{G:>9.5f}{Ghi:>9.5f}{period:>9.0f}")
            time.sleep(0.15)
        time.sleep(0.2)

    if not rows:
        print("\nno cells measured"); ws.close(); return

    rows.sort(key=lambda r: -r["G"])
    best = rows[0]

    md.append(f"\n## Best cell\n\n`{best['sym']}` @ g={best['g']:.2f} -> "
              f"**G = {best['G']:.5f}** (Wilson-99 upper {best['Ghi']:.5f}).\n\n")

    print(f"\n{'='*64}")
    print(f"BEST: {best['sym']} @ g={best['g']:.2f}  G={best['G']:.5f}  G_hi={best['Ghi']:.5f}")

    if best["G"] > 1:
        md.append("**G > 1 on the point estimate.** Hold-N EV factor is G^N:\n\n")
        print("\n  G > 1 — hold-N EV factor G^N:")
        for N in (5, 10, 20, 50, 100):
            print(f"    N={N:<4} {(best['G']**N - 1)*100:+.2f}%")
            md.append(f"- N={N}: {(best['G']**N - 1)*100:+.2f}%\n")
        md.append("\nNext: verify the barrier is stable across a session (re-proposal every "
                  "few minutes), then paper-trade hold-N with a spike-aware exit.\n")
        print("\n  NEXT: confirm barrier stability, then paper-trade hold-N.")
    elif best["Ghi"] > 1:
        md.append("Point estimate below 1 but the Wilson-99 upper bound clears it — "
                  "underpowered, not promising. Re-run with more ticks before concluding.\n")
        print("\n  Point est < 1 but upper bound > 1 — underpowered. Re-run with --ticks 100000.")
    else:
        md.append("**Every cell G < 1**, upper bounds included. The barrier is correctly "
                  "calibrated on this family too; no hold/sell pattern can be +EV.\n")
        print("\n  All cells G < 1 including upper bounds. Correctly priced. Hypothesis dead.")

    md.append("\n## All cells, ranked\n\n| rank | symbol | g | G | G_hi |\n|---:|---|---:|---:|---:|\n")
    for i, r in enumerate(rows[:15], 1):
        md.append(f"| {i} | {r['sym']} | {r['g']:.2f} | {r['G']:.5f} | {r['Ghi']:.5f} |\n")

    open(a.out, "w").write("".join(md))
    print(f"\nwrote {a.out}")
    ws.close()


if __name__ == "__main__":
    main()
