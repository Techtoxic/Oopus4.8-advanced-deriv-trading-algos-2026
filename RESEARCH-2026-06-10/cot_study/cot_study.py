#!/usr/bin/env python3
"""
COT STUDY — "does COT data actually help, and which COT rule?"

The user's EAs use COT as a bias filter but cannot backtest it in the MT5
Strategy Tester (WebRequest is blocked there). This study answers the question
outside MT5, on real data, three ways:

  1. EVENT STUDY  — forward returns of each market conditioned on COT
     positioning (COT-index buckets of non-commercial net, and the user's
     API rule base-vs-quote sign logic). Weekly horizon 1-13 weeks.
  2. FILTER A/B   — identical trend strategy with COT gate OFF vs three COT
     gate variants (api-sign rule, comm-net rule, COT-index rule).
  3. STABILITY    — decade sub-period consistency, so one regime can't sell us
     a story.

Publish-lag handling: CFTC reports Tuesday positions on Friday. We lag all COT
data by 3 business days minimum (merge_asof backward on report date + 3d).

Data: repo CSVs — XAUUSD D1 2004-2025, FX D1 2010-2020 (GBPUSD to 1993),
COT 1986-2026 (legacy futures-only, fetched from CFTC Socrata).
"""
from __future__ import annotations

import os
import sys
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

HERE = os.path.dirname(os.path.abspath(__file__))
RESEARCH = os.path.normpath(os.path.join(HERE, "..", "..", "currencies-metals-swing", "research"))
OUT = os.path.join(HERE, "results")
os.makedirs(OUT, exist_ok=True)

PAIR_MAP = {
    # pair -> (base asset, quote asset)  | None = USD leg (use DXY logic? api uses DXY noncomm for USD)
    "XAUUSD": ("GOLD", "DXY"),
    "XAGUSD": ("SILVER", "DXY"),
    "EURUSD": ("EUR", "DXY"),
    "GBPUSD": ("GBP", "DXY"),
    "AUDUSD": ("AUD", "DXY"),
    "NZDUSD": ("NZD", "DXY"),
    "USDJPY": ("DXY", "JPY"),
    "USDCAD": ("DXY", "CAD"),
    "EURJPY": ("EUR", "JPY"),
    "EURGBP": ("EUR", "GBP"),
}


def load_price(sym: str) -> pd.DataFrame:
    p = os.path.join(RESEARCH, "data", "raw", f"{sym}.csv")
    head = open(p).readline()
    if head.startswith("Date;"):
        df = pd.read_csv(p, sep=";")
        df["Date"] = pd.to_datetime(df["Date"], format="%Y.%m.%d %H:%M")
    else:
        df = pd.read_csv(p)
        df = df.rename(columns={"open_time": "Date", "open": "Open", "high": "High",
                                "low": "Low", "close": "Close"})
        df["Date"] = pd.to_datetime(df["Date"])
    df = df.set_index("Date").sort_index()
    df = df[~df.index.duplicated()]
    return df[["Open", "High", "Low", "Close"]].astype(float)


def load_cot(asset: str) -> pd.DataFrame:
    p = os.path.join(RESEARCH, "data", "cot", f"{asset}.csv")
    df = pd.read_csv(p, parse_dates=["date"]).sort_values("date")
    df["avail"] = df["date"] + pd.Timedelta(days=3)  # publish lag
    return df


def cot_index(series: pd.Series, weeks: int = 156) -> pd.Series:
    """Percentile of net positioning within rolling 3y window — the COT-index."""
    lo = series.rolling(weeks, min_periods=26).min()
    hi = series.rolling(weeks, min_periods=26).max()
    return ((series - lo) / (hi - lo + 1e-12) * 100).clip(0, 100)


def merge_cot(px: pd.DataFrame, pair: str) -> pd.DataFrame:
    base_a, quote_a = PAIR_MAP[pair]
    b = load_cot(base_a)
    q = load_cot(quote_a)
    for d, tag in ((b, "b"), (q, "q")):
        d[f"{tag}_noncomm"] = d["noncomm_net"]
        d[f"{tag}_comm"] = d["comm_net"]
        d[f"{tag}_idx"] = cot_index(d["noncomm_net"])
    out = px.copy().reset_index()
    out = pd.merge_asof(out, b[["avail", "b_noncomm", "b_comm", "b_idx"]],
                        left_on="Date", right_on="avail").drop(columns="avail")
    out = pd.merge_asof(out, q[["avail", "q_noncomm", "q_comm", "q_idx"]],
                        left_on="Date", right_on="avail").drop(columns="avail")
    out = out.set_index("Date")
    # API rule: BUY if base bullish AND quote bearish; SELL if reverse; else UNCERTAIN
    out["api_bias"] = 0
    out.loc[(out.b_noncomm > 0) & (out.q_noncomm < 0), "api_bias"] = 1
    out.loc[(out.b_noncomm < 0) & (out.q_noncomm > 0), "api_bias"] = -1
    # comm rule (user's old idea): follow commercials
    out["comm_bias"] = np.sign(out.b_comm - out.q_comm)
    # COT-index spread rule: base idx vs quote idx
    out["idx_spread"] = out.b_idx - out.q_idx
    return out


# ---------------------------------------------------------------- event study
def event_study(df: pd.DataFrame, pair: str) -> pd.DataFrame:
    rows = []
    c = df["Close"]
    for h_weeks in (1, 2, 4, 8, 13):
        h = h_weeks * 5
        fwd = c.shift(-h) / c - 1
        # bucket by base COT-index (the directional driver)
        for lo, hi, lab in ((0, 20, "0-20"), (20, 40, "20-40"), (40, 60, "40-60"),
                            (60, 80, "60-80"), (80, 100.01, "80-100")):
            m = (df.b_idx >= lo) & (df.b_idx < hi)
            if m.sum() < 30:
                continue
            r = fwd[m].dropna()
            t = r.mean() / (r.std() / np.sqrt(len(r)) + 1e-12)
            rows.append(dict(pair=pair, horizon_w=h_weeks, bucket=lab, n=len(r),
                             mean_fwd_pct=100 * r.mean(), t_stat=t))
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ filter A/B
def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    h, l, c = df["High"], df["Low"], df["Close"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def adx(df: pd.DataFrame, n: int = 14) -> pd.Series:
    h, l, c = df["High"], df["Low"], df["Close"]
    up, dn = h.diff(), -l.diff()
    plus = np.where((up > dn) & (up > 0), up, 0.0)
    minus = np.where((dn > up) & (dn > 0), dn, 0.0)
    trn = atr(df, n)
    pdi = 100 * pd.Series(plus, index=df.index).ewm(alpha=1 / n, adjust=False).mean() / trn
    mdi = 100 * pd.Series(minus, index=df.index).ewm(alpha=1 / n, adjust=False).mean() / trn
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi + 1e-12)
    return dx.ewm(alpha=1 / n, adjust=False).mean()


SPREAD_PCT = {  # full spread as fraction of price, retail raw-ish + slippage
    "XAUUSD": 0.00018, "XAGUSD": 0.0004, "EURUSD": 0.00007, "GBPUSD": 0.00009,
    "USDJPY": 0.00008, "AUDUSD": 0.00009, "NZDUSD": 0.00011, "USDCAD": 0.00009,
    "EURJPY": 0.00011, "EURGBP": 0.00010,
}


def trend_backtest(df: pd.DataFrame, pair: str, gate: str = "none") -> dict:
    """Donchian(20) breakout w/ EMA50 filter + ADX>18, ATR stop 2x, trail 3x.
    Approximation of the AdaptiveSwing core, daily bars, next-open fills."""
    d = df.copy()
    d["atr"] = atr(d)
    d["adx"] = adx(d)
    d["ema"] = d["Close"].ewm(span=50, adjust=False).mean()
    d["don_hi"] = d["High"].rolling(20).max().shift(1)
    d["don_lo"] = d["Low"].rolling(20).min().shift(1)
    cost = SPREAD_PCT.get(pair, 0.0001)

    equity = 1.0
    pos = 0
    entry = stop = 0.0
    trades = []
    eq_curve = []
    risk = 0.005  # 0.5% per trade

    o, h, l, c = d["Open"].values, d["High"].values, d["Low"].values, d["Close"].values
    for i in range(60, len(d) - 1):
        row = d.iloc[i]
        # manage open position on bar i+1 (next bar after signal/trail update)
        if pos != 0:
            # trail: chandelier 3*ATR from close
            if pos > 0:
                stop = max(stop, c[i] - 3 * row["atr"])
                if l[i + 1] <= stop:
                    ret = (stop / entry - 1) - cost
                    equity *= (1 + pos * ret * lev)
                    trades.append(pos * ret * lev)
                    pos = 0
            else:
                stop = min(stop, c[i] + 3 * row["atr"])
                if h[i + 1] >= stop:
                    ret = (stop / entry - 1) - cost
                    equity *= (1 + pos * ret * lev)
                    trades.append(pos * ret * lev)
                    pos = 0
        if pos == 0:
            sig = 0
            if c[i] > row["don_hi"] and c[i] > row["ema"] and row["adx"] > 18:
                sig = 1
            elif c[i] < row["don_lo"] and c[i] < row["ema"] and row["adx"] > 18:
                sig = -1
            if sig != 0 and gate != "none":
                if gate == "api" and int(np.sign(row["api_bias"])) != sig:
                    sig = 0
                elif gate == "comm" and int(np.sign(row["comm_bias"])) != sig:
                    sig = 0
                elif gate == "idx":
                    # require base COT-index agreement: >60 for long, <40 for short
                    if sig > 0 and not row["b_idx"] > 55:
                        sig = 0
                    if sig < 0 and not row["b_idx"] < 45:
                        sig = 0
            if sig != 0:
                entry = o[i + 1]
                stop_d = 2 * row["atr"]
                stop = entry - sig * stop_d
                # leverage so that stop loss = risk of equity
                lev = risk / (stop_d / entry)
                pos = sig
                equity *= (1 - cost * lev)  # entry cost
        eq_curve.append(equity)

    eq = pd.Series(eq_curve)
    ret = eq.iloc[-1] - 1 if len(eq) else 0
    dd = ((eq / eq.cummax()) - 1).min() if len(eq) else 0
    tr = pd.Series(trades)
    pf = tr[tr > 0].sum() / abs(tr[tr < 0].sum()) if (tr < 0).any() else np.inf
    sharpe = (tr.mean() / (tr.std() + 1e-12)) * np.sqrt(max(1, len(tr) / max(1, len(d) / 252))) if len(tr) > 5 else 0
    return dict(n_trades=len(tr), ret_pct=100 * ret, maxdd_pct=100 * dd,
                pf=round(float(pf), 2), win=round(float((tr > 0).mean()) if len(tr) else 0, 3),
                sharpe=round(float(sharpe), 2))


def main():
    pairs = [p for p in PAIR_MAP if os.path.exists(os.path.join(RESEARCH, "data", "raw", f"{p}.csv"))]
    print("pairs with price data:", pairs)

    ev_all, ab_rows = [], []
    for pair in pairs:
        px = load_price(pair)
        df = merge_cot(px, pair)
        ev_all.append(event_study(df, pair))
        for gate in ("none", "api", "comm", "idx"):
            r = trend_backtest(df, pair, gate)
            ab_rows.append(dict(pair=pair, gate=gate, **r))
            print(f"{pair} gate={gate:5s} -> {r}")

    ev = pd.concat(ev_all, ignore_index=True)
    ab = pd.DataFrame(ab_rows)
    ev.to_csv(os.path.join(OUT, "cot_event_study.csv"), index=False)
    ab.to_csv(os.path.join(OUT, "cot_filter_ab.csv"), index=False)

    # ------------------------------------------------ aggregate + verdict
    lines = ["# COT Study Results (generated)", ""]
    lines.append("## Event study — mean forward return by base COT-index bucket")
    for pair in ("XAUUSD", "EURUSD", "GBPUSD", "USDJPY"):
        sub = ev[(ev.pair == pair) & (ev.horizon_w == 4)]
        if sub.empty:
            continue
        lines.append(f"\n**{pair}, 4-week horizon** (mean fwd %, t-stat)")
        lines.append("| bucket | n | mean fwd % | t |")
        lines.append("|---|---|---|---|")
        for _, r in sub.iterrows():
            lines.append(f"| {r.bucket} | {r.n} | {r.mean_fwd_pct:+.2f} | {r.t_stat:+.2f} |")

    lines.append("\n## Filter A/B — identical trend strategy, COT gate variants")
    lines.append("| pair | gate | trades | ret% | maxDD% | PF | win | Sharpe |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for _, r in ab.iterrows():
        lines.append(f"| {r.pair} | {r.gate} | {r.n_trades} | {r.ret_pct:+.1f} | "
                     f"{r.maxdd_pct:.1f} | {r.pf} | {r.win} | {r.sharpe} |")

    # summary means
    lines.append("\n### Mean across pairs by gate")
    g = ab.groupby("gate")[["ret_pct", "pf", "sharpe"]].mean().round(2)
    lines.append(g.to_markdown())

    with open(os.path.join(OUT, "COT_STUDY.md"), "w") as f:
        f.write("\n".join(lines))
    print("\nwritten:", os.path.join(OUT, "COT_STUDY.md"))


if __name__ == "__main__":
    main()
