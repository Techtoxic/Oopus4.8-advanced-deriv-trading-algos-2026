"""
strategy.py — the Adaptive Swing logic, as a pure signal generator.

This is the *same* idea the MQL5 EA implements, expressed in Python so it can be
honestly walk-forward tested. It consumes a daily OHLC frame and emits, per bar:

    regime        +1 / -1 / 0   (trend direction, 0 = stand aside)
    long_signal   bool          (enter long at NEXT bar open)
    short_signal  bool          (enter short at NEXT bar open)
    atr           float         (volatility unit for adaptive SL/TP/sizing)

Everything is decided on the *closed* bar i and executed by the backtester at
the open of bar i+1 — no look-ahead, no repaint. Adaptivity is structural:
there is not a single hard-coded pip distance anywhere. Stops, targets, trails
and position size are all multiples of ATR / account risk, decided downstream.
"""

from __future__ import annotations
from dataclasses import dataclass, field
import numpy as np
import pandas as pd
import indicators as ind


@dataclass
class SwingConfig:
    # --- trend regime (adaptive SuperTrend + ADX strength gate) ---
    st_period: int = 10
    st_mult: float = 3.0
    adx_period: int = 14
    adx_min: float = 18.0          # below this = chop, stand aside
    ema_trend: int = 50            # trend filter
    ema_pullback: int = 20         # dynamic pullback reference / dynamic S&R

    # --- volatility regime gate (avoid dead & berserk markets) ---
    atr_period: int = 14
    atr_pct_window: int = 100
    atr_pct_min: float = 0.05      # ignore the flattest 5% of regimes
    atr_pct_max: float = 0.97      # ignore the wildest 3% of regimes

    # --- entry ---
    entry_mode: str = "either"     # "pullback" | "breakout" | "either"
    pullback_lookback: int = 6     # bars within which a pullback must have occurred
    donchian_period: int = 20      # breakout channel
    rsi_period: int = 14

    # --- risk (consumed by the backtester / EA, kept here for one source of truth) ---
    sl_atr_mult: float = 2.0       # stop = entry -/+ atr*this
    tp_R: float = 2.5              # take profit at this many R (0 = pure trail)
    trail_atr_mult: float = 3.0    # chandelier trail distance (0 = off)
    breakeven_R: float = 1.0       # move stop to BE after +this R (0 = off)
    time_stop_bars: int = 40       # give up a stagnant trade after N bars (0 = off)

    # --- COT bias filter (applied by backtester when a bias series is supplied) ---
    use_cot: bool = False
    cot_allow_uncertain: bool = True   # if True, UNCERTAIN does not block trades


def generate(df: pd.DataFrame, cfg: SwingConfig) -> pd.DataFrame:
    close, high, low, open_ = df["close"], df["high"], df["low"], df["open"]

    st_line, st_dir = ind.supertrend(df, cfg.st_period, cfg.st_mult)
    adx = ind.adx(df, cfg.adx_period)
    atr = ind.atr(df, cfg.atr_period)
    ema_t = ind.ema(close, cfg.ema_trend)
    ema_p = ind.ema(close, cfg.ema_pullback)
    rsi = ind.rsi(close, cfg.rsi_period)
    don_hi, don_lo = ind.donchian(df, cfg.donchian_period)
    atr_pct = ind.rolling_percentile(atr, cfg.atr_pct_window)

    vol_ok = (atr_pct >= cfg.atr_pct_min) & (atr_pct <= cfg.atr_pct_max)
    strong = adx >= cfg.adx_min

    trend_up = (st_dir == 1) & (close > ema_t) & strong & vol_ok
    trend_dn = (st_dir == -1) & (close < ema_t) & strong & vol_ok
    regime = np.select([trend_up, trend_dn], [1, -1], default=0)

    # Pullback continuation: price dipped to the dynamic pullback EMA recently,
    # then the current bar resumes in the trend direction with momentum.
    dipped_long = (low <= ema_p).rolling(cfg.pullback_lookback).max().fillna(0).astype(bool)
    dipped_short = (high >= ema_p).rolling(cfg.pullback_lookback).max().fillna(0).astype(bool)
    bull_bar = (close > open_) & (close > close.shift(1))
    bear_bar = (close < open_) & (close < close.shift(1))

    long_pullback = trend_up & dipped_long & bull_bar & (close >= ema_p)
    short_pullback = trend_dn & dipped_short & bear_bar & (close <= ema_p)

    # Breakout continuation (LVRB / Donchian style): close clears the prior
    # N-bar extreme in the trend direction.
    long_breakout = trend_up & (close > don_hi) & bull_bar
    short_breakout = trend_dn & (close < don_lo) & bear_bar

    if cfg.entry_mode == "pullback":
        long_sig, short_sig = long_pullback, short_pullback
    elif cfg.entry_mode == "breakout":
        long_sig, short_sig = long_breakout, short_breakout
    else:  # either
        long_sig = long_pullback | long_breakout
        short_sig = short_pullback | short_breakout

    out = pd.DataFrame(index=df.index)
    out["regime"] = regime
    out["long_signal"] = long_sig.fillna(False)
    out["short_signal"] = short_sig.fillna(False)
    out["atr"] = atr
    out["adx"] = adx
    out["rsi"] = rsi
    out["ema_trend"] = ema_t
    out["ema_pullback"] = ema_p
    out["st_dir"] = st_dir
    out["atr_pct"] = atr_pct
    # warm-up guard: blank signals until indicators are seeded
    warm = max(cfg.ema_trend, cfg.donchian_period, cfg.atr_pct_window, cfg.adx_period) + 2
    out.iloc[:warm, out.columns.get_indexer(["long_signal", "short_signal"])] = False
    return out
