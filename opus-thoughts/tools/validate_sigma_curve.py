"""Validate the sigma->edge curve on the 1M JD100 sample: bin ticks by rolling sigma_pips,
compute conditional P(U5|d=2), P(O4|d=7) per bin, compare to theory. The make-or-break test.
Run: python3 validate_sigma_curve.py
Out: ../results/sigma_curve_validation.md
"""
import gzip, math
import numpy as np
from sigma_edge_model import p_window_normal

def main():
    ts, ps = [], []
    with gzip.open("../data/JD100.csv.gz", "rt") as f:
        f.readline()
        for line in f:
            t, p = line.strip().split(",")
            ts.append(int(t)); ps.append(float(p))
    ps = np.array(ps); ts = np.array(ts)
    n = len(ps)
    span_days = (ts[-1] - ts[0]) / 86400
    v = np.round(ps * 100).astype(np.int64)
    st = np.diff(v)
    d = (v % 10).astype(np.int8)

    # rolling sigma over W=1800 no-jump steps (30 min), causal (uses past only)
    W = 1800
    absst = np.abs(st)
    nj = np.where(absst <= 20, st, 0).astype(float)
    njsq = nj * nj
    njc = (absst <= 20).astype(float)
    csum = np.concatenate([[0], np.cumsum(njsq)])
    ccnt = np.concatenate([[0], np.cumsum(njc)])
    sig = np.full(len(st), np.nan)
    for i in range(W, len(st)):
        cnt = ccnt[i] - ccnt[i - W]
        if cnt > 100:
            sig[i] = math.sqrt((csum[i] - csum[i - W]) / cnt)

    # trade rows: at tick i (digit d[i]), settle at tick i+1; gate on sig[i-1] (past steps only)
    rows = []
    BINS = [(3.6, 4.0), (4.0, 4.15), (4.15, 4.3), (4.3, 4.45), (4.45, 4.6), (4.6, 4.75), (4.75, 5.0)]
    md = [f"# JD100 sigma-curve validation — {n} ticks ({span_days:.1f} days), spot {ps.min():.2f}..{ps.max():.2f}\n\n",
          "| sigma bin | n(d=2) | P(U5|d2) | n(d=7) | P(O4|d7) | pooled P | theory P | EV@1.953 |\n",
          "|---|---:|---:|---:|---:|---:|---:|---:|\n"]
    for lo, hi in BINS:
        m = (sig[:-1] >= lo) & (sig[:-1] < hi)  # decision at tick index i corresponds to step idx i-1.. use approx alignment
        idx = np.where(m)[0]
        # digit index alignment: step i = v[i+1]-v[i]; decision tick = i (digit d[i]), outcome digit d[i+1]
        i2 = idx[d[idx] == 2]; i7 = idx[d[idx] == 7]
        u5 = np.isin(d[i2 + 1], [0, 1, 2, 3, 4]) if len(i2) else np.array([])
        o4 = np.isin(d[i7 + 1], [5, 6, 7, 8, 9]) if len(i7) else np.array([])
        k = int(u5.sum() + o4.sum()); ntr = len(i2) + len(i7)
        if ntr == 0: continue
        pooled = k / ntr
        mid = (lo + hi) / 2
        pt = p_window_normal(mid)
        md.append(f"| {lo}–{hi} | {len(i2)} | {u5.mean() if len(i2) else float('nan'):.4f} | {len(i7)} | "
                  f"{o4.mean() if len(i7) else float('nan'):.4f} | {pooled:.4f} | {pt:.4f} | {(pooled*1.953-1)*100:+.2f}% |\n")
    # all-sigma pooled + per-digit windows for completeness
    md.append("\n## All other digits (window {d-2..d+2} not an OU contract; shown for physics confirmation)\n\n")
    md.append("| d | P(next in d±2) lag1 | n |\n|---:|---:|---:|\n")
    for dd in range(10):
        m = d[:-1] == dd
        tgt = (d[1:][m] - dd) % 10
        pw = float(np.isin(tgt, [0, 1, 2, 8, 9]).mean())
        md.append(f"| {dd} | {pw:.4f} | {int(m.sum())} |\n")
    # spot percentiles
    md.append(f"\nspot percentiles: p5={np.percentile(ps,5):.1f} p50={np.percentile(ps,50):.1f} p95={np.percentile(ps,95):.1f}\n")
    sig_ok = sig[~np.isnan(sig)]
    md.append(f"sigma(30min rolling) percentiles: p5={np.percentile(sig_ok,5):.2f} p25={np.percentile(sig_ok,25):.2f} "
              f"p50={np.percentile(sig_ok,50):.2f} p75={np.percentile(sig_ok,75):.2f} p95={np.percentile(sig_ok,95):.2f}\n")
    frac = float((sig_ok <= 4.3).mean())
    md.append(f"fraction of time sigma<=4.3 (tradeable regime) over the sample: {frac:.3f}\n")
    open("../results/sigma_curve_validation.md", "w").write("".join(md))
    print("".join(md))

if __name__ == "__main__":
    main()
