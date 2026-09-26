"""redo_all.py — re-run every result that was computed on looped ticks.

WHAT WAS WRONG
deriv_api.history_paged() looped: 1.6M requested returned 86,401 unique epochs. The cause
was an unset `start` parameter (defaults to 1 day ago for style=ticks). derivfetch.py fixes
it and verifies integrity before returning.

WHAT NEEDS REDOING (everything above the 86,401 cap)
    regime_revalidate   1.2M  bins reported 170k samples that were really ~12k;
                              Wilson bounds too narrow by 3.7x. THE SIGMA-DECAY THESIS
                              RESTS ON THIS.
    surface_scan         600k  contract-family EV, CIs too narrow by 2.6x
    duration_surface     400k  duration axis, CIs too narrow by 2.1x
    local_vol            600k  vol clustering test
    battery_v2           600k  chi2 675.6 -> really ~98 vs null 81
    entry_select         800k  rho +0.879 measured on copies; dead on clean data
    cross_symbol          60k  UNDER THE CAP — unaffected, result stands

This script pulls one clean block and redoes the analyses that matter, with correct
sample sizes and correct thresholds throughout.

Read-only. Places no trades.

Run: python3 redo_all.py --ticks 1200000
     python3 redo_all.py --ticks 1200000 --symbol JD100
"""
import argparse, math
import numpy as np
from deriv_api import DerivWS
from derivfetch import (fetch_ticks, contiguous_pairs, sigma_series,
                        wilson, noise_range_pct)

EXEC_M = 1.8286          # executed OVER4/UNDER5, from payout_audit
BE = 1 / EXEC_M
WIN5 = [0, 1, 2, 8, 9]


def interleave(n, block=20000, purge=2000):
    a = np.zeros(n, bool); b = np.zeros(n, bool)
    for s in range(0, n, block):
        e = min(s + block, n)
        (a if (s // block) % 2 == 0 else b)[min(s + purge, e):max(e - purge, s)] = True
    return a, b


def spearman(x, y):
    rx = np.argsort(np.argsort(x)).astype(float)
    ry = np.argsort(np.argsort(y)).astype(float)
    return float(np.corrcoef(rx, ry)[0, 1])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="JD100")
    ap.add_argument("--ticks", type=int, default=1200000)
    ap.add_argument("--out", default="../results/redo_all.md")
    a = ap.parse_args()

    ws = DerivWS(token="")
    print(f"fetching {a.ticks} UNIQUE ticks of {a.symbol} (this takes a few minutes)...")
    t, p, pip = fetch_ticks(ws, a.symbol, a.ticks)
    ws.close()

    v = np.round(p * (10 ** pip)).astype(np.int64)
    cg = contiguous_pairs(t, v)
    dig = (v % 10).astype(int)
    ent = dig[:-1][cg]
    off = ((dig[1:] - dig[:-1]) % 10)[cg]
    win = np.isin(off, WIN5)
    st = np.diff(v)[cg]
    n = len(ent)
    sig_full = sigma_series(v, mask=cg)
    sig = sig_full[cg]
    ok = ~np.isnan(sig)
    print(f"  contiguous 1s pairs {n}, sigma "
          f"{np.nanmin(sig):.2f}-{np.nanmax(sig):.2f}\n")

    md = [f"# Re-analysis on clean ticks — {a.symbol}\n\n",
          f"{len(t)} unique ticks, {(t[-1]-t[0])/86400:.2f} days, "
          f"{n} contiguous pairs.\n\n"]

    # ================================================================== 1
    print("=" * 76)
    print("1. SIGMA-BINNED EDGE — the sigma-decay thesis, with CORRECT sample sizes")
    print(f"   executed payout {EXEC_M}, breakeven {BE*100:.2f}%")
    print("=" * 76)
    is_m, oos_m = interleave(n)
    binw = 0.1
    bins = np.round(sig / binw)
    print(f"{'sigma':>7}{'n(OOS)':>10}{'p':>9}{'vs BE':>11}{'EV':>9}{'99% low':>10}"
          f"{'99% high':>10}")
    rows = []
    for b in sorted(set(bins[ok].astype(int))):
        m = ok & (bins == b) & oos_m
        nn = int(m.sum())
        if nn < 3000:
            continue
        k = int(win[m].sum())
        pp = k / nn
        lo, hi = wilson(k, nn)
        rows.append((b * binw, nn, pp, lo, hi))
        print(f"{b*binw:>7.2f}{nn:>10}{pp:>9.5f}{(pp-BE)*100:>+10.3f}pp"
              f"{(pp*EXEC_M-1)*100:>8.2f}%{(lo*EXEC_M-1)*100:>9.2f}%"
              f"{(hi*EXEC_M-1)*100:>9.2f}%")
    md.append("## Sigma-binned edge\n\n| sigma | n | p | EV | 99% CI |\n|---|---|---|---|---|\n")
    for s, nn, pp, lo, hi in rows:
        md.append(f"| {s:.2f} | {nn} | {pp:.5f} | {(pp*EXEC_M-1)*100:+.2f}% | "
                  f"[{(lo*EXEC_M-1)*100:+.2f}%, {(hi*EXEC_M-1)*100:+.2f}%] |\n")

    if len(rows) >= 3:
        xs = np.array([r[0] for r in rows])
        ys = np.array([(r[2] * EXEC_M - 1) * 100 for r in rows])
        wts = np.array([r[1] for r in rows], dtype=float)
        b1, b0 = np.polyfit(xs, ys, 1, w=np.sqrt(wts))
        print(f"\n  weighted fit: EV% = {b0:.2f} + {b1:.2f} * sigma")
        if b1 < 0:
            s0 = -b0 / b1
            print(f"  crosses zero at sigma {s0:.2f}")
            cur = float(np.nanmedian(sig[-50000:]))
            drag = -6.71e-3
            if s0 < cur:
                d = math.log(s0 / cur) / drag
                print(f"  current sigma {cur:.2f} -> spot must fall "
                      f"{(1-s0/cur)*100:.1f}%, about {d:.0f} days at -0.67%/day")
            else:
                print(f"  current sigma {cur:.2f} is ALREADY below that")
            md.append(f"\nWeighted fit EV% = {b0:.2f} + {b1:.2f}*sigma, "
                      f"zero at sigma {s0:.2f}.\n")

    # ================================================================== 2
    print(f"\n{'='*76}")
    print("2. ENTRY-DIGIT SPREAD — against the CORRECT threshold")
    print("=" * 76)
    ps, ns = [], []
    pa, pb = [], []
    half = n // 2
    for c in range(10):
        m = ent == c
        nn = int(m.sum())
        if nn < 2000:
            continue
        ps.append(float(win[m].mean())); ns.append(nn)
        pa.append(float(win[:half][ent[:half] == c].mean()))
        pb.append(float(win[half:][ent[half:] == c].mean()))
    se = math.sqrt(0.53 * 0.47 / (n / 10)) * 100
    spread = (max(ps) - min(ps)) * 100
    thr95 = noise_range_pct(se, 10, 95)
    rho = spearman(np.array(pa), np.array(pb))
    print(f"  n per digit ~{n//10}, SE {se:.3f}pp")
    print(f"  spread {spread:.2f}pp   noise 95th pct {thr95:.2f}pp   "
          f"-> {'ABOVE' if spread > thr95 else 'within noise'}")
    print(f"  half-A/half-B rho {rho:+.3f}   (noise 95th ~ +0.55)")
    print(f"  best digits {[c for c in np.argsort(ps)[::-1][:2]]}, "
          f"best EV {(max(ps)*EXEC_M-1)*100:+.2f}%")
    print("\n  earlier runs reported spread 2.42pp at n=86k and 0.94pp at n=400k —")
    print("  scaling as 1/sqrt(n), which is the signature of noise, not signal.")
    md.append(f"\n## Entry digit\n\nspread {spread:.2f}pp vs noise 95th {thr95:.2f}pp, "
              f"rho {rho:+.3f}.\n")

    # ================================================================== 3
    print(f"\n{'='*76}")
    print("3. RANDOMNESS — chi2 with correct n, no replication inflation")
    print("=" * 76)
    tab = np.zeros((10, 10))
    np.add.at(tab, (ent, off), 1)
    rs, cs = tab.sum(1, keepdims=True), tab.sum(0, keepdims=True)
    exp = rs @ cs / tab.sum()
    chi2 = float(((tab - exp) ** 2 / np.maximum(exp, 1e-9)).sum())
    print(f"  entry/offset chi2 = {chi2:.1f} on 81 df   (null mean 81, "
          f"p=0.01 critical ~113)")
    print(f"  -> {'dependence present' if chi2 > 113 else 'consistent with independence'}")
    print(f"  battery_v2 reported 675.6 on looped data; /6.9 replication = 98.0")

    absst = np.abs(st).astype(float)
    x = absst - absst.mean()
    d0 = float((x * x).sum())
    worst = max(((abs(float((x[:-l] * x[l:]).sum()) / d0), l) for l in range(1, 51)))
    thr = 5 / math.sqrt(len(x))
    print(f"\n  vol clustering: worst |autocorr| {worst[0]:.5f} at lag {worst[1]}, "
          f"threshold {thr:.5f}")
    print(f"  -> {'clustering present' if worst[0] > thr else 'none — W=1800 is fine'}")
    md.append(f"\n## Randomness\n\nchi2 {chi2:.1f} on 81 df; worst |step| autocorr "
              f"{worst[0]:.5f} vs threshold {thr:.5f}.\n")

    # ================================================================== 4
    print(f"\n{'='*76}")
    print("4. DURATION AXIS — entry-conditioned, correct n")
    print("=" * 76)
    print(f"{'N':>4}{'best p':>10}{'entry':>7}{'n':>10}{'EV':>9}{'99% low':>10}")
    for N in (1, 2, 3, 5, 10):
        if N >= len(dig) - 1:
            continue
        e2 = dig[:-N]
        o2 = (dig[N:] - dig[:-N]) % 10
        w2 = np.isin(o2, WIN5)
        best = None
        for c in range(10):
            m = e2 == c
            nn = int(m.sum())
            if nn < 3000:
                continue
            k = int(w2[m].sum()); pp = k / nn
            lo, _ = wilson(k, nn)
            if best is None or pp > best[0]:
                best = (pp, c, nn, lo)
        if best:
            print(f"{N:>4}{best[0]:>10.5f}{best[1]:>7}{best[2]:>10}"
                  f"{(best[0]*EXEC_M-1)*100:>8.2f}%{(best[3]*EXEC_M-1)*100:>9.2f}%")

    print(f"\n{'='*76}")
    print("VERDICT")
    print("=" * 76)
    pos = [r for r in rows if (r[3] * EXEC_M - 1) > 0]
    if pos:
        print("  Sigma bins with a POSITIVE 99% lower bound:")
        for s, nn, pp, lo, hi in pos:
            print(f"    sigma {s:.2f}: n={nn} EV {(pp*EXEC_M-1)*100:+.2f}% "
                  f"99% low {(lo*EXEC_M-1)*100:+.2f}%")
        print("  These are on clean, deduplicated ticks with honest intervals.")
    else:
        print("  No sigma bin has a positive 99% lower bound on clean data.")
        print("  The edge is not present at any sigma in this sample.")
    open(a.out, "w").write("".join(md))
    print(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
