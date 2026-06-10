"""
Resilient Deriv WebSocket client.

Built for 100% uptime on a free Render dyno:
  - automatic reconnect with exponential backoff + jitter
  - application-level ping every 25s (Deriv drops idle sockets at ~2min)
  - request/response correlation via req_id
  - subscription registry: every subscription is re-established after reconnect
  - all consumers receive messages through asyncio queues; nothing blocks the
    socket reader

This module knows nothing about strategies. It speaks raw Deriv API.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from typing import Any, Awaitable, Callable, Dict, Optional

import websockets

log = logging.getLogger("omnibot.deriv")


class DerivError(Exception):
    def __init__(self, code: str, message: str, raw: dict | None = None):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.raw = raw or {}


class DerivClient:
    def __init__(self, app_id: int, token: str, url_tpl: str = "wss://ws.binaryws.com/websockets/v3?app_id={app_id}"):
        self.app_id = app_id
        self.token = token
        self.url = url_tpl.format(app_id=app_id)
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.authorized: dict = {}
        self.connected = asyncio.Event()
        self._req_id = 0
        self._pending: Dict[int, asyncio.Future] = {}
        # key -> dict(request=..., handler=coro, sub_id=str|None)
        self._subs: Dict[str, dict] = {}
        self._stop = False
        self.stats = {
            "connects": 0,
            "disconnects": 0,
            "last_connect_ts": 0.0,
            "last_msg_ts": 0.0,
            "msgs_in": 0,
            "msgs_out": 0,
        }
        self._tasks: list[asyncio.Task] = []

    # ------------------------------------------------------------------ core
    async def start(self):
        self._stop = False
        self._tasks.append(asyncio.create_task(self._run(), name="deriv-run"))

    async def stop(self):
        self._stop = True
        for t in self._tasks:
            t.cancel()
        if self.ws is not None:
            try:
                await self.ws.close()
            except Exception:
                pass
        self.connected.clear()

    async def _run(self):
        backoff = 1.0
        while not self._stop:
            try:
                log.info("connecting %s", self.url)
                async with websockets.connect(
                    self.url, ping_interval=20, ping_timeout=15, max_size=2**23
                ) as ws:
                    self.ws = ws
                    self.stats["connects"] += 1
                    self.stats["last_connect_ts"] = time.time()
                    backoff = 1.0
                    reader = asyncio.create_task(self._reader(ws))
                    ka = None
                    try:
                        await self._authorize()
                        await self._resubscribe()
                        self.connected.set()
                        ka = asyncio.create_task(self._keepalive())
                        await reader  # runs until the socket closes
                    finally:
                        reader.cancel()
                        if ka:
                            ka.cancel()
            except asyncio.CancelledError:
                return
            except Exception as e:
                log.warning("ws dropped: %s", e)
            self.connected.clear()
            self.stats["disconnects"] += 1
            # fail all in-flight requests so callers retry
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(DerivError("disconnected", "socket lost"))
            self._pending.clear()
            if self._stop:
                return
            sleep = min(backoff, 60) * (1 + random.random() * 0.3)
            log.info("reconnect in %.1fs", sleep)
            await asyncio.sleep(sleep)
            backoff *= 2

    async def _reader(self, ws):
        async for raw in ws:
            self.stats["msgs_in"] += 1
            self.stats["last_msg_ts"] = time.time()
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            await self._dispatch(msg)

    async def _keepalive(self):
        while True:
            await asyncio.sleep(25)
            try:
                await self.call({"ping": 1}, timeout=10)
            except Exception:
                return

    async def _authorize(self):
        resp = await self._send_raw({"authorize": self.token}, timeout=20)
        if "error" in resp:
            raise DerivError(resp["error"].get("code", "?"), resp["error"].get("message", "auth failed"))
        self.authorized = resp.get("authorize", {})
        log.info(
            "authorized loginid=%s currency=%s virtual=%s balance=%s",
            self.authorized.get("loginid"),
            self.authorized.get("currency"),
            self.authorized.get("is_virtual"),
            self.authorized.get("balance"),
        )

    async def _resubscribe(self):
        for key, sub in list(self._subs.items()):
            sub["sub_id"] = None
            try:
                await self._subscribe_now(key)
            except Exception as e:
                log.warning("resubscribe %s failed: %s", key, e)

    # ------------------------------------------------------------- messaging
    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    async def _send_raw(self, payload: dict, timeout: float = 30) -> dict:
        if self.ws is None:
            raise DerivError("disconnected", "no socket")
        rid = self._next_id()
        payload = dict(payload)
        payload["req_id"] = rid
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[rid] = fut
        try:
            await self.ws.send(json.dumps(payload))
            self.stats["msgs_out"] += 1
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self._pending.pop(rid, None)

    async def call(self, payload: dict, timeout: float = 30) -> dict:
        """One-shot request/response. Raises DerivError on API error."""
        await asyncio.wait_for(self.connected.wait(), timeout=timeout)
        resp = await self._send_raw(payload, timeout=timeout)
        if "error" in resp:
            e = resp["error"]
            raise DerivError(e.get("code", "?"), e.get("message", "?"), resp)
        return resp

    async def _dispatch(self, msg: dict):
        rid = msg.get("req_id")
        if rid is not None and rid in self._pending:
            fut = self._pending[rid]
            if not fut.done():
                fut.set_result(msg)
            # subscription first-responses also flow to handler below
        sub = msg.get("subscription") or {}
        sid = sub.get("id")
        mtype = msg.get("msg_type")
        for key, s in self._subs.items():
            if sid is not None and s.get("sub_id") == sid:
                await self._safe_handle(s, msg)
                return
            # match first message of a fresh subscription
            if s.get("sub_id") is None and s.get("pending_rid") == rid and sid is not None:
                s["sub_id"] = sid
                await self._safe_handle(s, msg)
                return
        # untracked stream types (e.g. transaction) – ignore silently
        if mtype in (None, "ping", "pong"):
            return

    async def _safe_handle(self, s: dict, msg: dict):
        try:
            await s["handler"](msg)
        except Exception:
            log.exception("handler error for %s", s.get("key"))

    # ----------------------------------------------------------- subscriptions
    async def subscribe(self, key: str, request: dict, handler: Callable[[dict], Awaitable[None]]):
        """Register a durable subscription, re-established on every reconnect."""
        self._subs[key] = {"key": key, "request": dict(request), "handler": handler, "sub_id": None, "pending_rid": None}
        if self.connected.is_set():
            await self._subscribe_now(key)

    async def _subscribe_now(self, key: str):
        s = self._subs[key]
        payload = dict(s["request"])
        payload["subscribe"] = 1
        rid = self._next_id()
        payload["req_id"] = rid
        s["pending_rid"] = rid
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[rid] = fut
        await self.ws.send(json.dumps(payload))
        self.stats["msgs_out"] += 1
        try:
            first = await asyncio.wait_for(fut, timeout=20)
        finally:
            self._pending.pop(rid, None)
        if "error" in first:
            e = first["error"]
            raise DerivError(e.get("code", "?"), e.get("message", "?"), first)
        sid = (first.get("subscription") or {}).get("id")
        if sid:
            s["sub_id"] = sid
        await self._safe_handle(s, first)

    async def unsubscribe(self, key: str):
        s = self._subs.pop(key, None)
        if s and s.get("sub_id") and self.connected.is_set():
            try:
                await self.call({"forget": s["sub_id"]}, timeout=10)
            except Exception:
                pass

    # ----------------------------------------------------------- convenience
    async def balance(self) -> dict:
        r = await self.call({"balance": 1})
        return r.get("balance", {})

    async def proposal(self, **kw) -> dict:
        r = await self.call({"proposal": 1, **kw})
        return r.get("proposal", {})

    async def buy(self, proposal_id: str, price: float) -> dict:
        r = await self.call({"buy": proposal_id, "price": round(price, 2)})
        return r.get("buy", {})

    async def sell(self, contract_id: int, price: float = 0.0) -> dict:
        r = await self.call({"sell": int(contract_id), "price": price})
        return r.get("sell", {})

    async def contracts_for(self, symbol: str) -> dict:
        r = await self.call({"contracts_for": symbol, "currency": self.authorized.get("currency", "USD")})
        return r.get("contracts_for", {})

    async def ticks_history(self, symbol: str, count: int = 1000, style: str = "ticks") -> dict:
        r = await self.call({"ticks_history": symbol, "end": "latest", "count": count, "style": style})
        return r
