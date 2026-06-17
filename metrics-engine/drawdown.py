"""drawdown.py — drawdown, ruin-risk, Kelly, and sigma/equity analysis for the JD100 sentinel.

Reads the committed live trade log (live/sentinel_trades.csv, $0.35 stake) and produces:
  - equity curve, max drawdown ($ and %), drawdown duration, longest losing streak, recovery
  - Monte-Carlo risk-of-ruin for several starting balances (order-resampled, not single-path)
  - Kelly fraction from the realised per-stake return distribution + safe-stake-by-balance
  - sigma-direction and sigma-trend vs equity, and a pause-filter back-test
Writes out/drawdown.json and (with --plots) PNG charts. Pure analysis; no trading.

    python3 drawdown.py [--csv live/sentinel_trades.csv] [--stake 0.35] [--plots]
"""
import csv, os, json, math, argparse
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))


def load(csv_path):
    rows = list(csv.DictReader(open(csv_path)))
    pnl = np.array([float(r["pnl"]) for r in rows])
    sig = np.array([float(r["sigma"]) for r in rows])
    ev = np.array([float(r["model_ev"]) for r in rows])
    return rows, pnl, sig, ev


def drawdown_stats(pnl, stake):
    eq = np.cumsum(pnl)
    peak = np.maximum.accumulate(eq)
    dd = peak - eq
    i_tr = int(dd.argmax())
    peak_val = peak[i_tr]
    i_pk = int(np.where(eq[:i_tr + 1] >= peak_val)[0][0])
    after = np.where(eq[i_tr:] >= peak_val)[0]
    recov = int(after[0]) if len(after) else None
    # longest losing streak
    streak = mx = 0
    for x in pnl:
        streak = streak + 1 if x < 0 else 0
        mx = max(mx, streak)
    # worst rolling windows (in stake units, dollars)
    return {
        "n_trades": len(pnl),
        "win_rate": float((pnl > 0).mean()),
        "sum_pnl": float(pnl.sum()),
        "mean_per_trade": float(pnl.mean()),
        "pct_of_stake": float(pnl.mean() / stake * 100),
        "t_stat": float(pnl.mean() / (pnl.std(ddof=1) / math.sqrt(len(pnl)))),
        "max_dd_dollars": float(dd.max()),
        "max_dd_peak_equity": float(peak_val),
        "max_dd_trades_down": i_tr - i_pk,
        "max_dd_recovery_trades": recov,
        "longest_losing_streak": int(mx),
        "equity_final": float(eq[-1]),
    }


def ruin_risk(pnl, start_balances, stake, horizon, n_paths=4000, seed=1):
    """Order-resampled MC. Ruin = balance can't cover next stake. Fixed-stake sizing."""
    rng = np.random.default_rng(seed)
    out = {}
    for B in start_balances:
        ruined = 0
        min_bals = np.empty(n_paths)
        for k in range(n_paths):
            draws = pnl[rng.integers(0, len(pnl), size=horizon)]
            bal = B + np.cumsum(draws)
            # ruin if it ever drops below one stake
            floor = bal.min()
            min_bals[k] = floor
            if floor < stake:
                ruined += 1
        out[str(B)] = {
            "p_ruin": ruined / n_paths,
            "p5_min_balance": float(np.percentile(min_bals, 5)),
            "median_min_balance": float(np.percentile(min_bals, 50)),
        }
    return out


def kelly(pnl, stake):
    """Kelly fraction on the realised per-stake return distribution r_i = pnl_i/stake.
    f* maximises E[log(1 + f r)]. Returns full-Kelly fraction-of-bankroll."""
    r = pnl / stake
    def g(f):
        x = 1 + f * r
        if np.any(x <= 0):
            return -1e9
        return np.mean(np.log(x))
    lo, hi = 0.0, 1.0 / max(1e-9, -r.min())  # f can't exceed 1/maxloss (maxloss=1 stake -> <1)
    hi = min(hi, 0.999)
    # golden-section search
    gr = (math.sqrt(5) - 1) / 2
    a, b = lo, hi
    c = b - gr * (b - a); d = a + gr * (b - a)
    for _ in range(200):
        if g(c) < g(d):
            a = c
        else:
            b = d
        c = b - gr * (b - a); d = a + gr * (b - a)
    fstar = (a + b) / 2
    return {"full_kelly_fraction": float(fstar),
            "quarter_kelly_fraction": float(fstar / 4),
            "mean_return_per_stake": float(r.mean()),
            "std_return_per_stake": float(r.std(ddof=1))}


def sigma_analysis(pnl, sig, stake, trend_k=40):
    """STRICTLY CAUSAL: the trend at trade i uses only sigma at i and i-k (no future ticks).
    A centered smoother would leak look-ahead and massively overstate any trend filter."""
    n = len(sig)
    dsig = np.diff(sig, prepend=sig[0])  # 1-tick change (causal)
    res = {"per_tick_direction": {}, "trend_causal_k": trend_k, "trend": {}, "by_bin": {}}
    for lab, m in [("rising", dsig > 0), ("flat", dsig == 0), ("falling", dsig < 0)]:
        if m.sum():
            res["per_tick_direction"][lab] = {
                "n": int(m.sum()), "pct_of_stake": float(pnl[m].mean() / stake * 100),
                "win_rate": float((pnl[m] > 0).mean())}
    tr = np.full(n, np.nan)
    tr[trend_k:] = sig[trend_k:] - sig[:-trend_k]  # change over last k trades, backward only
    valid = ~np.isnan(tr)
    for lab, m in [("trend_up", valid & (tr > 0)), ("trend_down", valid & (tr < 0))]:
        res["trend"][lab] = {"n": int(m.sum()), "pct_of_stake": float(pnl[m].mean() / stake * 100),
                             "win_rate": float((pnl[m] > 0).mean())}
    for lo, hi in [(3.4, 3.5), (3.5, 3.6), (3.6, 3.7), (3.7, 3.8), (3.8, 3.9), (3.9, 4.0), (4.0, 4.35)]:
        m = (sig >= lo) & (sig < hi)
        if m.sum() > 100:
            res["by_bin"][f"{lo}-{hi}"] = {"n": int(m.sum()), "pct_of_stake": float(pnl[m].mean() / stake * 100),
                                           "win_rate": float((pnl[m] > 0).mean())}
    # pause-filter back-test (CAUSAL): skip trades when sigma rose over the last k trades
    keep = valid & (tr <= 0)
    res["pause_filter_trend_rising"] = {
        "rule": f"skip trade if sigma[i] > sigma[i-{trend_k}] (causal)",
        "kept_trades": int(keep.sum()),
        "kept_pct_of_stake": float(pnl[keep].mean() / stake * 100),
        "skipped_trades": int((valid & (tr > 0)).sum()),
        "skipped_pct_of_stake": float(pnl[valid & (tr > 0)].mean() / stake * 100),
        "unfiltered_pct_of_stake": float(pnl.mean() / stake * 100),
    }
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=os.path.join(HERE, "live", "sentinel_trades.csv"))
    ap.add_argument("--stake", type=float, default=0.35)
    ap.add_argument("--plots", action="store_true")
    a = ap.parse_args()

    rows, pnl, sig, ev = load(a.csv)
    out = {"source_csv": os.path.relpath(a.csv, HERE), "stake": a.stake}
    out["drawdown"] = drawdown_stats(pnl, a.stake)
    out["ruin_risk"] = ruin_risk(pnl, [5, 10, 15, 20, 30, 50, 100], a.stake,
                                 horizon=len(pnl))
    out["kelly"] = kelly(pnl, a.stake)
    out["sigma"] = sigma_analysis(pnl, sig, a.stake)
    # safe-stake recommendation
    fq = out["kelly"]["quarter_kelly_fraction"]
    out["sizing"] = {
        "current_stake": a.stake,
        "min_balance_for_current_stake_quarter_kelly": float(a.stake / fq) if fq > 0 else None,
        "safe_stake_at_30_quarter_kelly": float(30 * fq),
        "safe_stake_at_50_quarter_kelly": float(50 * fq),
    }

    os.makedirs(os.path.join(HERE, "out"), exist_ok=True)
    json.dump(out, open(os.path.join(HERE, "out", "drawdown.json"), "w"), indent=2)

    d = out["drawdown"]
    print(f"n={d['n_trades']} win={d['win_rate']*100:.2f}% sumPnL=${d['sum_pnl']:.2f} "
          f"mean={d['pct_of_stake']:+.2f}%/tr t={d['t_stat']:.2f}")
    print(f"max DD ${d['max_dd_dollars']:.2f} (peak eq ${d['max_dd_peak_equity']:.2f}); "
          f"{d['max_dd_trades_down']} trades down, recover {d['max_dd_recovery_trades']} trades; "
          f"longest losing streak {d['longest_losing_streak']}")
    print("\nRISK OF RUIN (order-resampled MC, fixed $%.2f stake):" % a.stake)
    for B, v in out["ruin_risk"].items():
        print(f"  start ${B:>4}: P(ruin)={v['p_ruin']*100:5.1f}%  5th-pctile floor=${v['p5_min_balance']:.2f}")
    k = out["kelly"]
    print(f"\nKelly: full f*={k['full_kelly_fraction']:.3f}  quarter={k['quarter_kelly_fraction']:.3f} "
          f"(mean r={k['mean_return_per_stake']:+.4f}, std r={k['std_return_per_stake']:.3f})")
    s = out["sizing"]
    print(f"min balance for $%.2f stake @quarter-Kelly = $%.2f" %
          (a.stake, s["min_balance_for_current_stake_quarter_kelly"]))
    print("\nSIGMA TREND vs equity:")
    for lab, v in out["sigma"]["trend"].items():
        print(f"  {lab:11}: n={v['n']:6} {v['pct_of_stake']:+.2f}%/tr")
    pf = out["sigma"]["pause_filter_trend_rising"]
    print(f"  PAUSE when trend rising -> keep {pf['kept_trades']} @ {pf['kept_pct_of_stake']:+.2f}% "
          f"(skipped {pf['skipped_trades']} @ {pf['skipped_pct_of_stake']:+.2f}%, "
          f"unfiltered {pf['unfiltered_pct_of_stake']:+.2f}%)")

    if a.plots:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        eq = np.cumsum(pnl); peak = np.maximum.accumulate(eq)
        fig, ax = plt.subplots(2, 1, figsize=(11, 7), sharex=True)
        ax[0].plot(eq, lw=0.8, label="equity (cum PnL, $)")
        ax[0].plot(peak, lw=0.6, color="green", alpha=0.5, label="peak")
        ax2 = ax[0].twinx(); ax2.plot(sig, lw=0.4, color="red", alpha=0.4, label="sigma")
        ax2.set_ylabel("sigma", color="red")
        ax[0].set_ylabel("equity $"); ax[0].legend(loc="upper left"); ax[0].set_title("Equity vs sigma")
        ax[1].fill_between(range(len(eq)), -(peak - eq), 0, color="red", alpha=0.5)
        ax[1].set_ylabel("drawdown $"); ax[1].set_xlabel("trade #"); ax[1].set_title("Underwater")
        plt.tight_layout(); plt.savefig(os.path.join(HERE, "out", "drawdown.png"), dpi=110)
        print("\nsaved out/drawdown.png")


if __name__ == "__main__":
    main()
