"""HMA/RSI/LinReg long-only signals, reimplemented to match Ziad Francis' utils.prepare_dataset."""
from math import floor, sqrt
import numpy as np
import pandas as pd


def wma(x, n):
    w = np.arange(1, n + 1, dtype=float)
    return x.rolling(n).apply(lambda v: np.dot(v, w) / w.sum(), raw=True)


def hma(close, n):
    half = max(floor(n / 2 + 0.5), 1)
    root = max(floor(sqrt(n)), 1)
    return wma(2 * wma(close, half) - wma(close, n), root)


def rsi(close, n=14):
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, min_periods=n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, min_periods=n, adjust=False).mean()
    return 100 * up / (up + dn)


def linreg(close, n=50):
    x = np.arange(n, dtype=float)
    xm = x.mean()
    denom = ((x - xm) ** 2).sum()

    def end(v):
        slope = ((x - xm) * (v - v.mean())).sum() / denom
        return v.mean() + slope * (n - 1 - xm)
    return close.rolling(n).apply(end, raw=True)


def prepare(df, fast=16, slow=64, rsi_len=14, rsi_min=52.0, lr_len=50):
    out = df.copy()
    c = out['Close']
    out['hma_fast'], out['hma_slow'] = hma(c, fast), hma(c, slow)
    out['rsi'], out['linreg'] = rsi(c, rsi_len), linreg(c, lr_len)
    fp, sp = out.hma_fast.shift(1), out.hma_slow.shift(1)
    cross_up = (fp <= sp) & (out.hma_fast > out.hma_slow)
    cross_dn = (fp >= sp) & (out.hma_fast < out.hma_slow)
    out['entry_long'] = (cross_up & (out.rsi > rsi_min) & (c > out.linreg)).astype(int)
    out['exit_long'] = cross_dn.astype(int)
    return out.dropna(subset=['hma_fast', 'hma_slow', 'rsi', 'linreg'])
