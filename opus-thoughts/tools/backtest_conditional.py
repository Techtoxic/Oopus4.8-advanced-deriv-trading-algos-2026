"""Replay backtest of the sigma-gated conditional digit strategy on clean harvested ticks.
At each tick t: rolling causal sigma -> wrapped-normal pmf centered on digit(t) -> price all
40 contracts at the standard payout grid -> trade best if model EV > gate. Settles on tick t+1
(optionally degraded to t+2 with prob p_miss to model latency slips).
Run: python3 backtest_conditional.py JD100 [gate=0.005] [p_miss=0.0]
Out: ../results/backtest_JD100.md
"""
import sys, gzip, math
import numpy as np
from sigma_sentinel import wrapped_normal_pmf, winset, CONTRACTS

GRID = {}  # standard 18-symbol payout grid (measured live this session)
_OU = {0: 1.096, 1: 1.232, 2: 1.404, 3: 1.634, 4: 1.953, 5: 2.427, 6: 3.205, 7: 4.717, 8: 8.929}
for d in range(10):
    GRID[("DIGITMATCH", d)] = 8.929
    GRID[("DIGITDIFF", d)] = 1.0958
for k in range(9): GRID[("DIGITOVER", k)] = _OU[k]
for k in range(1, 10): GRID[("DIGITUNDER", k)] = _OU[9 - k]
GRID[("DIGITEVEN", None)] = GRID[("DIGITODD", None)] = 1.953

def main():
    sym = sys.argv[1] if len(sys.argv) > 1 else "JD100"
    gate = float(sys.argv[2]) if len(sys.argv) > 2 else 0.005
    p_miss = float(sys.argv[3]) if len(sys.argv) > 3 else 0.0
    ts, ps = [], []
    with gzip.open(f"../data/{sym}.csv.gz", "rt") as f:
        f.readline()
        for line in f:
            a, b = line.strip().split(",")
            ts.append(int(a)); ps.append(float(b))
    ts = np.array(ts); v = np.round(np.array(ps) * 100).astype(np.int64)
    d = (v % 10).astype(np.int8)
    st = np.diff(v)
    n = len(v)

    # causal rolling sigma over 1800 no-jump steps
    W = 1800
    absst = np.abs(st)
    njsq = np.where(absst <= 20, st.astype(float) ** 2, 0.0)
    njc = (absst <= 20).astype(float)
    csum = np.concatenate([[0], np.cumsum(njsq)]); ccnt = np.concatenate([[0], np.cumsum(njc)])
    rng = np.random.default_rng(3)

    # precompute pmf cache on sigma grid of 0.05
    cache = {}
    trades = []
    equity = 0.0
    day_pnl = {}
    for i in range(W + 1, n - 2):
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
            if best is None or ev > best[2]: best = (t, b, ev, M)
        if best[2] <= gate: continue
        # settle
        j = i + 1 if (p_miss == 0 or rng.random() > p_miss) else i + 2
        won = int(d[j]) in winset(best[0], best[1])
        pnl = (best[3] - 1) if won else -1.0
        equity += pnl
        day = ts[i] // 86400
        day_pnl[day] = day_pnl.get(day, 0.0) + pnl
        trades.append((sig, best[0], best[1], best[2], won, pnl))
    md = [f"# Backtest {sym} — sigma-gated conditional strategy ($1 stakes, gate={gate}, p_miss={p_miss})\n\n",
          f"sample: {n} ticks ({(ts[-1]-ts[0])/86400:.1f} days)\n\n"]
    if trades:
        arr_pnl = np.array([t[5] for t in trades]); arr_ev = np.array([t[3] for t in trades])
        wr = np.mean([t[4] for t in trades])
        md.append(f"- trades: {len(trades)}  win rate: {wr:.4f}  total PnL: {equity:+.2f}  "
                  f"avg PnL/trade: {equity/len(trades)*100:+.2f}%  avg model-EV: {arr_ev.mean()*100:+.2f}%\n")
        se = arr_pnl.std() / math.sqrt(len(arr_pnl))
        md.append(f"- PnL/trade t-stat: {arr_pnl.mean()/se:+.2f}\n")
        from collections import Counter
        cc = Counter((t[1], t[2]) for t in trades)
        md.append(f"- contracts used: {dict(cc)}\n- per-day PnL: " +
                  ", ".join(f"d{k%100}:{v:+.1f}" for k, v in sorted(day_pnl.items())) + "\n")
    else:
        md.append("- no trades fired (regime never opened at this gate)\n")
    open(f"../results/backtest_{sym}.md", "w").write("".join(md))
    print("".join(md))

if __name__ == "__main__":
    main()
