#!/usr/bin/env python3
"""
Online logistic regression on H1 bars — does ANY of it predict next-bar sign?
Purged walk-forward: model updates strictly causally (SGD, one pass, in time
order); prediction for bar t uses weights from bars < t-1 (1-bar purge).
Metrics: AUC, hit rate at decision threshold, net expectancy after costs.
Prior evidence: daily ML filter failed (AUC 0.49, ML_REPORT.md). This tests H1.
"""
import numpy as np, pandas as pd
from validate_intraday import load, SPREAD_PCT

def features(df):
    c = df["close"]
    r = np.log(c / c.shift())
    f = pd.DataFrame(index=df.index)
    for k in (1, 2, 3, 6, 12, 24):
        f[f"r{k}"] = np.log(c / c.shift(k))
    tr = (df["high"] - df["low"])
    f["atrp"] = tr.rolling(24).mean().rolling(24 * 60).rank(pct=True)
    f["er"] = (c - c.shift(20)).abs() / (c.diff().abs().rolling(20).sum() + 1e-12)
    hh = df.index.hour
    f["hs"], f["hc"] = np.sin(2 * np.pi * hh / 24), np.cos(2 * np.pi * hh / 24)
    f["y"] = (r.shift(-1) > 0).astype(float)  # next bar up?
    f["fwd"] = r.shift(-1)
    return f.dropna()

def run(sym):
    df = load(sym)
    f = features(df)
    X = f.drop(columns=["y", "fwd"]).values
    # causal z-normalization (expanding mean/std up to t-1)
    Xn = np.zeros_like(X)
    mean = np.zeros(X.shape[1]); m2 = np.ones(X.shape[1]); n = 0
    for i in range(len(X)):
        if n > 100:
            Xn[i] = (X[i] - mean) / np.sqrt(m2 / n + 1e-12)
        n += 1
        d = X[i] - mean
        mean += d / n
        m2 += d * (X[i] - mean)
    y = f["y"].values
    fwd = f["fwd"].values
    w = np.zeros(X.shape[1] + 1)
    lr = 0.01
    preds = np.full(len(X), np.nan)
    pend_x = None; pend_y = None
    for i in range(len(X)):
        xi = np.append(Xn[i], 1.0)
        if i > 5000:
            preds[i] = 1 / (1 + np.exp(-np.clip(w @ xi, -30, 30)))
        # purge: update with the PREVIOUS bar's example only now (its label needs bar i)
        if pend_x is not None:
            p = 1 / (1 + np.exp(-np.clip(w @ pend_x, -30, 30)))
            w += lr * (pend_y - p) * pend_x
        pend_x, pend_y = xi, y[i]
    m = ~np.isnan(preds)
    p, yy, ff = preds[m], y[m], fwd[m]
    # AUC
    order = np.argsort(p)
    ranks = np.empty_like(order, dtype=float); ranks[order] = np.arange(len(p))
    n1, n0 = yy.sum(), (1 - yy).sum()
    auc = (ranks[yy == 1].sum() - n1 * (n1 - 1) / 2) / (n1 * n0)
    cost = SPREAD_PCT[sym]
    for thr in (0.53, 0.55, 0.58):
        sel = p > thr
        selS = p < (1 - thr)
        nl, ns = sel.sum(), selS.sum()
        if nl + ns == 0:
            print(f"  thr {thr}: no trades"); continue
        pnl = np.concatenate([ff[sel] - cost, -ff[selS] - cost])
        print(f"  thr {thr}: trades={nl+ns} hit={(np.concatenate([ff[sel]>0, ff[selS]<0])).mean():.3f} "
              f"mean_net={1e4*pnl.mean():+.2f} bps  total={100*pnl.sum():+.1f}%")
    print(f"  AUC={auc:.4f} (0.5 = coin flip), n={len(p)}")

for sym in ("eurusd", "xauusd", "usdjpy"):
    print(f"=== {sym} ===")
    run(sym)
