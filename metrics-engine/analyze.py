"""analyze.py — Sentinel V2 metrics engine: behaviour of sigma & equity,
drawdown anatomy, and win/loss dissection. Reads the bot's trade log + the
independent sigma monitor; writes out/metrics.json, out/REPORT.md and a
self-contained out/dashboard.html. Does not trade, does not touch the bot.

    python3 analyze.py --trades live/sentinel_trades.csv --ticks live/sigma_ticks.csv
"""
import argparse, base64, csv, io, json, math, os, sys
from collections import defaultdict, Counter
import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(HERE, "..", "fable-thoughts", "tools")
RESULTS = os.path.join(HERE, "..", "fable-thoughts", "results")
sys.path.insert(0, TOOLS)
BINW = 0.1

try:
    TABLE_BINS = set(int(k) for k in json.load(open(os.path.join(RESULTS, "empirical_offset_tables.json"))).keys())
except Exception:
    TABLE_BINS = set()


def src_of(sigma):
    return "emp" if round(sigma / BINW) in TABLE_BINS else "wn"


def load_trades(path):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path) as f:
        for r in csv.DictReader(f):
            if r.get("status") not in ("won", "lost"):
                continue
            try:
                pnl = float(r["pnl"])
                sig = float(r["sigma"])
                mev = float(r["model_ev"])
                mp = float(r["model_p"])
            except (ValueError, TypeError, KeyError):
                continue
            rows.append({"epoch": int(r["decision_epoch"]), "sigma": sig, "ctype": r["ctype"],
                         "barrier": r["barrier"], "model_p": mp, "model_ev": mev,
                         "rtt_ms": float(r["rtt_ms"]) if r.get("rtt_ms") else None,
                         "status": r["status"], "pnl": pnl,
                         "lag": int(r["exit_lag_s"]) if r.get("exit_lag_s") not in (None, "", "None") else None,
                         "win": pnl > 0})
    rows.sort(key=lambda x: x["epoch"])
    return rows


def load_ticks(path):
    d = {}
    if not os.path.exists(path):
        return d
    with open(path) as f:
        for r in csv.DictReader(f):
            try:
                d[int(r["epoch"])] = {"quote": float(r["quote"]), "digit": int(r["digit"]),
                                      "sigma": float(r["sigma"]) if r["sigma"] else None}
            except (ValueError, KeyError):
                continue
    return d


def t_and_ci(x):
    x = np.asarray(x, float)
    n = len(x)
    if n < 2:
        return {"n": n, "mean": float(x.mean()) if n else 0.0, "t": 0.0, "ci95": [None, None]}
    m = x.mean(); sd = x.std(ddof=1); se = sd / math.sqrt(n)
    t = m / se if se > 0 else 0.0
    # bootstrap CI
    rng = np.random.default_rng(7)
    boot = rng.choice(x, size=(2000, n), replace=True).mean(axis=1)
    return {"n": n, "mean": float(m), "sd": float(sd), "t": float(t),
            "ci95": [float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5))]}


def drawdown(equity):
    eq = np.asarray(equity, float)
    peak = np.maximum.accumulate(eq)
    dd = eq - peak
    return dd, peak


def fig_to_b64(fig, name=None):
    if name:
        try:
            fig.savefig(os.path.join(HERE, "out", f"{name}.png"), dpi=92, bbox_inches="tight")
        except Exception:
            pass
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=92, bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


def analyze(trades, ticks, start_balance=None):
    M = {}
    if not trades:
        return {"trades": 0}, {}
    stake = float(np.median([-t["pnl"] for t in trades if not t["win"]])) if any(not t["win"] for t in trades) else 0.35
    pnl = np.array([t["pnl"] for t in trades])
    ret = pnl / stake  # per-trade return in stake units
    sig = np.array([t["sigma"] for t in trades])
    mev = np.array([t["model_ev"] for t in trades])
    wins = np.array([t["win"] for t in trades])
    epochs = np.array([t["epoch"] for t in trades])
    equity = np.cumsum(pnl)

    # recover digit & continuous sigma from monitor join
    for t in trades:
        tk = ticks.get(t["epoch"])
        t["digit"] = tk["digit"] if tk else None

    overall = t_and_ci(ret)
    M["overall"] = {"settled": len(trades), "stake": stake, "wins": int(wins.sum()),
                    "win_rate": float(wins.mean()), "total_pnl": float(pnl.sum()),
                    "per_trade_pct": overall["mean"] * 100, "t_stat": overall["t"],
                    "ci95_pct": [c * 100 if c is not None else None for c in overall["ci95"]],
                    "mean_model_ev_pct": float(mev.mean()) * 100,
                    "sigma_range": [float(sig.min()), float(sig.max())]}

    # drawdown (absolute equity if balance known)
    if start_balance is not None:
        abs_eq = start_balance + equity
    else:
        abs_eq = equity - equity.min() + max(equity.max() - equity.min(), 1)  # offset, % meaningful-ish
    dd_abs, peak_abs = drawdown(equity)
    maxdd_i = int(np.argmin(dd_abs))
    abs_eq2 = (start_balance if start_balance is not None else 0) + equity
    dd_pct = None
    if start_balance is not None:
        full_eq = np.concatenate([[start_balance], abs_eq2])
        ddp, pk = drawdown(full_eq)
        dd_pct = float((ddp / pk).min() * 100)
    M["equity"] = {"start_balance": start_balance,
                   "end_equity": float((start_balance or 0) + equity[-1]),
                   "max_drawdown_$": float(dd_abs.min()),
                   "max_drawdown_pct_of_peak": dd_pct,
                   "max_dd_at_trade": maxdd_i,
                   "final_cum_pnl": float(equity[-1])}

    # drawdown episodes with sigma context
    episodes = []
    in_dd = False; start_i = 0
    for i in range(len(equity)):
        if dd_abs[i] < -1e-9 and not in_dd:
            in_dd = True; start_i = i
        elif dd_abs[i] >= -1e-9 and in_dd:
            in_dd = False
            seg = slice(start_i, i)
            depth = float(dd_abs[start_i:i + 1].min())
            episodes.append({"start_trade": start_i, "len": i - start_i, "depth_$": depth,
                             "sigma_start": float(sig[start_i]), "sigma_end": float(sig[min(i, len(sig) - 1)]),
                             "sigma_rising": bool(sig[min(i, len(sig) - 1)] > sig[start_i])})
    episodes.sort(key=lambda e: e["depth_$"])
    M["drawdown_episodes_top"] = episodes[:8]
    rising = [e for e in episodes if e["sigma_rising"]]
    M["drawdown_sigma_link"] = {"episodes": len(episodes),
                                "with_sigma_rising": len(rising),
                                "frac_rising": len(rising) / len(episodes) if episodes else None}

    # sigma direction vs per-trade pnl: use trade-to-trade sigma slope over a short lookback
    look = 8
    slope = np.full(len(sig), np.nan)
    for i in range(len(sig)):
        lo = max(0, i - look)
        if i - lo >= 2:
            slope[i] = sig[i] - sig[lo]
    up = slope > 0.01; dn = slope < -0.01
    M["sigma_direction"] = {
        "sigma_rising": {"n": int(up.sum()), "per_trade_pct": float(ret[up].mean() * 100) if up.sum() else None,
                          "win_rate": float(wins[up].mean()) if up.sum() else None},
        "sigma_falling": {"n": int(dn.sum()), "per_trade_pct": float(ret[dn].mean() * 100) if dn.sum() else None,
                           "win_rate": float(wins[dn].mean()) if dn.sum() else None},
        "note": "slope = sigma(now) - sigma(8 trades ago)"}

    # correlation sigma level & sigma-change with rolling equity change
    if len(equity) > 20:
        deq = np.diff(equity)
        M["correlations"] = {
            "corr(sigma_level, per_trade_pnl)": float(np.corrcoef(sig, pnl)[0, 1]),
            "corr(sigma_slope, per_trade_pnl)": float(np.corrcoef(np.nan_to_num(slope), pnl)[0, 1]),
        }

    # win/loss anatomy
    def group(keyfn):
        g = defaultdict(lambda: {"n": 0, "wins": 0, "pnl": 0.0, "mev": 0.0})
        for t in trades:
            k = keyfn(t)
            if k is None:
                continue
            gg = g[k]; gg["n"] += 1; gg["wins"] += int(t["win"]); gg["pnl"] += t["pnl"]; gg["mev"] += t["model_ev"]
        return {str(k): {"n": v["n"], "win_rate": v["wins"] / v["n"],
                          "mean_pnl": v["pnl"] / v["n"], "per_trade_pct": (v["pnl"] / v["n"]) / stake * 100,
                          "mean_model_ev_pct": v["mev"] / v["n"] * 100} for k, v in sorted(g.items())}

    M["by_contract"] = group(lambda t: t["ctype"])
    M["by_sigma_bin"] = group(lambda t: round(t["sigma"], 1))
    M["by_source"] = group(lambda t: src_of(t["sigma"]))
    M["by_digit"] = group(lambda t: t.get("digit"))
    # model_ev calibration buckets
    M["ev_calibration"] = group(lambda t: f"{math.floor(t['model_ev']*100/2)*2}-{math.floor(t['model_ev']*100/2)*2+2}%")

    # winners vs losers profile
    w = [t for t in trades if t["win"]]; l = [t for t in trades if not t["win"]]
    def prof(g):
        if not g:
            return {}
        return {"n": len(g), "mean_sigma": float(np.mean([x["sigma"] for x in g])),
                "mean_model_ev_pct": float(np.mean([x["model_ev"] for x in g]) * 100),
                "top_contracts": Counter(x["ctype"] for x in g).most_common(4)}
    M["winners_profile"] = prof(w)
    M["losers_profile"] = prof(l)

    # latency
    rtts = [t["rtt_ms"] for t in trades if t["rtt_ms"] is not None]
    lags = Counter(t["lag"] for t in trades if t["lag"] is not None)
    M["execution"] = {"rtt_ms_p50": float(np.percentile(rtts, 50)) if rtts else None,
                      "rtt_ms_p90": float(np.percentile(rtts, 90)) if rtts else None,
                      "exit_lag_hist": dict(lags),
                      "lag1_frac": (lags.get(1, 0) / sum(lags.values())) if lags else None}

    # ---- charts ----
    charts = {}
    x = np.arange(len(equity))

    fig, ax = plt.subplots(figsize=(9, 3.4))
    ax.plot(x, (start_balance or 0) + equity, color="#1a7", lw=1.4)
    ax.set_title("Equity (cumulative PnL)"); ax.set_xlabel("settled trade #"); ax.set_ylabel("equity ($)")
    ax2 = ax.twinx(); ax2.plot(x, sig, color="#c33", lw=0.8, alpha=0.5)
    ax2.set_ylabel("sigma", color="#c33")
    charts["equity_sigma"] = fig_to_b64(fig, "equity_sigma")

    fig, ax = plt.subplots(figsize=(9, 2.4))
    ax.fill_between(x, dd_abs, 0, color="#c33", alpha=0.4)
    ax.set_title("Drawdown underwater ($ below running peak)"); ax.set_xlabel("settled trade #")
    charts["drawdown"] = fig_to_b64(fig, "drawdown")

    # calibration: model_ev vs realized per sigma bin
    bins = sorted(set(round(s, 1) for s in sig))
    mev_b = [np.mean([t["model_ev"] for t in trades if round(t["sigma"], 1) == b]) * 100 for b in bins]
    real_b = [np.mean([t["pnl"] for t in trades if round(t["sigma"], 1) == b]) / stake * 100 for b in bins]
    fig, ax = plt.subplots(figsize=(7, 3.4))
    ax.plot(bins, mev_b, "o-", label="model claimed EV %", color="#36c")
    ax.plot(bins, real_b, "s-", label="realised %/trade", color="#1a7")
    ax.axhline(0, color="#888", lw=0.7); ax.set_xlabel("sigma bin"); ax.set_ylabel("% per trade")
    ax.set_title("EV calibration: claimed vs realised"); ax.legend()
    charts["calibration"] = fig_to_b64(fig, "calibration")

    # per-trade pnl hist
    fig, ax = plt.subplots(figsize=(6, 3))
    ax.hist(pnl, bins=30, color="#46a")
    ax.set_title("Per-trade PnL distribution ($)"); charts["pnl_hist"] = fig_to_b64(fig, "pnl_hist")

    # win rate by contract
    ct = M["by_contract"]
    fig, ax = plt.subplots(figsize=(8, 3))
    names = list(ct.keys()); wr = [ct[n]["win_rate"] for n in names]
    ax.bar(names, wr, color="#7a3"); ax.axhline(0.5, color="#888", lw=0.7)
    ax.set_title("Win rate by contract type"); ax.tick_params(axis="x", rotation=60)
    charts["win_by_contract"] = fig_to_b64(fig, "win_by_contract")

    return M, charts


def write_dashboard(M, charts, edge, path):
    def tbl(d, cols):
        if not d:
            return "<i>no data</i>"
        head = "".join(f"<th>{c}</th>" for c in ["key"] + cols)
        body = ""
        for k, v in d.items():
            if not isinstance(v, dict):
                continue
            cells = "".join(f"<td>{round(v[c],4) if isinstance(v.get(c),(int,float)) else v.get(c,'')}</td>" for c in cols)
            body += f"<tr><td><b>{k}</b></td>{cells}</tr>"
        return f"<table><tr>{head}</tr>{body}</table>"

    img = lambda k: f'<img src="data:image/png;base64,{charts[k]}">' if k in charts else ""
    o = M.get("overall", {})
    html = f"""<!doctype html><meta charset=utf-8><title>Sentinel V2 — Metrics</title>
<style>body{{font:14px/1.5 system-ui,sans-serif;max-width:1000px;margin:24px auto;padding:0 16px;color:#222}}
h1,h2{{border-bottom:1px solid #ddd;padding-bottom:4px}}table{{border-collapse:collapse;margin:8px 0;font-size:13px}}
td,th{{border:1px solid #ccc;padding:3px 8px;text-align:right}}th{{background:#f4f4f4}}td:first-child,th:first-child{{text-align:left}}
img{{max-width:100%;border:1px solid #eee;margin:6px 0}}.kpi{{display:inline-block;background:#f7f7f7;border:1px solid #e0e0e0;border-radius:8px;padding:8px 14px;margin:4px}}
.kpi b{{display:block;font-size:22px}}.warn{{color:#b00}}.ok{{color:#1a7}}</style>
<h1>Sentinel V2 — Live Metrics Engine</h1>
<p>Read-only analysis of the running bot. Separate branch; original work untouched.</p>
<div>
<span class=kpi><b>{o.get('settled','?')}</b>settled trades</span>
<span class=kpi><b>{o.get('win_rate',0)*100:.1f}%</b>win rate</span>
<span class=kpi><b>${o.get('total_pnl',0):+.2f}</b>cumulative PnL</span>
<span class=kpi><b>{o.get('per_trade_pct',0):+.3f}%</b>realised / trade</span>
<span class=kpi><b>t={o.get('t_stat',0):+.2f}</b>significance</span>
<span class=kpi><b>{o.get('mean_model_ev_pct',0):+.2f}%</b>model claimed / trade</span>
</div>
<h2>Equity & sigma</h2>{img('equity_sigma')}
<h2>Drawdown</h2>{img('drawdown')}
<p>Max drawdown: <b>${M.get('equity',{}).get('max_drawdown_$',0):.2f}</b>
({M.get('equity',{}).get('max_drawdown_pct_of_peak')}% of peak). Drawdown episodes with sigma RISING:
<b>{M.get('drawdown_sigma_link',{}).get('with_sigma_rising')}/{M.get('drawdown_sigma_link',{}).get('episodes')}</b>.</p>
<h2>EV calibration (the key chart)</h2>{img('calibration')}
<p>Blue = what the model claimed per sigma bin; green = what actually happened. A real edge would have
green track blue above zero. </p>
<h2>Sigma direction vs outcome</h2>{tbl(M.get('sigma_direction',{}),['n','per_trade_pct','win_rate'])}
<h2>Win/loss anatomy</h2>
<h3>By contract</h3>{img('win_by_contract')}{tbl(M.get('by_contract',{}),['n','win_rate','per_trade_pct','mean_model_ev_pct'])}
<h3>By sigma bin</h3>{tbl(M.get('by_sigma_bin',{}),['n','win_rate','per_trade_pct','mean_model_ev_pct'])}
<h3>By source (empirical table vs wrapped-normal fallback)</h3>{tbl(M.get('by_source',{}),['n','win_rate','per_trade_pct','mean_model_ev_pct'])}
<h3>By current digit</h3>{tbl(M.get('by_digit',{}),['n','win_rate','per_trade_pct'])}
<h2>PnL distribution</h2>{img('pnl_hist')}
<h2>Execution</h2><pre>{json.dumps(M.get('execution',{}),indent=2)}</pre>
<h2>Edge-validity (historical ticks)</h2><pre>{json.dumps(edge_summary(edge),indent=2)}</pre>
"""
    open(path, "w").write(html)


def edge_summary(edge):
    if not edge:
        return {}
    out = {}
    for k in edge:
        r = edge[k].get("T4_strategy_replay", {})
        out[k] = {"replay_trades": r.get("trades"), "realised_pct_per_trade": round(r.get("per_trade_pct", 0), 3),
                  "t_stat": round(r.get("t_stat", 0), 2), "model_claimed_pct": round(r.get("mean_model_ev_pct", 0), 2)}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trades", default="live/sentinel_trades.csv")
    ap.add_argument("--ticks", default="live/sigma_ticks.csv")
    ap.add_argument("--start-balance", type=float, default=None)
    ap.add_argument("--probe-balance", action="store_true")
    a = ap.parse_args()

    trades = load_trades(a.trades)
    ticks = load_ticks(a.ticks)
    start_balance = a.start_balance
    if a.probe_balance and trades:
        try:
            from deriv_api import DerivWS
            bal = float(DerivWS().account.get("balance"))
            start_balance = bal - sum(t["pnl"] for t in trades)
        except Exception as e:
            print("balance probe failed:", e)

    edge = {}
    ep = os.path.join(HERE, "out", "edge_tests.json")
    if os.path.exists(ep):
        edge = json.load(open(ep))

    M, charts = analyze(trades, ticks, start_balance)
    os.makedirs(os.path.join(HERE, "out"), exist_ok=True)
    json.dump(M, open(os.path.join(HERE, "out", "metrics.json"), "w"), indent=2)
    if charts:
        write_dashboard(M, charts, edge, os.path.join(HERE, "out", "dashboard.html"))

    o = M.get("overall", {})
    print(f"settled={o.get('settled')} win={o.get('win_rate',0)*100:.1f}% "
          f"PnL=${o.get('total_pnl',0):+.2f} realised={o.get('per_trade_pct',0):+.3f}%/trade "
          f"t={o.get('t_stat',0):+.2f} model_claimed={o.get('mean_model_ev_pct',0):+.2f}%")
    eq = M.get("equity", {})
    print(f"maxDD=${eq.get('max_drawdown_$',0):.2f} ({eq.get('max_drawdown_pct_of_peak')}% of peak)")
    sd = M.get("sigma_direction", {})
    print(f"sigma rising: {sd.get('sigma_rising',{})}")
    print(f"sigma falling: {sd.get('sigma_falling',{})}")
    print("by_source:", json.dumps(M.get("by_source", {})))


if __name__ == "__main__":
    main()
