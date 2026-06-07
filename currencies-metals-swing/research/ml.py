"""
ml.py — optional ML trade filter, validated the only way that isn't self-deception.

Idea: don't predict the market — predict *which of the strategy's own signals are
worth taking*. For every entry the Adaptive Swing logic would fire, label it by a
triple-barrier outcome (does +tp_R*R get hit before -1R?) and learn a classifier
on features known AT the signal bar (trend strength, vol regime, RSI, trend
extension, recent returns, COT-index of each leg, day-of-week, asset class).

Validation = PURGED WALK-FORWARD: train only on signals strictly in the past,
test on the future, with an embargo gap so overlapping outcomes can't leak. We
then ask one question: does taking only high-confidence signals raise realised
expectancy out-of-sample versus taking them all? We report the answer straight.
"""

from __future__ import annotations
import warnings; warnings.filterwarnings("ignore")
import os
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.metrics import roc_auc_score

import data, cot
import strategy as S
import indicators as ind

RES = os.path.join(os.path.dirname(__file__), "results")
os.makedirs(RES, exist_ok=True)

SYMS = ["XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "NZDUSD", "EURJPY", "EURGBP"]
METALS = {"XAUUSD", "XAGUSD"}
CFG = S.SwingConfig(entry_mode="either", tp_R=2.5, sl_atr_mult=2.0, time_stop_bars=40)
FEATURES = ["direction", "adx", "atr_pct", "rsi", "trend_ext", "pull_ext",
            "ret5", "ret20", "cot_base_idx", "cot_quote_idx", "cot_bias", "dow", "is_metal"]


def build_symbol(symbol):
    df = data.load_ohlc(symbol)
    sig = S.generate(df, CFG)
    cotf = cot.pair_index_features(symbol, df.index)
    o = df["open"].to_numpy(); h = df["high"].to_numpy(); l = df["low"].to_numpy(); c = df["close"].to_numpy()
    atr = sig["atr"].to_numpy(); adxv = sig["adx"].to_numpy(); rsiv = sig["rsi"].to_numpy()
    emat = sig["ema_trend"].to_numpy(); emap = sig["ema_pullback"].to_numpy(); atrpct = sig["atr_pct"].to_numpy()
    longs = sig["long_signal"].to_numpy(); shorts = sig["short_signal"].to_numpy(); regime = sig["regime"].to_numpy()
    cb = cotf["cot_base_idx"].to_numpy(); cq = cotf["cot_quote_idx"].to_numpy(); cbias = cotf["cot_bias"].to_numpy()
    n = len(df); rows = []
    for i in range(60, n - 2):
        d = 1 if (longs[i] and regime[i] == 1) else (-1 if (shorts[i] and regime[i] == -1) else 0)
        if d == 0 or np.isnan(atr[i]) or atr[i] <= 0:
            continue
        entry = o[i + 1]
        R = CFG.sl_atr_mult * atr[i]
        stop = entry - R if d == 1 else entry + R
        target = entry + CFG.tp_R * R if d == 1 else entry - CFG.tp_R * R
        outcome = None
        for j in range(i + 1, min(i + 1 + CFG.time_stop_bars, n)):
            if d == 1:
                if l[j] <= stop: outcome = -1.0; break
                if h[j] >= target: outcome = CFG.tp_R; break
            else:
                if h[j] >= stop: outcome = -1.0; break
                if l[j] <= target: outcome = CFG.tp_R; break
        if outcome is None:
            j = min(i + CFG.time_stop_bars, n - 1)
            outcome = d * (c[j] - entry) / R
        rows.append({
            "symbol": symbol, "date": df.index[i + 1],
            "direction": d, "adx": adxv[i], "atr_pct": atrpct[i], "rsi": rsiv[i],
            "trend_ext": (c[i] - emat[i]) / atr[i], "pull_ext": (c[i] - emap[i]) / atr[i],
            "ret5": c[i] / c[i - 5] - 1, "ret20": c[i] / c[i - 20] - 1,
            "cot_base_idx": cb[i], "cot_quote_idx": cq[i], "cot_bias": cbias[i],
            "dow": df.index[i + 1].dayofweek, "is_metal": 1 if symbol in METALS else 0,
            "R": outcome, "win": 1 if outcome > 0 else 0,
        })
    return pd.DataFrame(rows)


def purged_walkforward(ds, n_folds=5, embargo=10, threshold=0.55):
    ds = ds.sort_values("date").reset_index(drop=True).dropna(subset=FEATURES)
    folds = np.array_split(np.arange(len(ds)), n_folds + 1)
    oos = []
    for k in range(1, n_folds + 1):
        test_idx = folds[k]
        train_end = test_idx[0] - embargo
        if train_end < 50:
            continue
        tr = ds.iloc[:train_end]; te = ds.iloc[test_idx]
        if tr["win"].nunique() < 2 or len(te) < 5:
            continue
        clf = GradientBoostingClassifier(n_estimators=160, max_depth=3, learning_rate=0.05, subsample=0.8)
        clf.fit(tr[FEATURES], tr["win"])
        p = clf.predict_proba(te[FEATURES])[:, 1]
        sub = te.copy(); sub["p"] = p
        oos.append(sub)
    oos = pd.concat(oos) if oos else pd.DataFrame()
    return oos


def main():
    parts = [build_symbol(s) for s in SYMS]
    ds = pd.concat(parts, ignore_index=True)
    base_rate = ds["win"].mean()
    print(f"signals={len(ds)}  win-rate={base_rate:.3f}  mean realised R (take ALL)={ds['R'].mean():.3f}")

    oos = purged_walkforward(ds)
    auc = roc_auc_score(oos["win"], oos["p"]) if len(oos) and oos["win"].nunique() > 1 else float("nan")
    lines = ["# ML Trade Filter — Purged Walk-Forward\n",
             f"- OOS signals scored: **{len(oos)}**",
             f"- OOS AUC: **{auc:.3f}** (0.50 = no skill)\n",
             "| Selectivity (take top %) | Trades | Win% | Mean R | vs take-all |",
             "|---|--:|--:|--:|--:|"]
    take_all_R = oos["R"].mean()
    for thr_pct in [100, 70, 50, 30, 20]:
        if thr_pct == 100:
            sel = oos
        else:
            cut = np.percentile(oos["p"], 100 - thr_pct)
            sel = oos[oos["p"] >= cut]
        if len(sel) == 0:
            continue
        lines.append(f"| top {thr_pct}% | {len(sel)} | {sel['win'].mean()*100:.1f} | "
                     f"{sel['R'].mean():.3f} | {sel['R'].mean()-take_all_R:+.3f}R |")
    # gold-only view
    g = oos[oos.symbol == "XAUUSD"]
    gold_line = ""
    if len(g) > 10:
        cut = np.percentile(g["p"], 50)
        gold_line = (f"take-all mean R={g['R'].mean():.3f} (n={len(g)}); "
                     f"top-50% mean R={g[g['p']>=cut]['R'].mean():.3f} (n={len(g[g['p']>=cut])})")
        lines.append(f"\n**Gold only:** {gold_line}")

    # data-driven verdict
    top30 = oos[oos["p"] >= np.percentile(oos["p"], 70)]
    improved = len(top30) > 0 and (top30["R"].mean() > take_all_R + 0.02)
    has_skill = np.isfinite(auc) and auc >= 0.52
    if has_skill and improved:
        verdict = ("\n## Read (this run)\n"
                   f"AUC {auc:.3f} and rising mean-R under selectivity: the filter carries real, if "
                   "thin, out-of-sample information. Use it as a *confidence gate* on the EA's entries, "
                   "re-fit quarterly, and expect decay.\n")
    else:
        verdict = ("\n## Read (this run)\n"
                   f"**Honest result: no usable ML edge here.** OOS AUC is {auc:.3f} (~0.50 = coin flip) "
                   "and being more selective did **not** raise expectancy — the classifier is fitting "
                   "noise, not signal. This is the same lesson as the Deriv work: most 'AI trade filter' "
                   "claims evaporate under purged walk-forward. **Do not ship the ML filter as-is.** The "
                   "durable signal is the simple, transparent one — trend regime + COT-index positioning, "
                   "concentrated in gold. More data, richer features (intermarket, rates, real order "
                   "flow) or a different label might change this; raw price+COT features did not.\n")
    lines.append(verdict)
    with open(os.path.join(RES, "ML_REPORT.md"), "w") as fh:
        fh.write("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
