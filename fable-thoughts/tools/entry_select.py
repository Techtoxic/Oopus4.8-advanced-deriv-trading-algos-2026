"""entry_select.py — is the entry-digit dependence tradeable by SELECTION?

WHAT WENT WRONG IN battery_v2
Section 2 returned byte-identical win rates for pooled and per-entry tables (p=0.53494,
gain +0.0000pp). Identical outputs from different inputs is a broken test, not a null.

The cause: it asked whether per-entry conditioning changes WHICH 5-window of offsets is
best. It does not — every offset distribution is centred on 0, so {8,9,0,1,2} wins for all
ten entry digits. Same selection, same win rate, by construction.

But chi2 = 675.6 vs a permutation null of 81.5 +/- 12.6 (z = +47) says the offset
distribution differs in SHAPE by entry digit. Same window, different probability. And
duration_surface already measured it directly: entry digit 7 gave p = 0.54825 while pooled
is 0.53494 — a 1.33pp spread that battery_v2 averaged away.

So the edge, if any, is not in changing the contract. It is in TRADING ONLY THE ENTRY
DIGITS WHERE p IS HIGHEST. That is a selection strategy and nothing in this repo has tested
it: adaptive_sentinel applies its sigma gate uniformly across entry digits.

THE TEST
  1. Per-entry-digit win rate, IS and OOS side by side, on the fixed +/-2 window at the
     executed payout 1.8286 (breakeven 54.69%). Does the IS ranking PERSIST out of sample?
     If the ranking is noise, IS-best digits will be average OOS and the idea is dead.
  2. Selection strategy: pick entry digits on IS alone, measure OOS. Compare against
     trading every digit. Reports trades/hour, because selectivity costs volume.
  3. Joint entry-digit x sigma-bin, since sigma is the other known axis and the two may
     compound.
  4. Rank correlation between IS and OOS per-digit win rates — the cleanest single number
     for "is this signal stable".

Interleaved-block split with purge: sigma trends across the sample, so a time split would
put IS and OOS in different regimes.

Read-only. Places no trades.

Run: python3 entry_select.py --symbol JD100
"""
import argparse, math
import numpy as np
from deriv_api import DerivWS

EXEC_M = 1.8286
BE = 1 / EXEC_M
WIN5 = [0, 1, 2, 8, 9]      # next digit within +/-2 of entry digit


def wilson(k, n, z=2.576):
    if n == 0:
        return 0.0, 1.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return c - h, c + h


def interleave(n, block=20000, purge=2000):
    a = np.zeros(n, bool); b = np.zeros(n, bool)
    for s in range(0, n, block):
        e = min(s + block, n)
        (a if (s // block) % 2 == 0 else b)[min(s + purge, e):max(e - purge, s)] = True
    return a, b


def roll_sigma(st, w=1800):
    f = np.where(np.abs(st) <= 20, st.astype(float) ** 2, 0.0)
    c = np.where(np.abs(st) <= 20, 1.0, 0.0)
    cs = np.concatenate([[0.0], np.cumsum(f)])
    cc = np.concatenate([[0.0], np.cumsum(c)])
    out = np.full(len(st), np.nan)
    for i in range(w + 1, len(st)):
        n = cc[i] - cc[i - w]
        if n >= 200:
            out[i] = math.sqrt((cs[i] - cs[i - w]) / n)
    return out


def spearman(x, y):
    rx = np.argsort(np.argsort(x)).astype(float)
    ry = np.argsort(np.argsort(y)).astype(float)
    return float(np.corrcoef(rx, ry)[0, 1])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="JD100")
    ap.add_argument("--ticks", type=int, default=800000)
    ap.add_argument("--out", default="../results/entry_select.md")
    a = ap.parse_args()

    ws = DerivWS(token="")
    print(f"fetching {a.ticks} ticks of {a.symbol}...")
    _, prices, pip = ws.history_paged(a.symbol, a.ticks, sleep=0.2)
    pip = int(pip)
    v = np.round(np.asarray(prices, dtype=float) * (10 ** pip)).astype(np.int64)
    ws.close()
    st = np.diff(v)
    dig = (v % 10).astype(int)
    ent = dig[:-1]
    off = (dig[1:] - dig[:-1]) % 10
    win = np.isin(off, WIN5)
    n = len(off)
    sig = roll_sigma(st)[:n]
    print(f"got {len(v)} ticks, spot {prices[-1]}, "
          f"sigma {np.nanmin(sig):.2f}-{np.nanmax(sig):.2f}\n")

    is_m, oos_m = interleave(n)

    # ---- 1. per-entry-digit, IS vs OOS ----------------------------------
    print("=" * 78)
    print(f"1. WIN RATE BY ENTRY DIGIT  (payout {EXEC_M}, breakeven {BE*100:.2f}%)")
    print("=" * 78)
    print(f"{'entry':>6}{'n(IS)':>9}{'p(IS)':>9}{'n(OOS)':>9}{'p(OOS)':>9}"
          f"{'OOS vs BE':>12}{'OOS EV':>9}{'99% low':>10}")
    is_p, oos_p, rows = [], [], []
    for c in range(10):
        si, so = is_m & (ent == c), oos_m & (ent == c)
        ni, no = int(si.sum()), int(so.sum())
        if ni < 1000 or no < 1000:
            continue
        pi, po = float(win[si].mean()), float(win[so].mean())
        lo, _ = wilson(int(win[so].sum()), no)
        is_p.append(pi); oos_p.append(po)
        rows.append((c, ni, pi, no, po, lo))
        print(f"{c:>6}{ni:>9}{pi:>9.5f}{no:>9}{po:>9.5f}"
              f"{(po-BE)*100:>+11.3f}pp{(po*EXEC_M-1)*100:>8.2f}%"
              f"{(lo*EXEC_M-1)*100:>9.2f}%")

    rho = spearman(np.array(is_p), np.array(oos_p))
    print(f"\n  IS/OOS rank correlation (Spearman) = {rho:+.3f}")
    print(f"  spread IS {(max(is_p)-min(is_p))*100:.2f}pp, "
          f"OOS {(max(oos_p)-min(oos_p))*100:.2f}pp")
    stable = rho > 0.5
    print(f"  -> {'ranking PERSISTS out of sample' if stable else 'ranking does NOT persist — the per-digit spread is noise'}")

    # ---- 2. selection strategy ------------------------------------------
    print(f"\n{'='*78}")
    print("2. SELECTION STRATEGY (choose entry digits on IS, measure on OOS)")
    print("=" * 78)
    all_k = int(win[oos_m].sum()); all_n = int(oos_m.sum())
    all_p = all_k / all_n
    lo, _ = wilson(all_k, all_n)
    print(f"  trade ALL digits:  n={all_n:>7} p={all_p:.5f} "
          f"EV {(all_p*EXEC_M-1)*100:+.2f}% 99%low {(lo*EXEC_M-1)*100:+.2f}%")
    print()
    print(f"  {'rule':28}{'n(OOS)':>9}{'p(OOS)':>9}{'EV':>9}{'99% low':>10}{'trades/hr':>11}")
    best = None
    for topk in (1, 2, 3, 4, 5):
        sel = [c for c, _, pi, _, _, _ in sorted(rows, key=lambda r: -r[2])[:topk]]
        m = oos_m & np.isin(ent, sel)
        k, nn = int(win[m].sum()), int(m.sum())
        if nn < 1000:
            continue
        p = k / nn
        wl, _ = wilson(k, nn)
        ev, evlo = p * EXEC_M - 1, wl * EXEC_M - 1
        frac = nn / all_n
        print(f"  {'top-'+str(topk)+' IS digits '+str(sel):28}{nn:>9}{p:>9.5f}"
              f"{ev*100:>8.2f}%{evlo*100:>9.2f}%{frac*3600:>11.0f}")
        if best is None or evlo > best[0]:
            best = (evlo, topk, sel, p, ev, nn)
    # threshold rule
    sel_t = [c for c, _, pi, _, _, _ in rows if pi > BE]
    if sel_t:
        m = oos_m & np.isin(ent, sel_t)
        k, nn = int(win[m].sum()), int(m.sum())
        if nn > 1000:
            p = k / nn
            wl, _ = wilson(k, nn)
            print(f"  {'IS p > breakeven '+str(sel_t):28}{nn:>9}{p:>9.5f}"
                  f"{(p*EXEC_M-1)*100:>8.2f}%{(wl*EXEC_M-1)*100:>9.2f}%"
                  f"{nn/all_n*3600:>11.0f}")
            if best is None or (wl * EXEC_M - 1) > best[0]:
                best = (wl * EXEC_M - 1, 'thr', sel_t, p, p * EXEC_M - 1, nn)

    # ---- 3. joint with sigma --------------------------------------------
    print(f"\n{'='*78}")
    print("3. ENTRY DIGIT x SIGMA  (do the two axes compound?)")
    print("=" * 78)
    ok = ~np.isnan(sig)
    if ok.sum() > 50000 and best:
        qs = np.nanpercentile(sig[ok], [33, 67])
        sel = best[2]
        print(f"  using selected digits {sel}")
        print(f"  {'sigma band':>20}{'n(OOS)':>9}{'p':>9}{'EV':>9}{'99% low':>10}")
        for lo_s, hi_s in ((-np.inf, qs[0]), (qs[0], qs[1]), (qs[1], np.inf)):
            m = oos_m & ok & np.isin(ent, sel) & (sig >= lo_s) & (sig < hi_s)
            nn = int(m.sum())
            if nn < 1000:
                continue
            k = int(win[m].sum()); p = k / nn
            wl, _ = wilson(k, nn)
            lab = (f"[{lo_s:.2f},{hi_s:.2f})" if np.isfinite(lo_s)
                   else f"< {hi_s:.2f}")
            print(f"  {lab:>20}{nn:>9}{p:>9.5f}"
                  f"{(p*EXEC_M-1)*100:>8.2f}%{(wl*EXEC_M-1)*100:>9.2f}%")

    # ---- verdict ---------------------------------------------------------
    print(f"\n{'='*78}")
    print("VERDICT")
    print("=" * 78)
    if best and best[0] > 0 and stable:
        print(f"  SELECTION WORKS. digits {best[2]}: OOS p={best[3]:.5f}, "
              f"EV {best[4]*100:+.2f}%, 99% low {best[0]*100:+.2f}%")
        print(f"  vs trading all digits at EV {(all_p*EXEC_M-1)*100:+.2f}%")
        print("  This is an implementable change to adaptive_sentinel: gate on entry")
        print("  digit as well as sigma. Confirm on a fresh block before sizing.")
    elif stable:
        print("  Ranking persists but no selection clears the 99% lower bound.")
        print("  Real structure, still not enough to cross breakeven at this sigma.")
        print("  Re-run as sigma decays — selection plus decay may cross together.")
    else:
        print("  Per-digit ranking does not persist out of sample. The chi2 reflects")
        print("  shape differences that do not survive as a stable win-rate ordering.")
        print("  Statistically real, not tradeable. This closes the entry-digit axis.")

    open(a.out, "w").write(
        f"# Entry-digit selection — {a.symbol}\n\n"
        f"payout {EXEC_M}, breakeven {BE*100:.2f}%. IS/OOS Spearman {rho:+.3f}.\n\n"
        + "| entry | n(IS) | p(IS) | n(OOS) | p(OOS) | OOS EV |\n|---|---|---|---|---|---|\n"
        + "".join(f"| {c} | {ni} | {pi:.5f} | {no} | {po:.5f} | "
                 f"{(po*EXEC_M-1)*100:+.2f}% |\n" for c, ni, pi, no, po, _ in rows))
    print(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
