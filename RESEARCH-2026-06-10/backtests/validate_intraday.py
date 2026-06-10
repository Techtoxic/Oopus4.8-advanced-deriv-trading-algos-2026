#!/usr/bin/env python3
"""
Intraday H1 validation suite — London breakout, Asian-range fade, XAU/XAG pairs.

Data: Dukascopy H1 bid (UTC), 2015→2026. True walk-forward: parameters chosen
on 2015-2020 (IS), verdicts use 2021-2026 (OOS) which no parameter ever saw.
Costs: full spread + slippage charged per round trip (SPREAD_PCT).

Usage: python3 validate_intraday.py [london|asian|pairs|all]
"""
import glob
import os
import sys

import numpy as np
import pandas as pd

DATA = os.environ.get("FABLE_H1_DATA", "/home/research-data/chunks")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
os.makedirs(OUT, exist_ok=True)

SPREAD_PCT = {"xauusd": 0.00018, "xagusd": 0.00045, "eurusd": 0.00007,
              "gbpusd": 0.00009, "usdjpy": 0.00008, "audusd": 0.00009}
OOS_SPLIT = "2021-01-01"


def load(sym: str) -> pd.DataFrame:
    files = sorted(glob.glob(f"{DATA}/{sym}-*.csv"))
    if not files:
        raise FileNotFoundError(sym)
    df = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
    df["ts"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.set_index("ts").sort_index()
    df = df[~df.index.duplicated()]
    df = df[df["open"] > 0]
    return df[["open", "high", "low", "close"]]


def stats(trades: pd.Series, label: str) -> dict:
    if len(trades) < 20:
        return dict(label=label, n=len(trades), note="too few trades")
    eq = (1 + trades).cumprod()
    dd = (eq / eq.cummax() - 1).min()
    pf = trades[trades > 0].sum() / abs(trades[trades < 0].sum()) if (trades < 0).any() else np.inf
    years = max((trades.index[-1] - trades.index[0]).days / 365.25, 0.25)
    sharpe = trades.mean() / (trades.std() + 1e-12) * np.sqrt(len(trades) / years)
    return dict(label=label, n=len(trades), win=round(float((trades > 0).mean()), 3),
                pf=round(float(pf), 2), ann_sharpe=round(float(sharpe), 2),
                ret_pct=round(float(100 * (eq.iloc[-1] - 1)), 1),
                maxdd_pct=round(float(100 * dd), 1))


# ------------------------------------------------------------------ LONDON ORB
def london_breakout(sym="xauusd", or_start=7, or_len=2, risk_atr=1.5, tp_R=2.0,
                    vol_gate=True, eod_hour=20):
    """Opening-range breakout: range = [or_start, or_start+or_len) UTC;
    trade first H1 close beyond range, ATR stop, R target, flat by eod_hour."""
    df = load(sym)
    cost = SPREAD_PCT[sym]
    df["atr"] = (df["high"] - df["low"]).rolling(24).mean()
    df["atr_pct_rank"] = df["atr"].rolling(24 * 90).rank(pct=True)
    df["date"] = df.index.date
    trades = []
    for date, day in df.groupby("date"):
        orng = day[(day.index.hour >= or_start) & (day.index.hour < or_start + or_len)]
        sess = day[(day.index.hour >= or_start + or_len) & (day.index.hour <= eod_hour)]
        if len(orng) < or_len or len(sess) < 3:
            continue
        hi, lo = orng["high"].max(), orng["low"].min()
        atr = orng["atr"].iloc[-1]
        if not np.isfinite(atr) or atr <= 0:
            continue
        if vol_gate:
            vr = orng["atr_pct_rank"].iloc[-1]
            if not np.isfinite(vr) or vr < 0.25:   # skip dead-vol days
                continue
        pos = 0
        for i, (t, r) in enumerate(sess.iterrows()):
            if pos == 0:
                if r["close"] > hi:
                    pos, entry, stop = 1, r["close"], r["close"] - risk_atr * atr
                elif r["close"] < lo:
                    pos, entry, stop = -1, r["close"], r["close"] + risk_atr * atr
                if pos != 0:
                    target = entry + pos * tp_R * abs(entry - stop)
                continue
            # manage
            if pos == 1:
                if r["low"] <= stop:
                    trades.append((t, (stop / entry - 1) - cost)); pos = 0; break
                if r["high"] >= target:
                    trades.append((t, (target / entry - 1) - cost)); pos = 0; break
            else:
                if r["high"] >= stop:
                    trades.append((t, (entry / stop - 1) - cost)); pos = 0; break
                if r["low"] <= target:
                    trades.append((t, (entry / target - 1) - cost)); pos = 0; break
        if pos != 0:
            last = sess.iloc[-1]
            ret = pos * (last["close"] / entry - 1) - cost
            trades.append((sess.index[-1], ret))
    s = pd.Series({t: r for t, r in trades}).sort_index()
    return s


# ------------------------------------------------------------- ASIAN-RANGE MR
def asian_fade(sym="eurusd", asia_start=0, asia_end=6, k_break=0.25, stop_atr=1.0,
               lowvol_only=True, exit_hour=14):
    """Fade pokes beyond the Asian range during early London, low-vol regime only."""
    df = load(sym)
    cost = SPREAD_PCT[sym]
    df["atr"] = (df["high"] - df["low"]).rolling(24).mean()
    df["atr_rank"] = df["atr"].rolling(24 * 90).rank(pct=True)
    df["date"] = df.index.date
    trades = []
    for date, day in df.groupby("date"):
        asia = day[(day.index.hour >= asia_start) & (day.index.hour < asia_end)]
        sess = day[(day.index.hour >= asia_end) & (day.index.hour <= exit_hour)]
        if len(asia) < 4 or len(sess) < 3:
            continue
        hi, lo = asia["high"].max(), asia["low"].min()
        rng = hi - lo
        atr = asia["atr"].iloc[-1]
        if rng <= 0 or not np.isfinite(atr) or atr <= 0:
            continue
        if lowvol_only:
            vr = asia["atr_rank"].iloc[-1]
            if not np.isfinite(vr) or vr > 0.6:
                continue
        pos = 0
        for t, r in sess.iterrows():
            if pos == 0:
                if r["high"] > hi + k_break * rng and r["close"] < hi + 0.75 * rng:
                    pos, entry = -1, r["close"]
                    stop = entry + stop_atr * atr
                    target = (hi + lo) / 2
                elif r["low"] < lo - k_break * rng and r["close"] > lo - 0.75 * rng:
                    pos, entry = 1, r["close"]
                    stop = entry - stop_atr * atr
                    target = (hi + lo) / 2
                continue
            if pos == 1:
                if r["low"] <= stop: trades.append((t, (stop / entry - 1) - cost)); pos = 0; break
                if r["high"] >= target: trades.append((t, (target / entry - 1) - cost)); pos = 0; break
            else:
                if r["high"] >= stop: trades.append((t, (entry / stop - 1) - cost)); pos = 0; break
                if r["low"] <= target: trades.append((t, (entry / target - 1) - cost)); pos = 0; break
        if pos != 0:
            last = sess.iloc[-1]
            trades.append((sess.index[-1], pos * (last["close"] / entry - 1) - cost))
    return pd.Series({t: r for t, r in trades}).sort_index()


# ------------------------------------------------------------------- XAU/XAG
def pairs_xauxag(z_in=2.0, z_out=0.5, z_stop=3.5, look=240, max_hold=240, corr_min=0.5):
    g, s = load("xauusd"), load("xagusd")
    df = pd.DataFrame({"g": g["close"], "s": s["close"]}).dropna()
    lg, ls = np.log(df["g"]), np.log(df["s"])
    beta = lg.rolling(look).cov(ls) / ls.rolling(look).var()
    spread = lg - beta * ls
    mu, sd = spread.rolling(look).mean(), spread.rolling(look).std()
    z = (spread - mu) / (sd + 1e-12)
    corr = lg.diff().rolling(look).corr(ls.diff())
    cost = SPREAD_PCT["xauusd"] + SPREAD_PCT["xagusd"]

    trades = []
    pos = 0
    entry_i = 0
    idx = df.index
    zv, gv, sv, bv, cv = z.values, df["g"].values, df["s"].values, beta.values, corr.values
    for i in range(look + 1, len(df)):
        if pos == 0:
            if not np.isfinite(zv[i]) or not np.isfinite(bv[i]) or cv[i] < corr_min:
                continue
            if zv[i] > z_in:   # spread rich: short gold, long silver
                pos, entry_i, eg, es, eb = -1, i, gv[i], sv[i], bv[i]
            elif zv[i] < -z_in:
                pos, entry_i, eg, es, eb = 1, i, gv[i], sv[i], bv[i]
            continue
        exit_now = (abs(zv[i]) < z_out or abs(zv[i]) > z_stop or (i - entry_i) > max_hold)
        if exit_now:
            rg = pos * (gv[i] / eg - 1)
            rs = -pos * eb * (sv[i] / es - 1)
            trades.append((idx[i], rg + rs - cost))
            pos = 0
    return pd.Series({t: r for t, r in trades}).sort_index()


def report(s: pd.Series, name: str):
    is_, oos = s[s.index < OOS_SPLIT], s[s.index >= OOS_SPLIT]
    print(f"\n=== {name} ===")
    for lab, x in (("FULL", s), ("IS 2015-2020", is_), ("OOS 2021-2026", oos)):
        print(" ", stats(x, lab))
    return {name: {"full": stats(s, "full"), "is": stats(is_, "is"), "oos": stats(oos, "oos")}}


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    out = {}
    if which in ("london", "all"):
        for sym in ("xauusd", "eurusd", "gbpusd"):
            try:
                out.update(report(london_breakout(sym), f"london_{sym}"))
            except FileNotFoundError:
                print(f"(skip london {sym}: no data yet)")
    if which in ("asian", "all"):
        for sym in ("eurusd", "usdjpy", "audusd"):
            try:
                out.update(report(asian_fade(sym), f"asian_{sym}"))
            except FileNotFoundError:
                print(f"(skip asian {sym}: no data yet)")
    if which in ("pairs", "all"):
        try:
            out.update(report(pairs_xauxag(), "pairs_xauxag"))
        except FileNotFoundError:
            print("(skip pairs: need xau+xag)")
    import json
    with open(os.path.join(OUT, "intraday_validation.json"), "a") as f:
        f.write(json.dumps(out, default=str) + "\n")
