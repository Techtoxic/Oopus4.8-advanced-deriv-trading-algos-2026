"""
Executor — the only component allowed to spend money.

Flow per trade: proposal → buy → subscribe proposal_open_contract → on settle,
report to RiskGuardian + Ledger. Accumulators are sold actively at the
configured take-profit tick count.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from .deriv import DerivClient, DerivError
from .ledger import Ledger
from .risk import RiskGuardian

log = logging.getLogger("omnibot.exec")


class Executor:
    def __init__(self, client: DerivClient, risk: RiskGuardian, ledger: Ledger, paper: bool = False):
        self.client = client
        self.risk = risk
        self.ledger = ledger
        self.paper = paper
        self.last_error: str = ""

    async def place(
        self,
        strategy: str,
        symbol: str,
        contract_type: str,
        stake: float,
        duration: Optional[int] = None,
        duration_unit: str = "t",
        barrier=None,
        growth_rate: Optional[float] = None,
        accum_tp_profit: Optional[float] = None,
        meta: Optional[dict] = None,
    ) -> Optional[dict]:
        ok, why = self.risk.can_trade(strategy, stake)
        if not ok:
            log.info("[%s] blocked: %s", strategy, why)
            return None

        req = {
            "contract_type": contract_type,
            "symbol": symbol,
            "currency": self.client.authorized.get("currency", "USD"),
            "amount": round(stake, 2),
            "basis": "stake",
        }
        if growth_rate is not None:
            req["growth_rate"] = growth_rate
        else:
            req["duration"] = duration
            req["duration_unit"] = duration_unit
        if barrier is not None:
            req["barrier"] = str(barrier)

        if self.paper:
            self.ledger.write(
                "paper", strategy=strategy, symbol=symbol, contract_type=contract_type,
                stake=stake, barrier=barrier, **(meta or {}),
            )
            return {"paper": True}

        try:
            prop = await self.client.proposal(**req)
            buy = await self.client.buy(prop["id"], prop["ask_price"])
        except DerivError as e:
            self.last_error = f"{strategy}/{contract_type}@{symbol}: {e}"
            log.warning("order failed: %s", self.last_error)
            self.ledger.write("reject", strategy=strategy, symbol=symbol,
                              contract_type=contract_type, stake=stake, error=str(e))
            return None

        cid = buy.get("contract_id")
        payout = float(buy.get("payout", 0) or 0)
        price = float(buy.get("buy_price", stake))
        self.risk.on_open(cid, strategy, price, {"symbol": symbol, "contract_type": contract_type})
        self.ledger.write(
            "open", strategy=strategy, symbol=symbol, contract_type=contract_type,
            stake=price, payout=payout, contract_id=cid, barrier=barrier, **(meta or {}),
        )
        log.info("[%s] OPEN %s %s stake=%.2f payout=%.2f cid=%s",
                 strategy, contract_type, symbol, price, payout, cid)
        asyncio.create_task(self._track(cid, strategy, symbol, contract_type, price, accum_tp_profit))
        return buy

    async def _track(self, cid, strategy, symbol, contract_type, stake, accum_tp_profit):
        key = f"poc:{cid}"
        done = asyncio.Event()
        result = {}

        async def handler(msg: dict):
            poc = msg.get("proposal_open_contract") or {}
            if not poc:
                return
            # accumulator active take-profit (profit grows ~(1+g)^ticks_survived)
            if accum_tp_profit and not poc.get("is_sold"):
                cur = float(poc.get("profit") or 0.0)
                if cur >= accum_tp_profit and poc.get("is_valid_to_sell"):
                    try:
                        await self.client.sell(cid)
                    except DerivError as e:
                        log.warning("accum sell failed cid=%s: %s", cid, e)
            if poc.get("is_sold"):
                result.update(poc)
                done.set()

        try:
            await self.client.subscribe(key, {"proposal_open_contract": 1, "contract_id": cid}, handler)
        except DerivError as e:
            log.warning("track subscribe failed cid=%s: %s", cid, e)

        try:
            await asyncio.wait_for(done.wait(), timeout=3600)
        except asyncio.TimeoutError:
            log.warning("contract %s tracking timed out", cid)
        finally:
            await self.client.unsubscribe(key)

        profit = float(result.get("profit", -stake) or 0.0)
        opened_ts = (self.risk.open_contracts.get(cid) or {}).get("opened", time.time())
        self.risk.on_close(cid, profit)
        self.ledger.write(
            "close", strategy=strategy, symbol=symbol, contract_type=contract_type,
            stake=stake, profit=profit, contract_id=cid,
            exit_spot=result.get("exit_tick_display_value"),
            status=result.get("status"),
            duration_s=round(time.time() - opened_ts, 1),
        )
        log.info("[%s] CLOSE %s %s profit=%+.2f (pnl today %.2f)",
                 strategy, contract_type, symbol, profit, self.risk.realized_pnl_today)
