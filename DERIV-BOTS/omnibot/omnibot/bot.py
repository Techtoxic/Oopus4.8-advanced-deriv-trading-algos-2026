"""Bot orchestrator: wires client, edge monitor, risk, executor, strategies."""
from __future__ import annotations

import asyncio
import logging
import time

from .config import CFG, Config
from .deriv import DerivClient
from .edge import EdgeMonitor
from .executor import Executor
from .ledger import Ledger
from .risk import RiskGuardian
from .strategies import Accumulators, DigitsFlow, RiseFall, Sentinel

log = logging.getLogger("omnibot.bot")


class OmniBot:
    def __init__(self, cfg: Config = CFG):
        self.cfg = cfg
        self.started_ts = time.time()
        self.client = DerivClient(cfg.app_id, cfg.token, cfg.ws_url)
        self.edge = EdgeMonitor(cfg.sentinel_window, cfg.sentinel_min_ticks, cfg.sentinel_z)
        self.risk = RiskGuardian(
            daily_loss_limit=cfg.daily_loss_limit,
            daily_profit_lock=cfg.daily_profit_lock,
            max_stake=cfg.max_stake,
            max_concurrent=cfg.max_concurrent,
            loss_streak_pause=cfg.loss_streak_pause,
            pause_minutes=cfg.pause_minutes,
        )
        self.ledger = Ledger(cfg.ledger_path)
        self.executor = Executor(self.client, self.risk, self.ledger, paper=(cfg.mode == "PAPER"))
        self.strategies: dict = {}
        self.balance: dict = {}
        self.fatal: str = ""
        self.paused = False
        self._tasks: list[asyncio.Task] = []
        self.pip_sizes: dict[str, float] = {}

    # ------------------------------------------------------------------ start
    async def start(self):
        if not self.cfg.token:
            self.fatal = "DERIV_TOKEN env var not set"
            log.error(self.fatal)
            return
        await self.client.start()
        try:
            await asyncio.wait_for(self.client.connected.wait(), timeout=40)
        except asyncio.TimeoutError:
            self.fatal = "could not connect/authorize within 40s (will keep retrying)"
            log.error(self.fatal)

        if self.client.authorized:
            if not self.client.authorized.get("is_virtual") and not self.cfg.allow_real_account:
                self.fatal = ("SAFETY HALT: token belongs to a REAL account and ALLOW_REAL is not set. "
                              "This bot only runs on demo unless you explicitly opt in.")
                log.error(self.fatal)
                await self.client.stop()
                return

        symbols = sorted(set(self.cfg.digit_symbols) | set(self.cfg.accum_symbols) | set(self.cfg.rf_symbols))
        for sym in symbols:
            await self._subscribe_ticks(sym)

        async def on_balance(msg: dict):
            b = msg.get("balance") or {}
            if b:
                self.balance = {"balance": b.get("balance"), "currency": b.get("currency"),
                                "loginid": b.get("loginid")}

        try:
            await self.client.subscribe("balance", {"balance": 1}, on_balance)
        except Exception as e:
            log.warning("balance subscribe failed: %s", e)

        mode = self.cfg.mode.upper()
        strats: list = []
        if self.cfg.enable_sentinel:
            strats.append(Sentinel(self.cfg, self.client, self.executor, self.edge))
        if mode in ("ACTIVE", "PAPER"):
            if self.cfg.enable_digits:
                strats.append(DigitsFlow(self.cfg, self.client, self.executor, self.edge))
            if self.cfg.enable_accum:
                strats.append(Accumulators(self.cfg, self.client, self.executor, self.edge))
            if self.cfg.enable_risefall:
                strats.append(RiseFall(self.cfg, self.client, self.executor, self.edge))
        for s in strats:
            self.strategies[s.name] = s
            self._tasks.append(asyncio.create_task(self._guard(s), name=f"strat-{s.name}"))
        log.info("omnibot started mode=%s strategies=%s symbols=%s",
                 mode, list(self.strategies), symbols)

    async def _guard(self, strat):
        while True:
            try:
                await strat.run()
            except asyncio.CancelledError:
                return
            except Exception:
                log.exception("strategy %s crashed; restarting in 10s", strat.name)
                await asyncio.sleep(10)

    async def _subscribe_ticks(self, symbol: str):
        async def on_tick(msg: dict):
            t = msg.get("tick") or {}
            if not t:
                return
            quote = float(t.get("quote", 0) or 0)
            pip = float(t.get("pip_size", 0) or 0)
            if pip == 0:
                pip = self.pip_sizes.get(symbol, 0.01)
            else:
                # pip_size arrives as number of decimals on some payloads
                if pip >= 1:
                    pip = 10 ** (-int(pip))
                self.pip_sizes[symbol] = pip
            if quote > 0:
                self.edge.on_tick(symbol, quote, pip)

        try:
            await self.client.subscribe(f"ticks:{symbol}", {"ticks": symbol}, on_tick)
            log.info("subscribed ticks %s", symbol)
        except Exception as e:
            log.warning("tick subscribe %s failed: %s", symbol, e)

    async def stop(self):
        for t in self._tasks:
            t.cancel()
        await self.client.stop()

    # ----------------------------------------------------------------- control
    def set_paused(self, paused: bool):
        self.paused = paused
        for s in self.strategies.values():
            s.enabled = not paused

    # ------------------------------------------------------------------ status
    def status(self) -> dict:
        auth = self.client.authorized or {}
        return {
            "service": "fable-omnibot",
            "mode": self.cfg.mode,
            "paused": self.paused,
            "fatal": self.fatal or None,
            "uptime_s": int(time.time() - self.started_ts),
            "ws": {
                "connected": self.client.connected.is_set(),
                **self.client.stats,
            },
            "account": {
                "loginid": auth.get("loginid"),
                "is_virtual": auth.get("is_virtual"),
                "currency": auth.get("currency"),
                "balance": self.balance.get("balance", auth.get("balance")),
            },
            "risk": self.risk.snapshot(),
            "strategies": {k: v.status() for k, v in self.strategies.items()},
            "last_order_error": self.executor.last_error or None,
        }
