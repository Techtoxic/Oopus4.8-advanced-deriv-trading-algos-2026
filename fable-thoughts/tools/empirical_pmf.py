"""empirical_pmf.py — replace the wrapped-normal assumption with the measured step
distribution. Build P(Δ mod 10 = k | sigma bin) tables from the big in-sample JD100 file
(1.21M ticks), then OOS-test pricing with those tables on the fresh post-cut ticks.

Why: OOS showed realized P consistently 0.3–1.4pp below wrapped-normal model P. Two suspects:
(1) real steps are heavier-tailed than normal (vol clustering) -> less mod-10 concentration;
(2) selection on a noisy sigma estimate (winner's curse): when the rolling estimate dips, true
sigma is on average higher. An empirical table keyed on the SAME rolling estimator kills both
biases at once, because the table inherits the estimator's noise characteristics.

Run: python3 empirical_pmf.py [gate=0.005]
"""
import sys, gzip, math, json
import numpy as np
from sigma_model import winset, GRID, wrapped_normal_pmf

W = 1800

def load(path):
    ts, ps = [], []
    with gzip.open(path, "rt") as f:
        f.readline()
        for line in f:
            a, b = line.strip().split(",")
            ts.append(int(a)); ps.append(float(b))
    return np.array(ts), np.array(ps)

def rolling_sigma(v):
    st = np.diff(v)
    absst = np.abs(st)
    njsq = np.where(absst <= 20, st.astype(float) ** 2, 0.0)
    njc = (absst <= 20).astype(float)
    csum = np.concatenate([[0], np.cumsum(njsq)]); ccnt = np.concatenate([[0], np.cumsum(njc)])
    # sig[i] = sigma of steps in window ending at step i-1 (causal for decision at tick i)
    sig = np.full(len(v), np.nan)
    for i in range(W + 1, len(v)):
        cnt = ccnt[i - 1] - ccnt[i - 1 - W]
        if cnt >= 200:
            sig[i] = math.sqrt((csum[i - 1] - csum[i - 1 - W]) / cnt)
    return sig, st

def build_tables(v, sig, st, binw=0.1):
    """tables[bin] = pmf over (Δ mod 10) given causal sigma estimate in bin."""
    tables = {}
    counts = {}
    mod = ((st + 5) % 10) - 5  # -5..4 offsets
    for i in range(W + 1, len(v) - 1):
        s = sig[i]
        if np.isnan(s): continue
        b = round(s / binw)
        c = counts.setdefault(b, np.zeros(10, dtype=np.int64))
        c[(mod[i]) % 10] += 1   # offset of next step (tick i -> i+1) is st[i]
    for b, c in counts.items():
        tot = c.sum()
        if tot >= 2000:
            tables[b] = (c / tot).tolist()
    return tables

def pmf_from_offsets(off_pmf, center):
    return [off_pmf[(dd - center) % 10] for dd in range(10)]

def main():
    gate = float(sys.argv[1]) if len(sys.argv) > 1 else 0.005
    binw = 0.1
    # IN-SAMPLE: build tables
    ts1, ps1 = load("../../opus-thoughts/data/JD100.csv.gz")
    v1 = np.round(ps1 * 100).astype(np.int64)
    sig1, st1 = rolling_sigma(v1)
    tables = build_tables(v1, sig1, st1, binw)
    json.dump({str(k): v for k, v in tables.items()}, open("../results/empirical_offset_tables.json", "w"))
    # OOS: trade with tables
    ts2, ps2 = load("../data/JD100_oos.csv.gz")
    v2 = np.round(ps2 * 100).astype(np.int64)
    d2 = (v2 % 10).astype(np.int8)
    sig2, st2 = rolling_sigma(v2)
    n = len(v2)
    trades = []
    equity = 0.0
    cal = {}
    for i in range(W + 1, n - 1):
        s = sig2[i]
        if np.isnan(s): continue
        b = round(s / binw)
        if b not in tables: continue
        pmf = pmf_from_offsets(tables[b], int(d2[i]))
        best = None
        for (t, bar), M in GRID.items():
            p = sum(pmf[x] for x in winset(t, bar))
            ev = p * M - 1
            if best is None or ev > best[3]: best = (t, bar, p, ev, M)
        if best[3] <= gate: continue
        won = int(d2[i + 1]) in winset(best[0], best[1])
        pnl = (best[4] - 1) if won else -1.0
        equity += pnl
        sb = round(s * 10) / 10
        c = cal.setdefault(sb, [0.0, 0.0, 0.0, 0])
        c[0] += best[2]; c[1] += best[3]; c[2] += pnl; c[3] += 1
        trades.append(pnl)
    md = [f"# Empirical-pmf OOS test — gate={gate}, tables from 1.21M in-sample ticks\n\n",
          f"trades: {len(trades)}\n"]
    if trades:
        arr = np.array(trades)
        se = arr.std() / math.sqrt(len(arr))
        md.append(f"- total {arr.sum():+.2f} = {arr.mean()*100:+.2f}%/trade, t={arr.mean()/se:+.2f}\n\n")
        md.append("| sigma | n | model P | model EV | realized PnL/trade |\n|---:|---:|---:|---:|---:|\n")
        for sb in sorted(cal):
            mp, mev, pnl, m = cal[sb]
            if m < 30: continue
            md.append(f"| {sb:.1f} | {m} | {mp/m:.4f} | {mev/m*100:+.2f}% | {pnl/m*100:+.2f}% |\n")
    out = "".join(md)
    open("../results/empirical_pmf_oos.md", "w").write(out)
    print(out)

if __name__ == "__main__":
    main()
