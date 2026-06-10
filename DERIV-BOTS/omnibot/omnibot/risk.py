"""
Risk guardian — the part that keeps a negative-EV playground from eating the
account. Hard daily loss stop, profit lock, stake caps, concurrency caps,
loss-streak cooldowns. All state is per-UTC-day.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone


def utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


@dataclass
class StratState:
    losses_in_row: int = 0
    paused_until: float = 0.0
    trades_today: int = 0


@dataclass
class RiskGuardian:
    daily_loss_limit: float
    daily_profit_lock: float
    max_stake: float
    max_concurrent: int
    loss_streak_pause: int
    pause_minutes: float

    day: str = field(default_factory=utc_day)
    realized_pnl_today: float = 0.0
    open_contracts: dict = field(default_factory=dict)   # contract_id -> info
    strat: dict = field(default_factory=dict)            # name -> StratState
    halted_reason: str = ""

    def _roll_day(self):
        d = utc_day()
        if d != self.day:
            self.day = d
            self.realized_pnl_today = 0.0
            self.halted_reason = ""
            for s in self.strat.values():
                s.losses_in_row = 0
                s.trades_today = 0
                s.paused_until = 0.0

    def state_for(self, name: str) -> StratState:
        if name not in self.strat:
            self.strat[name] = StratState()
        return self.strat[name]

    # ------------------------------------------------------------------ gates
    def can_trade(self, strategy: str, stake: float) -> tuple[bool, str]:
        self._roll_day()
        if self.halted_reason:
            return False, self.halted_reason
        if self.realized_pnl_today <= -abs(self.daily_loss_limit):
            self.halted_reason = f"daily loss limit hit ({self.realized_pnl_today:.2f})"
            return False, self.halted_reason
        if self.daily_profit_lock > 0 and self.realized_pnl_today >= self.daily_profit_lock:
            self.halted_reason = f"daily profit lock hit (+{self.realized_pnl_today:.2f})"
            return False, self.halted_reason
        if stake > self.max_stake:
            return False, f"stake {stake} > max {self.max_stake}"
        if len(self.open_contracts) >= self.max_concurrent:
            return False, "max concurrent contracts open"
        st = self.state_for(strategy)
        now = time.time()
        if now < st.paused_until:
            return False, f"{strategy} cooling down {int(st.paused_until - now)}s"
        return True, "ok"

    # ----------------------------------------------------------------- events
    def on_open(self, contract_id, strategy: str, stake: float, meta: dict):
        self._roll_day()
        self.open_contracts[contract_id] = {
            "strategy": strategy, "stake": stake, "opened": time.time(), **meta,
        }
        self.state_for(strategy).trades_today += 1

    def on_close(self, contract_id, profit: float):
        info = self.open_contracts.pop(contract_id, None)
        self.realized_pnl_today += profit
        if info:
            st = self.state_for(info["strategy"])
            if profit < 0:
                st.losses_in_row += 1
                if self.loss_streak_pause > 0 and st.losses_in_row >= self.loss_streak_pause:
                    st.paused_until = time.time() + self.pause_minutes * 60
                    st.losses_in_row = 0
            else:
                st.losses_in_row = 0
        return info

    # ------------------------------------------------------------------ views
    def snapshot(self) -> dict:
        self._roll_day()
        return {
            "day": self.day,
            "realized_pnl_today": round(self.realized_pnl_today, 2),
            "daily_loss_limit": self.daily_loss_limit,
            "daily_profit_lock": self.daily_profit_lock,
            "open_contracts": len(self.open_contracts),
            "halted": self.halted_reason or None,
            "strategies": {
                k: {
                    "losses_in_row": v.losses_in_row,
                    "trades_today": v.trades_today,
                    "paused_for_s": max(0, int(v.paused_until - time.time())),
                }
                for k, v in self.strat.items()
            },
        }
