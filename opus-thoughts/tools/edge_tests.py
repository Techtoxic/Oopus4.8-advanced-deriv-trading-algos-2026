"""Core edge tests on harvested ticks (H1 adversarial, H2 step physics, conditionals, n+1).
Run: python3 edge_tests.py JD100 R_100 1HZ100V 1HZZ10V ...
Out: ../results/edge_tests_{sym}.json + prints; RESULTS.md assembled separately.
"""
import sys, gzip, json, math, os
import numpy as np

def load(sym):
    ts, ps = [], []
    with gzip.open(f"../data/{sym}.csv.gz", "rt") as f:
        header = f.readline()
        pip = int(header.strip().split("=")[1])
        for line in f:
            t, p = line.strip().split(",")
            ts.append(int(t)); ps.append(p)
    return np.array(ts), ps, pip

def digits_arr(prices, pip):
    return np.array([int(f"{float(p):.{pip}f}"[-1]) for p in prices], dtype=np.int8)

def steps_arr(prices, pip):
    scale = 10 ** pip
    v = np.array([round(float(p) * scale) for p in prices], dtype=np.int64)
    return np.diff(v)

def wilson(k, n, z=2.576):  # 99%
    if n == 0: return (0.0, 1.0)
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (c - h, c + h)

def contract_sets():
    cs = {}
    for d in range(10):
        cs[f"MATCH{d}"] = {d}
        cs[f"DIFF{d}"] = set(range(10)) - {d}
    for k in range(9):
        cs[f"OVER{k}"] = set(range(k + 1, 10))
    for k in range(1, 10):
        cs[f"UNDER{k}"] = set(range(0, k))
    cs["EVEN"] = {0, 2, 4, 6, 8}; cs["ODD"] = {1, 3, 5, 7, 9}
    return cs

def payout_map(sym):
    """live payouts from scanner if present, else defaults."""
    M = {}
    path = "../results/payout_surface.json"
    if os.path.exists(path):
        try:
            surf = json.load(open(path))["surface"].get(sym, {}).get("1", [])
            for r in surf:
                if "M" in r:
                    b = r["barrier"] if r["barrier"] is not None else ""
                    M[r["type"].replace("DIGIT", "") + str(b)] = r["M"]
        except Exception:
            pass
    if not M:
        base = {"MATCH": 8.93, "DIFF": 1.0958, "EVEN": 1.953, "ODD": 1.953}
        ou = {0: 1.096, 1: 1.232, 2: 1.404, 3: 1.634, 4: 1.953, 5: 2.427, 6: 3.205, 7: 4.717, 8: 8.929}
        for d in range(10):
            M[f"MATCH{d}"] = base["MATCH"]; M[f"DIFF{d}"] = base["DIFF"]
        for k in range(9): M[f"OVER{k}"] = ou[k]
        for k in range(1, 10): M[f"UNDER{k}"] = ou[9 - k]
        M["EVEN"] = M["ODD"] = base["EVEN"]
    return M

def analyze(sym):
    ts, prices, pip = load(sym)
    d = digits_arr(prices, pip)
    st = steps_arr(prices, pip)
    n = len(d)
    out = {"symbol": sym, "n": n, "pip": pip}

    iv = np.diff(ts)
    vals, cnts = np.unique(iv, return_counts=True)
    out["tick_interval"] = {int(v): int(c) for v, c in zip(vals[:6], cnts[:6])}
    out["monotonic_time"] = bool((iv > 0).all())

    # marginal
    cnt = np.bincount(d, minlength=10)
    out["digit_freq"] = (cnt / n).round(5).tolist()
    out["digit_tv"] = float(0.5 * np.abs(cnt / n - 0.1).sum())
    out["digit_max_z"] = float(np.max(np.abs(cnt / n - 0.1)) / math.sqrt(0.09 / n))

    # step physics
    out["sigma_pips"] = float(st.std())
    out["median_abs_step"] = float(np.median(np.abs(st)))
    out["p_small_step(|s|<=5)"] = float((np.abs(st) <= 5).mean())
    mod = np.bincount(st % 10, minlength=10)
    out["step_mod10"] = (mod / len(st)).round(5).tolist()
    out["step_mod10_tv"] = float(0.5 * np.abs(mod / len(st) - 0.1).sum())
    out["step_mod10_max_z"] = float(np.max(np.abs(mod / len(st) - 0.1)) / math.sqrt(0.09 / len(st)))
    # jump split (jump = |step| > 6 sigma-of-median-scale)
    mad = np.median(np.abs(st - np.median(st))) * 1.4826
    jthr = max(6 * mad, 20)
    isj = np.abs(st) > jthr
    out["jump_frac"] = float(isj.mean()); out["jump_thr_pips"] = float(jthr)
    if isj.any() and (~isj).sum() > 1000:
        nm = np.bincount(st[~isj] % 10, minlength=10) / (~isj).sum()
        out["step_mod10_nojump"] = nm.round(5).tolist()
        out["step_mod10_nojump_tv"] = float(0.5 * np.abs(nm - 0.1).sum())
        out["sigma_pips_nojump"] = float(st[~isj].std())

    # conditional next-digit tables, lag 1 and 2
    M = payout_map(sym)
    cs = contract_sets()
    best = []
    for lag in (1, 2):
        a, b = d[:-lag], d[lag:]
        tab = np.zeros((10, 10))
        for i in range(10):
            m = a == i
            ni = int(m.sum())
            if ni: tab[i] = np.bincount(b[m], minlength=10) / ni
        out[f"cond_lag{lag}"] = tab.round(5).tolist()
        out[f"cond_lag{lag}_rowN"] = [int((a == i).sum()) for i in range(10)]
        # price every contract conditionally
        for i in range(10):
            ni = int((a == i).sum())
            if ni < 1000: continue
            for cname, cset in cs.items():
                if cname not in M: continue
                k = int(np.isin(b[(a == i)], list(cset)).sum())
                p = k / ni
                lo, hi = wilson(k, ni)
                ev = p * M[cname] - 1
                ev_lo = lo * M[cname] - 1
                if ev_lo > 0:
                    best.append(dict(lag=lag, cond_digit=i, contract=cname, n=ni, p=round(p, 5),
                                     wilson_lo=round(lo, 5), M=M[cname], ev=round(ev, 5),
                                     ev_wilson_lo=round(ev_lo, 5)))
    best.sort(key=lambda r: -r["ev_wilson_lo"])
    out["positive_ev_conditionals"] = best[:25]

    # ---- H1 adversarial: trailing-window rank test ----
    W = 1000
    if n > W + 5000:
        freq = np.zeros(10, dtype=np.int32)
        for i in range(W):
            freq[d[i]] += 1
        hot_hits = cold_hits = 0; trials = 0
        rank_hits = np.zeros(10, dtype=np.int64)  # rank 0 = hottest
        for t in range(W, n - 1):
            order = np.argsort(-freq, kind="stable")
            ranks = np.empty(10, dtype=np.int64); ranks[order] = np.arange(10)
            nxt = d[t]  # digit at t given window [t-W, t)
            rank_hits[ranks[nxt]] += 1
            if nxt == order[0]: hot_hits += 1
            if nxt == order[-1]: cold_hits += 1
            trials += 1
            freq[d[t]] += 1; freq[d[t - W]] -= 1
        out["adversarial"] = dict(window=W, trials=trials,
            p_hit_hottest=round(hot_hits / trials, 5), p_hit_coldest=round(cold_hits / trials, 5),
            hottest_wilson=wilson(hot_hits, trials), coldest_wilson=wilson(cold_hits, trials),
            rank_curve=(rank_hits / trials).round(5).tolist())

    # ---- overdue/gap hazard ----
    last_seen = {}; gaps = []
    for i, x in enumerate(d):
        if x in last_seen: gaps.append(i - last_seen[x])
        last_seen[x] = i
    gaps = np.array(gaps)
    haz = []
    for k in range(1, 41):
        at_risk = (gaps >= k).sum()
        hit = (gaps == k).sum()
        if at_risk > 500:
            haz.append((k, int(hit), int(at_risk), round(hit / at_risk, 5)))
    out["gap_hazard"] = haz  # flat 0.1 expected
    out["max_gap"] = int(gaps.max())

    # ---- parity streak flip ----
    par = d % 2
    flips = []
    streak = 1
    rec = {}
    for i in range(1, len(par)):
        same = par[i] == par[i - 1]
        L = streak
        rec.setdefault(L, [0, 0])
        rec[L][1] += 1
        if not same: rec[L][0] += 1
        streak = streak + 1 if same else 1
    out["parity_flip_by_streak"] = {k: (v[0], v[1], round(v[0] / v[1], 5)) for k, v in sorted(rec.items()) if v[1] > 500}

    json.dump(out, open(f"../results/edge_tests_{sym}.json", "w"), indent=1)
    return out

def brief(o):
    print(f"\n=== {o['symbol']} (n={o['n']}, interval={o['tick_interval']}) ===")
    print(f" digit TV={o['digit_tv']:.5f} max_z={o['digit_max_z']:.2f}")
    print(f" sigma={o['sigma_pips']:.2f} pips (nojump {o.get('sigma_pips_nojump','-')}) med|s|={o['median_abs_step']:.1f} P(|s|<=5)={o['p_small_step(|s|<=5)']:.4f}")
    print(f" step mod10 TV={o['step_mod10_tv']:.5f} max_z={o['step_mod10_max_z']:.2f} nojumpTV={o.get('step_mod10_nojump_tv','-')}")
    if o.get("adversarial"):
        a = o["adversarial"]
        print(f" H1: P(hottest)={a['p_hit_hottest']} P(coldest)={a['p_hit_coldest']} (null 0.1) trials={a['trials']}")
    pos = o["positive_ev_conditionals"]
    print(f" positive-EV conditionals (Wilson-99 lower bound): {len(pos)}")
    for r in pos[:5]:
        print(f"   lag{r['lag']} d={r['cond_digit']} {r['contract']:8s} p={r['p']} lo={r['wilson_lo']} M={r['M']} EV_lo={r['ev_wilson_lo']:+.4f} (n={r['n']})")

if __name__ == "__main__":
    for sym in sys.argv[1:]:
        brief(analyze(sym))
