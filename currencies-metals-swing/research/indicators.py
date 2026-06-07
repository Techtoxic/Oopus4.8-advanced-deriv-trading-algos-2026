"""
indicators.py — vectorised technical indicators.

Design rule: every function returns a Series/array aligned to the input index
using ONLY information available up to and including each bar. Any look-ahead
(e.g. a centred swing pivot needs `right` future bars to confirm) is the
caller's responsibility to delay — see strategy.py, which shifts confirmed
pivots by their right-window before use. Nothing here peeks forward on its own.
"""

from __future__ import annotations
import numpy as np
import pandas as pd


def ema(s: pd.Series, period: int) -> pd.Series:
    return s.ewm(span=period, adjust=False).mean()


def sma(s: pd.Series, period: int) -> pd.Series:
    return s.rolling(period).mean()


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder's ATR (RMA of true range)."""
    tr = true_range(df)
    return tr.ewm(alpha=1.0 / period, adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0.0)
    down = -delta.clip(upper=0.0)
    roll_up = up.ewm(alpha=1.0 / period, adjust=False).mean()
    roll_down = down.ewm(alpha=1.0 / period, adjust=False).mean()
    rs = roll_up / roll_down.replace(0.0, np.nan)
    out = 100.0 - 100.0 / (1.0 + rs)
    return out.fillna(50.0)


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder's ADX — trend-strength oscillator (0..100)."""
    up_move = df["high"].diff()
    down_move = -df["low"].diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    plus_dm = pd.Series(plus_dm, index=df.index)
    minus_dm = pd.Series(minus_dm, index=df.index)

    tr = true_range(df)
    atr_ = tr.ewm(alpha=1.0 / period, adjust=False).mean()
    plus_di = 100.0 * plus_dm.ewm(alpha=1.0 / period, adjust=False).mean() / atr_.replace(0.0, np.nan)
    minus_di = 100.0 * minus_dm.ewm(alpha=1.0 / period, adjust=False).mean() / atr_.replace(0.0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan)
    return dx.ewm(alpha=1.0 / period, adjust=False).mean().fillna(0.0)


def supertrend(df: pd.DataFrame, period: int = 10, mult: float = 3.0):
    """Classic SuperTrend. Returns (line, direction) where direction is +1
    (up / bullish) or -1 (down / bearish). This is the adaptive trend engine
    that mirrors the user's 'SelfAware Adaptive SuperTrend' indicator."""
    atr_ = atr(df, period)
    hl2 = (df["high"] + df["low"]) / 2.0
    upper = hl2 + mult * atr_
    lower = hl2 - mult * atr_

    n = len(df)
    close = df["close"].to_numpy()
    upper = upper.to_numpy()
    lower = lower.to_numpy()
    f_upper = np.full(n, np.nan)
    f_lower = np.full(n, np.nan)
    st = np.full(n, np.nan)
    direction = np.ones(n, dtype=int)

    for i in range(n):
        if i == 0 or np.isnan(upper[i]):
            f_upper[i] = upper[i]
            f_lower[i] = lower[i]
            st[i] = upper[i]
            direction[i] = -1
            continue
        f_upper[i] = upper[i] if (upper[i] < f_upper[i - 1] or close[i - 1] > f_upper[i - 1]) else f_upper[i - 1]
        f_lower[i] = lower[i] if (lower[i] > f_lower[i - 1] or close[i - 1] < f_lower[i - 1]) else f_lower[i - 1]

        if st[i - 1] == f_upper[i - 1]:
            st[i] = f_lower[i] if close[i] > f_upper[i] else f_upper[i]
        else:
            st[i] = f_upper[i] if close[i] < f_lower[i] else f_lower[i]
        direction[i] = 1 if st[i] == f_lower[i] else -1

    return pd.Series(st, index=df.index), pd.Series(direction, index=df.index)


def donchian(df: pd.DataFrame, period: int = 20):
    """Highest high / lowest low over the *prior* `period` bars (shifted by 1 so
    the current bar is excluded — breakout logic compares price to this)."""
    hh = df["high"].rolling(period).max().shift(1)
    ll = df["low"].rolling(period).min().shift(1)
    return hh, ll


def pivot_high(high: pd.Series, left: int, right: int) -> pd.Series:
    """Boolean Series: True at bars that are a confirmed local maximum with
    `left` lower bars before and `right` lower bars after. NOTE: a True at bar i
    is only *known* `right` bars later; strategy.py shifts by `right`."""
    h = high.to_numpy()
    n = len(h)
    out = np.zeros(n, dtype=bool)
    for i in range(left, n - right):
        window = h[i - left:i + right + 1]
        if h[i] == window.max() and (window.argmax() == left):
            out[i] = True
    return pd.Series(out, index=high.index)


def pivot_low(low: pd.Series, left: int, right: int) -> pd.Series:
    l = low.to_numpy()
    n = len(l)
    out = np.zeros(n, dtype=bool)
    for i in range(left, n - right):
        window = l[i - left:i + right + 1]
        if l[i] == window.min() and (window.argmin() == left):
            out[i] = True
    return pd.Series(out, index=low.index)


def rolling_percentile(s: pd.Series, window: int) -> pd.Series:
    """Percentile rank (0..1) of the current value within the trailing window."""
    def pct(x):
        return (x[-1] >= x).mean()
    return s.rolling(window).apply(pct, raw=True)


def last_confirmed_level(pivot_flags: pd.Series, price: pd.Series, right: int) -> pd.Series:
    """Forward-fill the price at each confirmed pivot, delayed by `right` bars so
    the level only becomes visible once the pivot is actually confirmed.
    Returns a Series of the most-recent confirmed pivot price at each bar."""
    confirmed = pivot_flags.shift(right).fillna(False)
    lvl = price.where(confirmed.astype(bool))
    return lvl.ffill()
