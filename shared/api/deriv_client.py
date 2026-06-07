"""
Deriv API client (synchronous) for the Edge Research Agent.

Thin, robust wrapper over the Deriv WebSocket v3 API using req_id correlation.
Secrets are read from the environment (DERIV_TOKEN); never hard-coded.
"""
import json
import os
import time
import itertools
import websocket  # websocket-client


class DerivClient:
    def __init__(self, token=None, app_id=1089, url=None, timeout=60):
        self.token = token or os.environ["DERIV_TOKEN"]
        self.app_id = app_id
        self.url = url or f"wss://ws.derivws.com/websockets/v3?app_id={app_id}"
        self.timeout = timeout
        self._ids = itertools.count(1)
        self.ws = None
        self.authorized = None

    # ---- connection ----
    def connect(self):
        self.ws = websocket.create_connection(self.url, timeout=self.timeout)
        return self.authorize()

    def authorize(self):
        res = self.request({"authorize": self.token})
        self.authorized = res.get("authorize")
        return self.authorized

    def close(self):
        try:
            if self.ws:
                self.ws.close()
        except Exception:
            pass

    def reconnect(self):
        self.close()
        self.connect()

    # ---- core request/response with req_id correlation ----
    def request(self, payload, timeout=None):
        rid = next(self._ids)
        payload = dict(payload)
        payload["req_id"] = rid
        self.ws.send(json.dumps(payload))
        deadline = time.time() + (timeout or self.timeout)
        while time.time() < deadline:
            raw = self.ws.recv()
            if not raw:
                continue
            msg = json.loads(raw)
            if msg.get("req_id") == rid:
                return msg
            # ignore stray subscription messages with other req_ids
        raise TimeoutError(f"No response for req_id={rid} payload={list(payload)}")

    # ---- convenience calls ----
    def balance(self):
        return self.request({"balance": 1})["balance"]

    def ticks_history(self, symbol, count=5000, end="latest", style="ticks"):
        """Return up to `count` historical ticks (instant)."""
        res = self.request({
            "ticks_history": symbol,
            "count": count,
            "end": end,
            "style": style,
        })
        if "error" in res:
            raise RuntimeError(f"ticks_history {symbol}: {res['error']['message']}")
        h = res["history"]
        return list(zip(h["times"], h["prices"]))  # list of (epoch, price)

    def proposal(self, **kwargs):
        req = {"proposal": 1}
        req.update(kwargs)
        return self.request(req)

    def buy(self, proposal_id, price):
        return self.request({"buy": proposal_id, "price": price})

    def buy_contract(self, parameters, price):
        """buy using parameters dict (price = max acceptable stake)."""
        return self.request({"buy": 1, "price": price, "parameters": parameters})

    def proposal_open_contract(self, contract_id, timeout=120):
        """Subscribe once and wait for full settlement (is_sold). Rate-friendly:
        one subscribe request instead of repeated polling."""
        rid = next(self._ids)
        self.ws.send(json.dumps({
            "proposal_open_contract": 1,
            "contract_id": contract_id,
            "subscribe": 1,
            "req_id": rid,
        }))
        deadline = time.time() + timeout
        last = None
        sub_id = None
        while time.time() < deadline:
            raw = self.ws.recv()
            if not raw:
                continue
            msg = json.loads(raw)
            if msg.get("req_id") != rid:
                continue
            if "error" in msg:
                raise RuntimeError(msg["error"]["message"])
            poc = msg.get("proposal_open_contract")
            if not poc:
                continue
            sub_id = msg.get("subscription", {}).get("id", sub_id)
            last = poc
            if poc.get("is_sold"):
                if sub_id:
                    try:
                        self.ws.send(json.dumps({"forget": sub_id}))
                    except Exception:
                        pass
                return poc
        return last
