"""adaptive_backtest.py — proves the regime-adaptive layer (the unfinished 25%).

The base edge (digit clusters near current digit because price moves a small step) is real but
ONLY positive while rolling sigma is low (~<=4.4). JD100 spot/sigma drifts up and down over days
with no anchor, so a static bot bleeds whenever sigma drifts into the negative zone.

This backtest runs the SAME strategy three ways on the wide-regime June-11 data (spot 223-440,
sigma 4-7) and the fresh June-29 data, and reports per-day PnL:
  A) STATIC ungated         — trade every tick (what bled the account)
  B) HARD sigma gate        — trade only when rolling sigma <= SIGMA_MAX
  C) ADAPTIVE self-retrain   — trailing-window offset table (recalibrates continuously) +
                               per-contract EV gate computed from that trailing table + sigma gate

Everything causal: tables/sigma at tick i use only ticks < i. Contract chosen a-priori is the
OVER/UNDER whose 5-wide window is centered on the current digit (no max-over-contracts selection).
Run: python3 adaptive_backtest.py
"""
import gzip, math, sys
import numpy as np

PAY = 1.953                 # OVER/UNDER 5-wide window live payout (verified +-0.15%)
SIGMA_MAX = 4.25            # hard regime gate (zero-cross ~4.45; 0.2 safety margin)
TRAIL = 60000              # trailing ticks for self-recalibrating table (~16h)
WSIG = 1800                # rolling sigma window
EV_GATE = 0.005

def load(p):
    ts, ps = [], []
    with gzip.open(p, "rt") as f:
        f.readline()
        for line in f:
            a, b = line.strip().split(","); ts.append(int(a)); ps.append(float(b))
    return np.array(ts), np.array(ps)

def window(dig):
    return [(dig + k) % 10 for k in (-2, -1, 0, 1, 2)]

def run(path, label):
    ts, ps = load(path)
    v = np.round(ps * 100).astype(np.int64); d = v % 10
    st = np.diff(v); absst = np.abs(st); n = len(v)
    njsq = np.where(absst <= 20, st.astype(float) ** 2, 0.0); njc = (absst <= 20).astype(float)
    csum = np.concatenate([[0], np.cumsum(njsq)]); ccnt = np.concatenate([[0], np.cumsum(njc)])
    sig = np.full(n, np.nan)
    for i in range(WSIG + 1, n):
        c = ccnt[i - 1] - ccnt[i - 1 - WSIG]
        if c >= 200: sig[i] = math.sqrt((csum[i - 1] - csum[i - 1 - WSIG]) / c)
    off = (np.diff(d)) % 10  # off[i] = offset of step i (d[i]->d[i+1])

    res = {}
    for mode in ("A_static", "B_gated", "C_adaptive"):
        eq = 0.0; trades = 0; wins = 0; day_pnl = {}
        # rolling offset histogram for adaptive mode (trailing TRAIL no-jump steps)
        hist = np.zeros(10); from collections import deque; q = deque()
        for i in range(WSIG + 1, n - 1):
            s = sig[i]
            if np.isnan(s): continue
            # maintain trailing table from steps strictly before i
            if mode == "C_adaptive":
                # add step i-1 (completed, uses past only)
                if absst[i - 1] <= 20:
                    hist[off[i - 1]] += 1; q.append(off[i - 1])
                    if len(q) > TRAIL: hist[q.popleft()] -= 1
            if absst[i] > 20: continue
            dig = int(d[i]); win = window(dig)
            if mode == "A_static":
                pass  # always trade the window rule
            elif mode == "B_gated":
                if s > SIGMA_MAX: continue
            elif mode == "C_adaptive":
                if s > SIGMA_MAX: continue
                tot = hist.sum()
                if tot < 5000: continue
                # EV from trailing table: window sits on offsets {-2..2} -> p = sum table at those offsets
                p_est = sum(hist[k % 10] for k in (-2, -1, 0, 1, 2)) / tot
                if p_est * PAY - 1 <= EV_GATE: continue
            won = int(d[i + 1]) in win
            pnl = (PAY - 1) if won else -1.0
            eq += pnl; trades += 1; wins += int(won)
            day = ts[i] // 86400; day_pnl[day] = day_pnl.get(day, 0.0) + pnl
        if trades:
            res[mode] = (trades, wins / trades, eq, eq / trades * 100, day_pnl)
    print(f"\n===== {label} ({n} ticks, spot {ps.min():.0f}-{ps.max():.0f}) =====")
    for mode, (tr, wr, eq, ept, dp) in res.items():
        t = (ept/100) / ((np.std([1])*0+1.0)/math.sqrt(tr))  # rough; recompute below
        print(f"{mode:12s}: {tr:7d} trades  wr={wr:.4f}  PnL={eq:+8.1f}  {ept:+.2f}%/trade")
    # per-day comparison static vs adaptive
    if "C_adaptive" in res and "A_static" in res:
        days = sorted(set(res["A_static"][4]) | set(res["C_adaptive"][4]))
        print("  per-day PnL (static -> adaptive):")
        for dd in days:
            a = res["A_static"][4].get(dd, 0.0); c = res["C_adaptive"][4].get(dd, 0.0)
            ntr = "" 
            print(f"    day {dd%1000}: static {a:+7.1f}  adaptive {c:+7.1f}")
    return res

if __name__ == "__main__":
    run("../../opus-thoughts/data/JD100.csv.gz", "WIDE REGIME June-11 (good+bad)")
    run("../data/JD100_jun29_v2.csv.gz", "FRESH June-29 (currently good)")
