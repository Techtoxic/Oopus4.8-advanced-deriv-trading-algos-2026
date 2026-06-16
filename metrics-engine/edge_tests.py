"""edge_tests.py — does the digit edge actually exist? (runs on historical ticks)

Decisive, in-sample-honest statistical tests of the Sentinel V2 premise, using the
repo's own tick data, payout grid, empirical tables and model code. No trading.

Outputs out/edge_tests.json and prints a human summary.

Tests
  T1  Unconditional last-digit uniformity (chi-square).
  T2  Offset PMF P((next-cur) mod 10) per sigma bin vs uniform (chi-square + max dev).
  T3  Best-achievable EV per sigma bin using the EXACT payout grid against the
      realised conditional digit distribution measured ON THE SAME data (the most
      generous, in-sample-optimistic case). If this is <= ~0 the edge cannot exist.
  T4  Walk-forward replay of the EXACT bot decision rule (empirical tables where
      available else wrapped-normal, argmax-EV over 40 contracts, gate, sigma-max)
      on out-of-sample ticks -> realised per-trade PnL, t-stat, breakdown.
"""
import csv, gzip, json, math, os, sys
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(HERE, "..", "fable-thoughts", "tools")
RESULTS = os.path.join(HERE, "..", "fable-thoughts", "results")
DATA = os.path.join(HERE, "..", "fable-thoughts", "data")
sys.path.insert(0, TOOLS)
from sigma_model import wrapped_normal_pmf, winset, CONTRACTS, GRID  # noqa: E402

W = 1800
JUMP_THR = 20
BINW = 0.1
PIP = 2  # JD100 pip_size (decimals); last digit = round(quote*100)%10


def load_ticks(path):
    qs = []
    with gzip.open(path, "rt") as f:
        r = csv.reader(f)
        next(r, None)
        for row in r:
            if len(row) >= 2:
                qs.append(float(row[1]))
    return np.array(qs)


def rolling_sigma_and_digits(quotes):
    """Reproduce sentinel_v2's estimator tick by tick. Returns (sigma[], digit[])."""
    iv = np.round(quotes * (10 ** PIP)).astype(np.int64)
    digits = (iv % 10).astype(int)
    steps = np.diff(iv)
    nojump = np.abs(steps) <= JUMP_THR
    sq = np.where(nojump, steps.astype(float) ** 2, 0.0)
    n = len(iv)
    sigma = np.full(n, np.nan)
    sum_sq = 0.0
    cnt = 0
    from collections import deque
    dsq, dcn = deque(maxlen=W), deque(maxlen=W)
    for i in range(1, n):
        s2 = sq[i - 1]; nj = 1 if nojump[i - 1] else 0
        if len(dsq) == W:
            sum_sq -= dsq[0]; cnt -= dcn[0]
        dsq.append(s2); dcn.append(nj)
        sum_sq += s2; cnt += nj
        if cnt >= 200:
            sigma[i] = math.sqrt(sum_sq / cnt)
    return sigma, digits


def load_tables():
    raw = json.load(open(os.path.join(RESULTS, "empirical_offset_tables.json")))
    return {int(k): v for k, v in raw.items()}


def chisq_uniform(counts):
    counts = np.asarray(counts, float)
    n = counts.sum()
    if n == 0:
        return 0.0, 1.0
    exp = n / len(counts)
    chi = ((counts - exp) ** 2 / exp).sum()
    # survival of chi-square with df=len-1 via regularized gamma (no scipy dependency)
    df = len(counts) - 1
    try:
        from scipy.stats import chi2
        p = float(chi2.sf(chi, df))
    except Exception:
        p = math.exp(-chi / 2)  # crude
    return float(chi), p


def t1_uniformity(digits, sigma, sig_lo=0.0, sig_hi=4.35):
    m = (~np.isnan(sigma)) & (sigma >= sig_lo) & (sigma <= sig_hi)
    d = digits[m]
    counts = [int((d == k).sum()) for k in range(10)]
    chi, p = chisq_uniform(counts)
    return {"n": int(m.sum()), "sig_lo": sig_lo, "sig_hi": sig_hi,
            "counts": counts, "chi2": chi, "df": 9, "p_value": p,
            "max_dev_from_0.1": max(abs(c / max(sum(counts), 1) - 0.1) for c in counts)}


def t2_offset_pmf(digits, sigma):
    out = {}
    nxt = digits[1:]
    cur = digits[:-1]
    off = (nxt - cur) % 10
    sg = sigma[1:]  # sigma at decision tick i aligns with offset to i+1... use sigma[i]
    # decision at tick i uses sigma[i]; outcome is digit[i+1]; offset[i]=(d[i+1]-d[i])%10
    sg = sigma[:-1]
    for b in range(40, 47):  # the claimed edge zone bins 4.0..4.6
        m = (~np.isnan(sg)) & (np.round(sg / BINW).astype(int) == b)
        if m.sum() < 500:
            continue
        o = off[m]
        counts = [int((o == k).sum()) for k in range(10)]
        chi, p = chisq_uniform(counts)
        pmf = [c / m.sum() for c in counts]
        out[b] = {"sigma": b / 10, "n": int(m.sum()), "chi2": chi, "p_value": p,
                  "max_dev_from_uniform": max(abs(x - 0.1) for x in pmf),
                  "pmf": [round(x, 4) for x in pmf]}
    return out


def t3_best_ev_per_bin(digits, sigma):
    """Most generous case: measure the TRUE conditional next-digit pmf given current
    digit on THIS data, then pick the best contract per (bin). In-sample optimistic."""
    nxt = digits[1:]; cur = digits[:-1]; sg = sigma[:-1]
    out = {}
    for b in range(38, 48):
        m = (~np.isnan(sg)) & (np.round(sg / BINW).astype(int) == b)
        if m.sum() < 1000:
            continue
        c = cur[m]; nx = nxt[m]
        # conditional pmf P(next | cur), then marginalize per contract using its winset
        best = None
        # build joint counts
        joint = np.zeros((10, 10))  # joint[cur][next]
        for cc, nn in zip(c, nx):
            joint[cc][nn] += 1
        row_tot = joint.sum(axis=1, keepdims=True)
        cond = np.divide(joint, row_tot, out=np.zeros_like(joint), where=row_tot > 0)
        cur_dist = (row_tot.flatten() / row_tot.sum())
        for (t, bar) in CONTRACTS:
            ws = winset(t, bar)
            payout = GRID[(t, bar)]
            # expected win prob averaged over current-digit distribution
            pwin = sum(cur_dist[cd] * sum(cond[cd][x] for x in ws) for cd in range(10))
            ev = pwin * payout - 1
            if best is None or ev > best["ev"]:
                best = {"ctype": t, "barrier": bar, "p_win": pwin, "payout": payout, "ev": ev}
        out[b] = {"sigma": b / 10, "n": int(m.sum()), "best_in_sample": best}
    return out


def replay_strategy(quotes, label, tables, gate=0.01, sigma_max=4.35, stake=1.0):
    """Exact sentinel decision rule replayed on a tick series. Realised PnL vs actual next digit."""
    sigma, digits = rolling_sigma_and_digits(quotes)
    n = len(quotes)
    rows = []  # (sigma, ctype, barrier, model_p, model_ev, src, win, pnl)
    for i in range(1, n - 1):
        s = sigma[i]
        if math.isnan(s) or s > sigma_max:
            continue
        d = digits[i]
        b = round(s / BINW)
        if b in tables:
            off = tables[b]
            pmf = [off[(dd - d) % 10] for dd in range(10)]
            src = "emp"
        else:
            pmf = wrapped_normal_pmf(s, d)
            src = "wn"
        best = None
        for (t, bar) in CONTRACTS:
            p = sum(pmf[x] for x in winset(t, bar))
            ev = p * GRID[(t, bar)] - 1
            if best is None or ev > best[3]:
                best = (t, bar, p, ev)
        if best is None or best[3] <= gate:
            continue
        nd = digits[i + 1]
        win = nd in winset(best[0], best[1])
        pnl = stake * (GRID[(best[0], best[1])] - 1) if win else -stake
        rows.append((s, best[0], best[1], best[2], best[3], src, int(win), pnl))
    if not rows:
        return {"label": label, "trades": 0}
    arr_pnl = np.array([r[7] for r in rows])
    arr_ev = np.array([r[4] for r in rows])
    src = np.array([r[5] for r in rows])
    sg = np.array([r[0] for r in rows])
    mean = arr_pnl.mean(); sd = arr_pnl.std(ddof=1)
    t = mean / (sd / math.sqrt(len(arr_pnl))) if sd > 0 else 0.0
    res = {"label": label, "trades": len(rows), "total_pnl": float(arr_pnl.sum()),
           "per_trade_pct": float(mean * 100), "t_stat": float(t),
           "win_rate": float(np.mean([r[6] for r in rows])),
           "mean_model_ev_pct": float(arr_ev.mean() * 100),
           "by_source": {}, "by_sigma_bin": {}}
    for s_ in ("emp", "wn"):
        mm = src == s_
        if mm.sum():
            p = arr_pnl[mm]
            tt = p.mean() / (p.std(ddof=1) / math.sqrt(len(p))) if len(p) > 1 and p.std() > 0 else 0.0
            res["by_source"][s_] = {"trades": int(mm.sum()), "per_trade_pct": float(p.mean() * 100),
                                    "t_stat": float(tt), "model_ev_pct": float(arr_ev[mm].mean() * 100)}
    for b in range(36, 46):
        mm = np.round(sg / BINW).astype(int) == b
        if mm.sum() >= 50:
            p = arr_pnl[mm]
            res["by_sigma_bin"][b / 10] = {"trades": int(mm.sum()),
                                           "per_trade_pct": float(p.mean() * 100),
                                           "model_ev_pct": float(arr_ev[mm].mean() * 100)}
    return res


def main():
    tables = load_tables()
    report = {}
    for name, fn in [("live_window", "JD100_live_window.csv.gz"), ("oos", "JD100_oos.csv.gz")]:
        q = load_ticks(os.path.join(DATA, fn))
        sigma, digits = rolling_sigma_and_digits(q)
        report[name] = {
            "n_ticks": len(q),
            "sigma_range": [float(np.nanmin(sigma)), float(np.nanmax(sigma))],
            "T1_uniformity_in_zone": t1_uniformity(digits, sigma, 0, 4.35),
            "T2_offset_pmf": t2_offset_pmf(digits, sigma),
            "T3_best_ev_per_bin_in_sample_optimistic": t3_best_ev_per_bin(digits, sigma),
            "T4_strategy_replay": replay_strategy(q, name, tables),
        }
    os.makedirs(os.path.join(HERE, "out"), exist_ok=True)
    json.dump(report, open(os.path.join(HERE, "out", "edge_tests.json"), "w"), indent=2)

    # human summary
    print("=" * 78)
    for name in report:
        r = report[name]
        print(f"\n### {name}  ({r['n_ticks']} ticks, sigma {r['sigma_range'][0]:.2f}-{r['sigma_range'][1]:.2f})")
        t1 = r["T1_uniformity_in_zone"]
        print(f"  T1 last-digit uniformity (sigma<=4.35, n={t1['n']}): "
              f"chi2={t1['chi2']:.1f} (df9, p={t1['p_value']:.3f}), max dev from 0.1 = {t1['max_dev_from_0.1']:.4f}")
        print("  T2 offset-PMF vs uniform (edge zone):")
        for b, v in r["T2_offset_pmf"].items():
            print(f"     sigma {v['sigma']:.1f}: n={v['n']:>6} chi2={v['chi2']:5.1f} p={v['p_value']:.3f} "
                  f"max_dev={v['max_dev_from_uniform']:.4f}")
        print("  T3 best achievable EV per bin (in-sample optimistic, real grid):")
        for b, v in r["T3_best_ev_per_bin_in_sample_optimistic"].items():
            bi = v["best_in_sample"]
            print(f"     sigma {v['sigma']:.1f}: best={bi['ctype']}{bi['barrier'] if bi['barrier'] is not None else ''} "
                  f"p={bi['p_win']:.4f} payout={bi['payout']:.3f} EV={bi['ev']*100:+.2f}%  (n={v['n']})")
        rep = r["T4_strategy_replay"]
        if rep.get("trades"):
            print(f"  T4 EXACT bot replay: {rep['trades']} trades, realised {rep['per_trade_pct']:+.3f}%/trade "
                  f"(t={rep['t_stat']:+.2f}), win={rep['win_rate']:.3f}, model claimed {rep['mean_model_ev_pct']:+.2f}%/trade")
            for s_, v in rep["by_source"].items():
                print(f"       [{s_}] {v['trades']} trades realised {v['per_trade_pct']:+.3f}% "
                      f"(t={v['t_stat']:+.2f}) vs model {v['model_ev_pct']:+.2f}%")
    print("\nwrote out/edge_tests.json")


if __name__ == "__main__":
    main()
