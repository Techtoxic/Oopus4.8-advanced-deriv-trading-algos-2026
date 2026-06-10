#!/usr/bin/env python3
"""
Validation: time-series momentum (TSMOM, Moskowitz et al. 2012) on metals/FX
with vol targeting — is the effect present in OUR data, after costs?
Rule: sign of k-month return (ensemble of 1/3/6/12m votes), weekly rebalance,
position scaled to 10% annualized vol target. Costs charged on turnover.
Also: + COT-index tilt variant for gold.
"""
import os, sys
import numpy as np, pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "cot_study"))
from cot_study import load_price, load_cot, cot_index, SPREAD_PCT

OUT = os.path.join(os.path.dirname(__file__), "results"); os.makedirs(OUT, exist_ok=True)

def tsmom(pair, use_cot=False, vol_target=0.10, oos_split=None):
    px = load_price(pair)["Close"].resample("W-FRI").last().dropna()
    rets = px.pct_change()
    votes = sum(np.sign(px.pct_change(k)) for k in (4, 13, 26, 52))  # 1/3/6/12m in weeks
    sig = np.sign(votes)
    vol = rets.rolling(26).std() * np.sqrt(52)
    lev = (vol_target / vol).clip(0, 3)
    pos = (sig * lev).shift(1)  # decide on Friday close, hold next week
    if use_cot:
        base = {"XAUUSD": "GOLD", "XAGUSD": "SILVER", "EURUSD": "EUR"}.get(pair)
        if base:
            cot = load_cot(base)
            idx = cot_index(cot["noncomm_net"])
            cidx = pd.Series(idx.values, index=cot["avail"]).reindex(
                px.index, method="ffill")
            tilt = np.where(cidx > 55, 1.25, np.where(cidx < 45, 0.5, 1.0))
            pos = pos * np.where((np.sign(pos) > 0) & (cidx < 45), 0.5,
                  np.where((np.sign(pos) < 0) & (cidx > 55), 0.5, 1.0)) * 1.0
    cost = SPREAD_PCT.get(pair, 1e-4)
    turn = pos.diff().abs().fillna(0)
    pnl = pos * rets - turn * cost
    pnl = pnl.dropna()
    def stats(p):
        if len(p) < 30: return {}
        ann = p.mean() * 52; vol_ = p.std() * np.sqrt(52)
        eq = (1 + p).cumprod(); dd = (eq / eq.cummax() - 1).min()
        return dict(ann_ret=round(100*ann,1), sharpe=round(ann/vol_,2), maxdd=round(100*dd,1))
    full = stats(pnl)
    res = dict(pair=pair, cot=use_cot, **full)
    if oos_split:
        res["IS"] = stats(pnl[pnl.index < oos_split])
        res["OOS"] = stats(pnl[pnl.index >= oos_split])
    return res

print(f"{'pair':8s} {'cot':5s} {'ann%':>6s} {'shrp':>6s} {'dd%':>7s}   IS/OOS")
for pair in ("XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "AUDUSD"):
    for use_cot in (False, True):
        r = tsmom(pair, use_cot, oos_split="2018-01-01")
        print(f"{r['pair']:8s} {str(r['cot']):5s} {r.get('ann_ret',0):6.1f} {r.get('sharpe',0):6.2f} "
              f"{r.get('maxdd',0):7.1f}   IS {r.get('IS')} OOS {r.get('OOS')}")
