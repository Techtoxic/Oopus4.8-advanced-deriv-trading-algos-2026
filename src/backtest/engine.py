"""
Event-driven backtester for Deriv binary contracts.

Realism features:
  * Correct settlement lag. A contract bought at decision tick i has
    entry spot = i+1 and exit spot = i+1+duration (Deriv processes on the
    next tick; a "1-tick" option resolves one tick after entry).
  * Real payouts. Win pays stake*payout (net profit stake*(payout-1));
    loss returns 0 (you lose the stake). Payouts come from live proposal
    quotes captured in data/payouts.json (fallback table below).
  * Pluggable stake sizing (flat / martingale / anti-martingale / Kelly).
  * Tracks equity curve, drawdown, ruin, and a full trade ledger.

This engine is deliberately *generous* to the trader: zero spread beyond
the quoted payout, instant fills, no slippage, no commission. If a strategy
loses here, it loses worse live.
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import Callable, Optional
import numpy as np

# Fallback payouts (per $1 stake) if a live quote is unavailable.
PAYOUTS = {
    "DIGITDIFF": 1.09, "DIGITMATCH": 8.93,
    "DIGITEVEN": 1.95, "DIGITODD": 1.95,
    "DIGITOVER": {0:1.09,1:1.23,2:1.40,3:1.63,4:1.95,5:2.43,6:3.21,7:4.72,8:8.93},
    "DIGITUNDER": {1:8.93,2:4.72,3:3.21,4:2.43,5:1.95,6:1.63,7:1.40,8:1.23,9:1.09},
    "CALL": 1.92, "PUT": 1.92,
}

def payout_for(contract: str, barrier=None) -> float:
    p = PAYOUTS[contract]
    if isinstance(p, dict):
        return p[int(barrier)]
    return p


@dataclass
class Order:
    contract: str            # DIGITDIFF, DIGITOVER, CALL, PUT, ...
    barrier: Optional[int] = None   # for digit contracts
    duration: int = 1        # ticks


@dataclass
class Result:
    name: str
    n_trades: int
    wins: int
    losses: int
    win_rate: float
    total_staked: float
    net_pnl: float
    roi: float
    start_bankroll: float
    end_bankroll: float
    max_drawdown: float
    ruined: bool
    ev_per_trade: float
    equity: list = field(default_factory=list)

    def line(self) -> str:
        r = "RUINED" if self.ruined else "survived"
        return (f"{self.name:34s} | trades {self.n_trades:6d} | win {self.win_rate*100:5.2f}% | "
                f"staked ${self.total_staked:9.2f} | PnL ${self.net_pnl:+8.2f} | "
                f"ROI {self.roi*100:+6.2f}% | EV/trade ${self.ev_per_trade:+.4f} | "
                f"maxDD ${self.max_drawdown:7.2f} | end ${self.end_bankroll:8.2f} | {r}")


def settle(contract: str, barrier, entry_price, exit_price, entry_digit, exit_digit) -> bool:
    """Return True if the contract WINS."""
    if contract == "DIGITDIFF":
        return exit_digit != barrier
    if contract == "DIGITMATCH":
        return exit_digit == barrier
    if contract == "DIGITEVEN":
        return exit_digit % 2 == 0
    if contract == "DIGITODD":
        return exit_digit % 2 == 1
    if contract == "DIGITOVER":
        return exit_digit > barrier
    if contract == "DIGITUNDER":
        return exit_digit < barrier
    if contract == "CALL":
        return exit_price > entry_price
    if contract == "PUT":
        return exit_price < entry_price
    raise ValueError(contract)


def backtest(
    name: str,
    prices: np.ndarray,
    digits: np.ndarray,
    strategy: Callable[[int, np.ndarray, np.ndarray], Optional[Order]],
    stake_fn: Callable[[dict], float],
    bankroll: float = 10.0,
    min_stake: float = 0.35,
    payouts: Optional[dict] = None,
    stop_at_ruin: bool = True,
    cooldown: int = 0,
) -> Result:
    """
    strategy(i, prices, digits) -> Order or None   (decision uses info up to i inclusive)
    stake_fn(state) -> float stake in $              (state has loss streak, bankroll, etc.)
    """
    def get_payout(contract, barrier):
        if payouts:
            key = (contract, barrier)
            if key in payouts:
                return payouts[key]
        return payout_for(contract, barrier)

    N = len(prices)
    start = bankroll
    equity = [bankroll]
    peak = bankroll
    max_dd = 0.0
    n = wins = losses = 0
    total_staked = 0.0
    loss_streak = 0
    win_streak = 0
    last_trade_i = -10**9
    i = 1
    while i < N - 3:
        if i - last_trade_i <= cooldown:
            i += 1
            continue
        order = strategy(i, prices, digits)
        if order is None:
            i += 1
            continue
        state = {"bankroll": bankroll, "loss_streak": loss_streak, "win_streak": win_streak,
                 "start": start, "min_stake": min_stake}
        stake = stake_fn(state)
        stake = max(min_stake, stake)
        if stake > bankroll:   # cannot afford -> this is ruin for a martingale
            if stop_at_ruin:
                break
            else:
                stake = bankroll
        # settlement indices
        entry_i = i + 1
        exit_i = i + 1 + order.duration
        if exit_i >= N:
            break
        p_payout = get_payout(order.contract, order.barrier)
        won = settle(order.contract, order.barrier, prices[entry_i], prices[exit_i],
                     digits[entry_i], digits[exit_i])
        total_staked += stake
        n += 1
        if won:
            bankroll += stake * (p_payout - 1.0)
            wins += 1
            loss_streak = 0
            win_streak += 1
        else:
            bankroll -= stake
            losses += 1
            loss_streak += 1
            win_streak = 0
        equity.append(bankroll)
        peak = max(peak, bankroll)
        max_dd = max(max_dd, peak - bankroll)
        last_trade_i = i
        if bankroll < min_stake and stop_at_ruin:
            break
        i = exit_i  # next decision after this contract resolves (non-overlapping)
    ruined = bankroll < min_stake
    net = bankroll - start
    return Result(
        name=name, n_trades=n, wins=wins, losses=losses,
        win_rate=(wins / n if n else 0.0),
        total_staked=total_staked, net_pnl=net,
        roi=(net / total_staked if total_staked else 0.0),
        start_bankroll=start, end_bankroll=bankroll, max_drawdown=max_dd,
        ruined=ruined, ev_per_trade=(net / n if n else 0.0), equity=equity,
    )
