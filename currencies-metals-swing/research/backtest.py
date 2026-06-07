"""
backtest.py — event-driven daily backtester with honest cost & risk modelling.

Conventions
-----------
* A signal is decided on the CLOSE of bar i and filled at the OPEN of bar i+1.
* Entry/exit pay half the spread + slippage; an optional commission (in price
  units) is charged per round turn.
* Stops/targets are checked intrabar against the bar high/low; if a bar gaps
  through the stop, the fill is the (worse) open. If both SL and TP sit inside
  one bar, the STOP is assumed hit first (pessimistic).
* Position size = (equity * risk_pct) / stop_distance, capped by max_leverage so
  a tiny ATR can never imply an absurd notional. This is the adaptive sizing the
  EA uses too — never a fixed lot.
* Equity compounds on realised PnL. Drawdown is measured on a daily
  mark-to-close curve so open risk is visible.

Nothing here is fitted; parameters come from SwingConfig. Parameter selection is
done explicitly and out-of-sample in run_research.py / ml.py.
"""

from __future__ import annotations
from dataclasses import dataclass, field
import numpy as np
import pandas as pd

import data as datamod
from strategy import SwingConfig


@dataclass
class CostModel:
    spread: float            # full dealing spread in price units
    slippage: float          # per-side slippage in price units
    commission: float = 0.0  # per round-turn, in price units (per unit traded)


@dataclass
class Trade:
    symbol: str
    direction: int
    entry_date: pd.Timestamp
    entry: float
    stop: float
    take: float
    units: float
    init_risk: float = 0.0
    exit_date: pd.Timestamp = None
    exit: float = np.nan
    reason: str = ""
    pnl: float = 0.0
    r_multiple: float = 0.0
    bars_held: int = 0
    equity_after: float = 0.0


def _round_turn_cost(units, cm: CostModel):
    # spread + slippage are applied to entry & exit prices directly; commission
    # is a flat per-unit round-turn charge.
    return units * cm.commission


def backtest(df: pd.DataFrame, sig: pd.DataFrame, cfg: SwingConfig, symbol: str,
             cost: CostModel, equity0: float = 10_000.0, risk_pct: float = 0.01,
             max_leverage: float = 50.0, cot_bias: pd.Series | None = None) -> dict:
    idx = df.index
    o = df["open"].to_numpy(); h = df["high"].to_numpy()
    l = df["low"].to_numpy(); c = df["close"].to_numpy()
    atr = sig["atr"].to_numpy()
    regime = sig["regime"].to_numpy()
    long_sig = sig["long_signal"].to_numpy()
    short_sig = sig["short_signal"].to_numpy()
    bias = (cot_bias.reindex(idx).ffill().to_numpy()
            if cot_bias is not None else np.zeros(len(idx)))

    n = len(df)
    half_spread = cost.spread / 2.0
    equity = equity0
    trades: list[Trade] = []
    daily_equity = np.full(n, equity0, dtype=float)

    in_pos = False
    tr: Trade | None = None
    entry_i = -1
    hh = ll = np.nan          # extremes since entry, for trailing
    be_done = False

    for i in range(n):
        # ---------- manage an open position on bar i ----------
        if in_pos:
            d = tr.direction
            hh = max(hh, h[i]); ll = min(ll, l[i])
            risk_dist = abs(tr.entry - tr.stop)
            exit_price = None; reason = ""

            # breakeven shift
            if cfg.breakeven_R > 0 and not be_done:
                if d == 1 and h[i] >= tr.entry + cfg.breakeven_R * risk_dist:
                    tr.stop = max(tr.stop, tr.entry); be_done = True
                elif d == -1 and l[i] <= tr.entry - cfg.breakeven_R * risk_dist:
                    tr.stop = min(tr.stop, tr.entry); be_done = True

            # chandelier trail
            if cfg.trail_atr_mult > 0 and not np.isnan(atr[i]):
                if d == 1:
                    tr.stop = max(tr.stop, hh - cfg.trail_atr_mult * atr[i])
                else:
                    tr.stop = min(tr.stop, ll + cfg.trail_atr_mult * atr[i])

            # intrabar exits — stop has priority over target (pessimistic)
            if d == 1:
                if o[i] <= tr.stop:
                    exit_price, reason = o[i], "gap_stop"
                elif l[i] <= tr.stop:
                    exit_price, reason = tr.stop, "stop"
                elif tr.take > 0 and h[i] >= tr.take:
                    exit_price, reason = tr.take, "target"
            else:
                if o[i] >= tr.stop:
                    exit_price, reason = o[i], "gap_stop"
                elif h[i] >= tr.stop:
                    exit_price, reason = tr.stop, "stop"
                elif tr.take > 0 and l[i] <= tr.take:
                    exit_price, reason = tr.take, "target"

            # time stop / regime flip -> exit at close
            if exit_price is None:
                held = i - entry_i
                if cfg.time_stop_bars > 0 and held >= cfg.time_stop_bars:
                    exit_price, reason = c[i], "time"
                elif regime[i] == -d:
                    exit_price, reason = c[i], "flip"

            if exit_price is not None:
                fill = exit_price - half_spread - cost.slippage if d == 1 else exit_price + half_spread + cost.slippage
                pnl = d * (fill - tr.entry) * tr.units - _round_turn_cost(tr.units, cost)
                equity += pnl
                tr.exit_date = idx[i]; tr.exit = fill; tr.reason = reason
                tr.pnl = pnl
                tr.r_multiple = pnl / (tr.init_risk * tr.units) if tr.init_risk > 0 else 0.0
                tr.bars_held = i - entry_i; tr.equity_after = equity
                trades.append(tr)
                in_pos = False; tr = None; be_done = False

        # ---------- look for a new entry from bar i's close, fill at i+1 open ----------
        if not in_pos and i + 1 < n:
            want = 0
            if long_sig[i] and regime[i] == 1:
                want = 1
            elif short_sig[i] and regime[i] == -1:
                want = -1
            if want != 0 and cfg.use_cot and cot_bias is not None:
                b = bias[i]
                if want == 1 and b == -1:
                    want = 0
                elif want == -1 and b == 1:
                    want = 0
                elif b == 0 and not cfg.cot_allow_uncertain:
                    want = 0
            if want != 0 and not np.isnan(atr[i]) and atr[i] > 0:
                raw = o[i + 1]
                entry = raw + half_spread + cost.slippage if want == 1 else raw - half_spread - cost.slippage
                stop = entry - cfg.sl_atr_mult * atr[i] if want == 1 else entry + cfg.sl_atr_mult * atr[i]
                risk_dist = abs(entry - stop)
                if risk_dist > 0:
                    risk_amt = equity * risk_pct
                    units = risk_amt / risk_dist
                    units = min(units, (equity * max_leverage) / max(entry, 1e-9))
                    take = (entry + cfg.tp_R * risk_dist if want == 1 else entry - cfg.tp_R * risk_dist) if cfg.tp_R > 0 else 0.0
                    tr = Trade(symbol, want, idx[i + 1], entry, stop, take, units, init_risk=risk_dist)
                    in_pos = True; entry_i = i + 1; hh = h[i + 1]; ll = l[i + 1]; be_done = False

        daily_equity[i] = equity if not in_pos else equity + (
            tr.direction * (c[i] - tr.entry) * tr.units if tr else 0.0)

    eq = pd.Series(daily_equity, index=idx)
    tdf = pd.DataFrame([t.__dict__ for t in trades])
    return {"symbol": symbol, "trades": tdf, "equity": eq,
            "metrics": compute_metrics(tdf, eq, equity0)}


def compute_metrics(tdf: pd.DataFrame, equity: pd.Series, equity0: float) -> dict:
    if len(tdf) == 0:
        return {"trades": 0, "net": 0.0, "ret_pct": 0.0, "cagr": 0.0, "sharpe": 0.0,
                "max_dd": 0.0, "win_rate": 0.0, "profit_factor": 0.0, "expectancy_R": 0.0,
                "avg_bars": 0.0}
    rets = equity.pct_change().fillna(0.0)
    years = max((equity.index[-1] - equity.index[0]).days / 365.25, 1e-9)
    cagr = (equity.iloc[-1] / equity0) ** (1 / years) - 1 if equity.iloc[-1] > 0 else -1.0
    sharpe = (rets.mean() / rets.std() * np.sqrt(252)) if rets.std() > 0 else 0.0
    roll_max = equity.cummax()
    max_dd = ((equity - roll_max) / roll_max).min()
    wins = tdf[tdf.pnl > 0]; losses = tdf[tdf.pnl <= 0]
    pf = wins.pnl.sum() / abs(losses.pnl.sum()) if abs(losses.pnl.sum()) > 0 else float("inf")
    return {
        "trades": int(len(tdf)),
        "net": float(equity.iloc[-1] - equity0),
        "ret_pct": float((equity.iloc[-1] / equity0 - 1) * 100),
        "cagr": float(cagr * 100),
        "sharpe": float(sharpe),
        "max_dd": float(max_dd * 100),
        "win_rate": float(len(wins) / len(tdf) * 100),
        "profit_factor": float(pf),
        "expectancy_R": float(tdf.r_multiple.mean()),
        "avg_bars": float(tdf.bars_held.mean()),
    }


def default_cost(symbol: str) -> CostModel:
    sp = datamod.typical_spread(symbol)
    pip = datamod.pip(symbol)
    return CostModel(spread=sp, slippage=0.5 * pip, commission=0.2 * pip)
