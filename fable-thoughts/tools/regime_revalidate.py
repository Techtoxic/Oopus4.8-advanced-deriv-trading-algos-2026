"""regime_revalidate.py — rebuild the offset tables at TODAY's sigma and test the
extrapolation that the whole 12-day forecast rests on.

THE PROBLEM
payout_audit.py established the executed JD100 grid is cut: OVER4/UNDER5 fills at 1.8286,
not 1.953. Breakeven moved 51.20% -> 54.69%. At sigma 3.90 the projected EV is -2.90%, and
the projection says breakeven arrives around sigma 3.60 (spot ~192, ~12 days at the measured
-0.67%/day drag).

Every row of that projection is EXTRAPOLATION. empirical_offset_tables.json stops at sigma
4.0. The shrink factor used (0.9989) was calibrated on 4.0-4.9. Nobody has measured whether
the wrapped-normal shape survives below 4.0.

This repo has been burned by exactly this before: opus's +3.22% deep-gate estimate became
-1.67% OOS because a model was trusted outside its fitted range.

THE FIX
JD100 is sitting AT sigma ~3.90 right now, so the sub-4.0 region is directly measurable
instead of extrapolated. This script:

  1. Pulls a large block of fresh JD100 ticks.
  2. Rebuilds P(step mod 10 = k | sigma bin) with the SAME W=1800 jump-filtered causal
     estimator empirical_pmf.py uses, so the table inherits the estimator's noise and both
     the heavy-tail and winner's-curse biases cancel.
  3. Splits IS/OOS by time. Tables are built on IS ONLY; all reported numbers are OOS.
  4. For every sigma bin, compares MEASURED p against the wrapped-normal prediction.
     That comparison is the point: it validates or kills the extrapolation.
  5. Prices the restricted ladder at the EXECUTED grid and reports the sigma at which
     EV actually turns positive -- measured, not projected.

Outputs fresh tables keyed to the current regime. The existing tables were fitted at
sigma 4.15-4.35 and must NOT be reused at 3.90.

Read-only. Places no trades.

Run: python3 regime_revalidate.py
     python3 regime_revalidate.py --ticks 300000 --symbol JD100
"""
import argparse, json, math
import numpy as np
from deriv_api import DerivWS
from sigma_model import winset, wrapped_normal_pmf

W = 1800

# EXECUTED grid, measured by payout_audit.py 2026-08-04 (NOT the proposal grid,
# which still serves the pre-cut numbers: proposal 1.886 vs executed 1.8286).
EXEC_OU = {0: 1.057, 1: 1.171, 2: 1.343, 3: 1.5429, 4: 1.8286,
           5: 2.229, 6: 2.857, 7: 4.000, 8: 6.657}
EXEC_GRID = {}
for _k in range(9):
    EXEC_GRID[("DIGITOVER", _k)] = EXEC_OU[_k]
for _k in range(1, 10):
    EXEC_GRID[("DIGITUNDER", _k)] = EXEC_OU[9 - _k]
EXEC_GRID[("DIGITMATCH", None)] = 6.657
EXEC_GRID[("DIGITDIFF", None)] = 1.057

LADDER = [("DIGITOVER", 3), ("DIGITOVER", 4), ("DIGITUNDER", 5), ("DIGITUNDER", 6)]


def rolling_sigma(v):
    """Causal: sig[i] uses steps strictly before tick i. Same as empirical_pmf.py."""
    st = np.diff(v)
    absst = np.abs(st)
    njsq = np.where(absst <= 20, st.astype(float) ** 2, 0.0)
    njc = (absst <= 20).astype(float)
    csum = np.concatenate([[0], np.cumsum(njsq)])
    ccnt = np.concatenate([[0], np.cumsum(njc)])
    sig = np.full(len(v), np.nan)
    for i in range(W + 1, len(v)):
        cnt = ccnt[i - 1] - ccnt[i - 1 - W]
        if cnt >= 200:
            sig[i] = math.sqrt((csum[i - 1] - csum[i - 1 - W]) / cnt)
    return sig, st


def interleave_split(n, block, purge):
    """
    Sigma decays MONOTONICALLY, so a time split gives IS and OOS disjoint sigma ranges
    and the bins never intersect. Interleave alternating blocks instead so both halves
    span the same sigma range, purging `purge` ticks either side of every boundary to
    kill leakage through the W-tick rolling estimator.
    """
    is_m = np.zeros(n, dtype=bool)
    oos_m = np.zeros(n, dtype=bool)
    for start in range(0, n, block):
        end = min(start + block, n)
        tgt = is_m if (start // block) % 2 == 0 else oos_m
        tgt[min(start + purge, end):max(end - purge, start)] = True
    return is_m, oos_m


def build_tables(v, sig, st, mask, binw=0.1, min_n=2000):
    """tables[bin] = offset pmf, counts[bin] = n. Built over indices where mask is True."""
    mod = ((st + 5) % 10) - 5
    counts = {}
    for i in range(W + 1, len(v) - 1):
        if not mask[i] or np.isnan(sig[i]):
            continue
        b = round(sig[i] / binw)
        counts.setdefault(b, np.zeros(10, dtype=np.int64))[mod[i] % 10] += 1
    tables = {b: (c / c.sum()).tolist() for b, c in counts.items() if c.sum() >= min_n}
    return tables, {b: int(c.sum()) for b, c in counts.items()}


def pmf_from_offsets(off, center):
    return [off[(d - center) % 10] for d in range(10)]


def best_contract(pmf, grid):
    best = None
    for (ct, bar), M in grid.items():
        if bar is None:
            continue
        p = sum(pmf[x] for x in winset(ct, bar))
        ev = p * M - 1
        if best is None or ev > best[0]:
            best = (ev, ct, bar, p, M)
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="JD100")
    ap.add_argument("--ticks", type=int, default=1200000)
    ap.add_argument("--block", type=int, default=20000,
                    help="interleave block size for the IS/OOS split")
    ap.add_argument("--purge", type=int, default=W,
                    help="ticks dropped either side of each block boundary")
    ap.add_argument("--binw", type=float, default=0.1)
    ap.add_argument("--min-n", type=int, default=2000)
    ap.add_argument("--out-tables", default="../results/offset_tables_current.json")
    ap.add_argument("--out", default="../results/regime_revalidate.md")
    a = ap.parse_args()

    ws = DerivWS(token="")
    print(f"fetching {a.ticks} ticks of {a.symbol}...")
    _, prices, pip = ws.history_paged(a.symbol, a.ticks, sleep=0.2)
    pip = int(pip)
    v = np.round(np.asarray(prices, dtype=float) * (10 ** pip)).astype(np.int64)
    print(f"got {len(v)} ticks, spot {prices[-1]}, pip 1e-{pip}")
    ws.close()

    sig, st = rolling_sigma(v)
    valid = sig[~np.isnan(sig)]
    print(f"sigma range {valid.min():.2f} - {valid.max():.2f}, "
          f"median {np.median(valid):.2f}")

    is_m, oos_m = interleave_split(len(v), a.block, a.purge)
    print(f"interleaved split: block {a.block}, purge {a.purge} "
          f"-> IS {is_m.sum()} ticks, OOS {oos_m.sum()} ticks")
    tabs_is, n_is = build_tables(v, sig, st, is_m, a.binw, a.min_n)
    tabs_oos, n_oos = build_tables(v, sig, st, oos_m, a.binw, a.min_n)
    shared = sorted(set(tabs_is) & set(tabs_oos))
    print(f"IS bins {sorted(tabs_is)}")
    print(f"OOS bins {sorted(tabs_oos)}")
    print(f"shared bins {shared}  (sigma {[round(b*a.binw,2) for b in shared]})")
    if not shared:
        print("\nNo shared bins. Increase --ticks; the sample spans too little sigma.")
        return
    if not any(b * a.binw < 4.0 for b in shared) or not any(b * a.binw >= 4.0 for b in shared):
        print("\nWARNING: shared bins do not straddle sigma 4.0, so the extrapolation")
        print("cannot be validated. Increase --ticks to reach further back.")

    # ---- 1. DOES THE WRAPPED-NORMAL SHAPE SURVIVE BELOW 4.0? --------------
    print(f"\n{'='*74}")
    print("1. MEASURED vs WRAPPED-NORMAL  (5-wide window, offsets +/-2)")
    print("   this is what the whole 12-day forecast rests on")
    print(f"{'='*74}")
    WIN5 = {0, 1, 2, 8, 9}
    print(f"{'sigma':>7}{'n(IS)':>9}{'meas p':>9}{'WN p':>9}{'meas/WN':>9}  region")
    rows = []
    for b in sorted(set(tabs_is) & set(tabs_oos)):
        s = b * a.binw
        meas = sum(tabs_is[b][i] for i in WIN5)
        pmf = wrapped_normal_pmf(s, 0)
        wn = sum(pmf[i] for i in WIN5)
        reg = "BELOW 4.0 (was extrapolated)" if s < 4.0 else "within old tables"
        rows.append((s, n_is.get(b, 0), meas, wn, meas / wn, reg))
        print(f"{s:>7.2f}{n_is.get(b,0):>9}{meas:>9.4f}{wn:>9.4f}"
              f"{meas/wn:>9.4f}  {reg}")

    sub = [r for r in rows if r[0] < 4.0]
    above = [r for r in rows if r[0] >= 4.0]
    if sub and above:
        rs, ra = np.mean([r[4] for r in sub]), np.mean([r[4] for r in above])
        print(f"\n  mean meas/WN below 4.0: {rs:.4f}   at/above 4.0: {ra:.4f}")
        if abs(rs - ra) > 0.01:
            print(f"  *** SHAPE CHANGES below 4.0 by {(rs/ra-1)*100:+.2f}%. "
                  f"The extrapolation was WRONG. ***")
        else:
            print("  Shape is stable across 4.0. Extrapolation was sound.")
    elif not sub:
        print("\n  No bins below 4.0 in this sample — cannot validate. "
              "Re-run with more ticks or wait for further decay.")

    # ---- 2. OOS EV AT THE EXECUTED GRID ----------------------------------
    print(f"\n{'='*74}")
    print("2. OOS EV AT THE EXECUTED GRID (tables from IS, evaluated on OOS)")
    print(f"{'='*74}")
    print(f"{'sigma':>7}{'n(OOS)':>9}{'best-EV':>12}{'ladder-only EV':>18}")
    ev_rows = []
    for b in sorted(set(tabs_is) & set(tabs_oos)):
        s = b * a.binw
        # average over centres, weighting each centre equally
        evs, lad = [], []
        for c in range(10):
            pmf_is = pmf_from_offsets(tabs_is[b], c)
            pick = best_contract(pmf_is, EXEC_GRID)
            if pick is None:
                continue
            _, ct, bar, _, M = pick
            pmf_oos = pmf_from_offsets(tabs_oos[b], c)
            p_oos = sum(pmf_oos[x] for x in winset(ct, bar))
            evs.append(p_oos * M - 1)
            lp = [(sum(pmf_oos[x] for x in winset(lct, lb)) * EXEC_GRID[(lct, lb)] - 1)
                  for lct, lb in LADDER]
            lad.append(max(lp))
        if not evs:
            continue
        ev, evl = float(np.mean(evs)), float(np.mean(lad))
        ev_rows.append((s, ev, evl))
        print(f"{s:>7.2f}{n_oos.get(b,0):>9}{ev*100:>11.2f}%{evl*100:>17.2f}%")

    pos = [r for r in ev_rows if r[2] > 0]
    print()
    if pos:
        lo_s = min(r[0] for r in pos)
        print(f"  Ladder EV turns POSITIVE at sigma <= {lo_s:.2f} (measured, OOS)")
        print(f"  Projection said 3.60. Measured says {lo_s:.2f}.")
    else:
        print("  Ladder EV is negative at EVERY measured sigma bin.")
        print("  The cut grid is not recoverable in the sigma range sampled here.")

    json.dump({str(k): v_ for k, v_ in tabs_is.items()}, open(a.out_tables, "w"))
    print(f"\nwrote fresh tables -> {a.out_tables}")

    md = [f"# Regime revalidation — {a.symbol} at the executed grid\n\n",
          f"Ticks: {len(v)}, spot {prices[-1]}, sigma "
          f"{valid.min():.2f}-{valid.max():.2f}.\n\n",
          "Executed grid from payout_audit.py (OVER4/UNDER5 = 1.8286). The proposal "
          "endpoint still serves the pre-cut grid and must not be used.\n\n",
          "## Measured vs wrapped-normal\n\n",
          "| sigma | n | measured p | WN p | ratio | region |\n|---:|---:|---:|---:|---:|---|\n"]
    for s, n, meas, wn, r, reg in rows:
        md.append(f"| {s:.2f} | {n} | {meas:.4f} | {wn:.4f} | {r:.4f} | {reg} |\n")
    md.append("\n## OOS EV at executed grid\n\n| sigma | best-EV | ladder-only |\n|---:|---:|---:|\n")
    for s, ev, evl in ev_rows:
        md.append(f"| {s:.2f} | {ev*100:+.2f}% | {evl*100:+.2f}% |\n")
    open(a.out, "w").write("".join(md))
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
