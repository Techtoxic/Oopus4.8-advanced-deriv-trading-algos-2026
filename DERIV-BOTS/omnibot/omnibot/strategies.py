"""
Strategies. Each is an asyncio loop that proposes trades to the Executor.

Honest framing (see FINDINGS.md at repo root — twice independently verified):
every synthetic-index contract is negative-EV by construction. The only
strategy here with a path to +EV is `sentinel`, which fires exclusively when
the live tick distribution deviates from fair by more than the live payout's
break-even. The flow strategies exist because this bot's job is to WATCH the
algo and stay execution-ready on a demo account, with hard risk caps.
"""
from __future__ import annotations

import asyncio
import logging
import math
import random
import time

from .config import Config
from .deriv import DerivClient, DerivError
from .edge import EdgeMonitor
from .executor import Executor

log = logging.getLogger("omnibot.strat")


class BaseStrategy:
    name = "base"

    def __init__(self, cfg: Config, client: DerivClient, executor: Executor, edge: EdgeMonitor):
        self.cfg = cfg
        self.client = client
        self.executor = executor
        self.edge = edge
        self.enabled = True
        self.state: dict = {}

    async def run(self):
        raise NotImplementedError

    def status(self) -> dict:
        return {"enabled": self.enabled, **self.state}


# --------------------------------------------------------------------------- #
class DigitsFlow(BaseStrategy):
    """Paced flat-stake digit trades on the lowest-house-edge contract.

    Negative EV by design (≈1.6–2% per trade on OVER 1). Purpose: keep the
    executor hot, generate live calibration data for the edge monitor, and
    show real win-rate-vs-payout arithmetic on the dashboard.
    """

    name = "digits_flow"

    async def run(self):
        i = 0
        while True:
            await asyncio.sleep(self.cfg.digits_trade_interval_s * (0.8 + 0.4 * random.random()))
            if not self.enabled:
                continue
            sym = self.cfg.digit_symbols[i % len(self.cfg.digit_symbols)]
            i += 1
            st = self.edge.stats_for(sym)
            self.state = {"last_symbol": sym, "window_n": st.n()}
            await self.executor.place(
                self.name, sym, self.cfg.digits_contract,
                stake=self.cfg.base_stake, duration=1, duration_unit="t",
                barrier=self.cfg.digits_barrier,
                meta={"note": "flow trade, lowest-edge contract"},
            )


# --------------------------------------------------------------------------- #
class Sentinel(BaseStrategy):
    """THE watcher. Scans the rolling battery against LIVE payouts and fires
    only when the Wilson-99.9% lower bound of a contract's win probability
    clears its live break-even. On a fair RNG this never triggers — and that
    is the correct behavior. If Deriv's generator ever drifts, this collects
    within minutes.
    """

    name = "sentinel"
    PAYOUT_REFRESH_S = 600

    async def run(self):
        asyncio.create_task(self._payout_refresher())
        while True:
            await asyncio.sleep(20)
            if not self.enabled:
                continue
            for sym in self.cfg.digit_symbols:
                opps = self.edge.scan(sym)
                st = self.edge.stats_for(sym)
                self.state[sym] = {
                    "window_n": st.n(),
                    "chi2_p": round(st.chi2_p(), 4),
                    "opportunities": len(opps),
                }
                for o in opps[:1]:  # fire at most one per scan per symbol
                    log.warning("SENTINEL EDGE DETECTED %s %s b=%s p_lo=%.4f payout=%.4f edge=%.2f%% n=%d",
                                o.symbol, o.contract_type, o.barrier, o.p_lower, o.payout, o.edge_pct, o.n)
                    # quarter-Kelly stake, capped
                    b = o.payout - 1.0
                    kelly = max(0.0, (o.p_lower * (b + 1) - 1) / b) if b > 0 else 0.0
                    bal = float(self.client.authorized.get("balance", 0) or 0)
                    stake = min(self.cfg.max_stake, max(self.cfg.base_stake, 0.25 * kelly * bal))
                    await self.executor.place(
                        self.name, o.symbol, o.contract_type, stake=round(stake, 2),
                        duration=1, duration_unit="t", barrier=o.barrier,
                        meta={"p_lower": o.p_lower, "payout": o.payout, "edge_pct": o.edge_pct, "n": o.n},
                    )

    async def _payout_refresher(self):
        """Keep live payout multipliers fresh for the contracts the scanner prices."""
        probe = [("DIGITEVEN", None), ("DIGITODD", None),
                 ("DIGITOVER", 0), ("DIGITOVER", 1), ("DIGITOVER", 3),
                 ("DIGITUNDER", 6), ("DIGITUNDER", 8), ("DIGITUNDER", 9),
                 ("DIGITDIFF", 0), ("DIGITDIFF", 5), ("DIGITMATCH", 5)]
        while True:
            for sym in self.cfg.digit_symbols:
                for ctype, barrier in probe:
                    try:
                        req = dict(proposal=1, contract_type=ctype, symbol=sym,
                                   currency=self.client.authorized.get("currency", "USD"),
                                   amount=1.0, basis="stake", duration=1, duration_unit="t")
                        if barrier is not None:
                            req["barrier"] = str(barrier)
                        r = await self.client.call(req)
                        p = r.get("proposal", {})
                        ask = float(p.get("ask_price", 1) or 1)
                        pay = float(p.get("payout", 0) or 0)
                        if ask > 0 and pay > 0:
                            self.edge.set_payout(sym, ctype, barrier, pay / ask)
                    except DerivError:
                        pass
                    await asyncio.sleep(1.2)  # stay way under rate limits
            await asyncio.sleep(self.PAYOUT_REFRESH_S)


# --------------------------------------------------------------------------- #
class Accumulators(BaseStrategy):
    """Accumulator with a fixed survival take-profit policy.

    P(survive k ticks) ≈ (1-p_knock)^k; payout grows ≈ (1+g)^k. Deriv sets the
    knock-out band so growth < survival cost ⇒ negative EV at every k. The TP
    tick count shapes the outcome distribution (many small wins / rare resets),
    it cannot flip the sign. Defaults: g=3%, TP at 18 ticks ≈ +70% on a win.
    """

    name = "accumulators"

    async def run(self):
        i = 0
        while True:
            await asyncio.sleep(self.cfg.accum_interval_s * (0.8 + 0.4 * random.random()))
            if not self.enabled:
                continue
            stt = self.executor.risk.state_for(self.name)
            if stt.trades_today >= self.cfg.accum_max_per_day:
                self.state["note"] = "daily accum cap reached"
                continue
            sym = self.cfg.accum_symbols[i % len(self.cfg.accum_symbols)]
            i += 1
            tp_mult = (1 + self.cfg.accum_growth_rate) ** self.cfg.accum_take_profit_ticks - 1
            self.state = {"last_symbol": sym, "tp_ticks": self.cfg.accum_take_profit_ticks,
                          "tp_profit_mult": round(tp_mult, 3)}
            await self.executor.place(
                self.name, sym, "ACCU", stake=self.cfg.accum_stake,
                growth_rate=self.cfg.accum_growth_rate,
                accum_tp_profit=self.cfg.accum_stake * tp_mult,
                meta={"growth": self.cfg.accum_growth_rate, "tp_profit": round(self.cfg.accum_stake * tp_mult, 2)},
            )


# --------------------------------------------------------------------------- #
class RiseFall(BaseStrategy):
    """Rise/Fall with a live regime detector.

    Measures lag-1 autocorrelation of tick returns in a rolling window. If
    |z| ≥ 2.5 it trades the implied direction (momentum if +, fade if −);
    otherwise it alternates a flat-stake heartbeat trade. On synthetics the
    detector should stay silent (fair walk) — that silence showing up live on
    the dashboard is the point.
    """

    name = "rise_fall"

    def _regime(self, sym: str) -> tuple[str, float]:
        st = self.edge.stats_for(sym)
        q = list(st.quotes)[-self.cfg.rf_regime_window:]
        if len(q) < 100:
            return "warmup", 0.0
        rets = [(q[i + 1] - q[i]) for i in range(len(q) - 1)]
        n = len(rets)
        mean = sum(rets) / n
        num = sum((rets[i] - mean) * (rets[i + 1] - mean) for i in range(n - 1))
        den = sum((r - mean) ** 2 for r in rets)
        if den <= 0:
            return "flat", 0.0
        ac1 = num / den
        z = ac1 * math.sqrt(n)
        if z >= 2.5:
            return "momentum", z
        if z <= -2.5:
            return "meanrev", z
        return "fair", z

    async def run(self):
        i = 0
        flip = True
        while True:
            await asyncio.sleep(self.cfg.rf_interval_s * (0.8 + 0.4 * random.random()))
            if not self.enabled:
                continue
            sym = self.cfg.rf_symbols[i % len(self.cfg.rf_symbols)]
            i += 1
            regime, z = self._regime(sym)
            st = self.edge.stats_for(sym)
            q = list(st.quotes)
            self.state = {"last_symbol": sym, "regime": regime, "ac1_z": round(z, 2)}
            if regime == "warmup":
                continue
            if regime == "momentum" and len(q) >= 2:
                ctype = "CALL" if q[-1] >= q[-2] else "PUT"
            elif regime == "meanrev" and len(q) >= 2:
                ctype = "PUT" if q[-1] >= q[-2] else "CALL"
            else:
                ctype = "CALL" if flip else "PUT"
                flip = not flip
            await self.executor.place(
                self.name, sym, ctype, stake=self.cfg.rf_stake,
                duration=self.cfg.rf_duration_ticks, duration_unit="t",
                meta={"regime": regime, "z": round(z, 2)},
            )
