#!/usr/bin/env python3
"""London ORB refinement grid — IS (2015-2020) ONLY. The single best config
gets exactly ONE out-of-sample shot in validate_london_oos.py. No peeking."""
import sys
import numpy as np
import pandas as pd
from validate_intraday import load, stats, SPREAD_PCT

def orb(sym, or_start, or_len, stop_mode, tp_R, trend_filter, compress_filter,
        start="2015-01-01", end="2020-12-31", eod_hour=20):
    df = load(sym).loc[start:end]
    cost = SPREAD_PCT[sym]
    df["atr"] = (df["high"] - df["low"]).rolling(24).mean()
    df["ema_d"] = df["close"].ewm(span=24 * 50).mean()   # ~50-day EMA on H1
    df["date"] = df.index.date
    # daily OR range history for compression
    or_hist = {}
    trades = []
    for date, day in df.groupby("date"):
        orng = day[(day.index.hour >= or_start) & (day.index.hour < or_start + or_len)]
        sess = day[(day.index.hour >= or_start + or_len) & (day.index.hour <= eod_hour)]
        if len(orng) < or_len or len(sess) < 3:
            continue
        hi, lo = orng["high"].max(), orng["low"].min()
        rng = hi - lo
        atr = orng["atr"].iloc[-1]
        if rng <= 0 or not np.isfinite(atr) or atr <= 0:
            continue
        hist = or_hist.setdefault(sym, [])
        med = np.median(hist[-20:]) if len(hist) >= 10 else None
        hist.append(rng)
        if compress_filter and (med is None or rng > med):
            continue
        trend = 1 if orng["close"].iloc[-1] > orng["ema_d"].iloc[-1] else -1
        pos = 0
        for t, r in sess.iterrows():
            if pos == 0:
                want = 0
                if r["close"] > hi: want = 1
                elif r["close"] < lo: want = -1
                if want == 0: continue
                if trend_filter and want != trend: continue
                pos, entry = want, r["close"]
                stop = (lo if want == 1 else hi) if stop_mode == "range" else entry - want * 1.5 * atr
                target = entry + want * tp_R * abs(entry - stop)
                continue
            if pos == 1:
                if r["low"] <= stop: trades.append((t, (stop/entry-1) - cost)); pos = 0; break
                if r["high"] >= target: trades.append((t, (target/entry-1) - cost)); pos = 0; break
            else:
                if r["high"] >= stop: trades.append((t, (entry/stop-1) - cost)); pos = 0; break
                if r["low"] <= target: trades.append((t, (entry/target-1) - cost)); pos = 0; break
        if pos != 0:
            last = sess.iloc[-1]
            trades.append((sess.index[-1], pos*(last["close"]/entry-1) - cost))
    return pd.Series({t: r for t, r in trades}).sort_index()

if __name__ == "__main__":
    sym = sys.argv[1] if len(sys.argv) > 1 else "xauusd"
    rows = []
    for or_start in (6, 7, 8):
        for stop_mode in ("range", "atr"):
            for tp_R in (1.5, 2.0, 3.0):
                for tf in (True, False):
                    for cf in (True, False):
                        s = orb(sym, or_start, 2, stop_mode, tp_R, tf, cf)
                        st = stats(s, f"or{or_start} {stop_mode} tp{tp_R} tf{int(tf)} cf{int(cf)}")
                        if "pf" in st:
                            rows.append(st)
    rows.sort(key=lambda r: -r.get("ann_sharpe", -9))
    for r in rows[:12]:
        print(r)
    print("...")
    for r in rows[-3:]:
        print(r)
