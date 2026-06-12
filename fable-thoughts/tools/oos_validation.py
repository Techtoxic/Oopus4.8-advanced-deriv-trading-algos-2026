"""oos_validation.py — out-of-sample test of the opus-thoughts sigma model on ticks the model
has NEVER seen (everything after the opus sample cut 2026-06-11 15:38 UTC).
For every tick: causal rolling sigma (same 1800-step window as the backtest), wrapped-normal
pmf centered on current digit, price all 40 contracts on the measured payout grid, trade best
if model EV > gate, settle on the actual next tick. Also bins empirical P vs model P by sigma.
Run: python3 oos_validation.py [gate=0.005]
"""
import sys, gzip, math
import numpy as np
from sigma_model import wrapped_normal_pmf, winset, GRID

def load(path):
    ts, ps = [], []
    with gzip.open(path, "rt") as f:
        f.readline()
        for line in f:
            a, b = line.strip().split(",")
            ts.append(int(a)); ps.append(float(b))
    return np.array(ts), np.array(ps)

def main():
    gate = float(sys.argv[1]) if len(sys.argv) > 1 else 0.005
    ts, ps = load("../data/JD100_oos.csv.gz")
    v = np.round(ps * 100).astype(np.int64)
    d = (v % 10).astype(np.int8)
    st = np.diff(v)
    n = len(v)
    W = 1800
    absst = np.abs(st)
    njsq = np.where(absst <= 20, st.astype(float) ** 2, 0.0)
    njc = (absst <= 20).astype(float)
    csum = np.concatenate([[0], np.cumsum(njsq)]); ccnt = np.concatenate([[0], np.cumsum(njc)])

    cache = {}
    trades = []
    equity = 0.0
    hour_pnl = {}
    # calibration accumulators: sigma bin -> [model_p_sum, wins, n] for the *chosen* contract
    cal = {}
    for i in range(W + 1, n - 1):
        cnt = ccnt[i - 1] - ccnt[i - 1 - W]
        if cnt < 200: continue
        sig = math.sqrt((csum[i - 1] - csum[i - 1 - W]) / cnt)
        key = (round(sig / 0.05), int(d[i]))
        if key not in cache:
            cache[key] = wrapped_normal_pmf(key[0] * 0.05, key[1])
        pmf = cache[key]
        best = None
        for (t, b), M in GRID.items():
            p = sum(pmf[x] for x in winset(t, b))
            ev = p * M - 1
            if best is None or ev > best[3]: best = (t, b, p, ev, M)
        if best[3] <= gate: continue
        won = int(d[i + 1]) in winset(best[0], best[1])
        pnl = (best[4] - 1) if won else -1.0
        equity += pnl
        hr = ts[i] // 3600
        hour_pnl[hr] = hour_pnl.get(hr, 0.0) + pnl
        sb = round(sig * 10) / 10
        c = cal.setdefault(sb, [0.0, 0, 0])
        c[0] += best[2]; c[1] += int(won); c[2] += 1
        trades.append((sig, best[0], best[1], best[3], won, pnl))

    md = [f"# OUT-OF-SAMPLE validation — JD100, {n} fresh ticks (after opus cut), gate={gate}\n\n",
          f"spot range {ps.min():.2f}..{ps.max():.2f}; trades fired: {len(trades)}\n\n"]
    if trades:
        arr = np.array([t[5] for t in trades])
        ev_arr = np.array([t[3] for t in trades])
        wr = np.mean([t[4] for t in trades])
        se = arr.std() / math.sqrt(len(arr))
        md.append(f"- total PnL ($1 stakes): **{equity:+.2f}** over {len(trades)} trades = "
                  f"**{equity/len(trades)*100:+.2f}%/trade** (model-EV avg {ev_arr.mean()*100:+.2f}%)\n")
        md.append(f"- win rate {wr:.4f}; t-stat {arr.mean()/se:+.2f}\n")
        from collections import Counter
        cc = Counter((t[1], t[2]) for t in trades)
        md.append(f"- contracts: {dict(cc.most_common(8))}\n\n")
        md.append("## Calibration: model P vs realized win rate (chosen contract, by sigma)\n\n")
        md.append("| sigma | n | model P | realized P | realized EV |\n|---:|---:|---:|---:|---:|\n")
        for sb in sorted(cal):
            mp, w, m = cal[sb]
            if m < 30: continue
            md.append(f"| {sb:.1f} | {m} | {mp/m:.4f} | {w/m:.4f} | {(w/m)*1.953-1:+.2%} |\n")
        md.append("\n## PnL by hour (UTC)\n\n")
        for hr in sorted(hour_pnl):
            import time as _t
            md.append(f"- {_t.strftime('%m-%d %H:00', _t.gmtime(hr*3600))}: {hour_pnl[hr]:+.1f}\n")
    else:
        md.append("- no trades fired\n")
    out = "".join(md)
    open("../results/oos_validation.md", "w").write(out)
    print(out)

if __name__ == "__main__":
    main()
