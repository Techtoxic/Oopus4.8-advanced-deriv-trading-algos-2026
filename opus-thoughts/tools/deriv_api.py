"""Minimal robust sync Deriv WS client (single file, no deps beyond websocket-client)."""
import json, os, time, threading, itertools
import websocket

APP_ID = int(os.environ.get("DERIV_APP_ID", "1089"))
URL = f"wss://ws.binaryws.com/websockets/v3?app_id={APP_ID}"
TOKEN = os.environ.get("DERIV_TOKEN", "")


class DerivWS:
    def __init__(self, url=URL, token=None, timeout=30):
        self.url, self.timeout = url, timeout
        self.token = token if token is not None else TOKEN
        self._req = itertools.count(1)
        self._lock = threading.Lock()
        self._connect()

    def _connect(self):
        self.ws = websocket.create_connection(self.url, timeout=self.timeout)
        if self.token:
            r = self._call({"authorize": self.token})
            self.account = r.get("authorize", {})

    def _call(self, payload):
        rid = next(self._req)
        payload = dict(payload); payload["req_id"] = rid
        with self._lock:
            self.ws.send(json.dumps(payload))
            while True:
                msg = json.loads(self.ws.recv())
                if msg.get("req_id") == rid or msg.get("msg_type") == "error":
                    return msg

    def call(self, payload, retries=3):
        for i in range(retries):
            try:
                r = self._call(payload)
                if "error" in r:
                    code = r["error"].get("code", "")
                    if code == "RateLimit":
                        time.sleep(2.5 * (i + 1)); continue
                return r
            except (websocket.WebSocketException, ConnectionError, OSError, TimeoutError):
                time.sleep(1.5 * (i + 1))
                try: self._connect()
                except Exception: pass
        return self._call(payload)

    # ---- helpers ----
    def active_symbols(self):
        return self.call({"active_symbols": "full", "product_type": "basic"}).get("active_symbols", [])

    def contracts_for(self, symbol):
        return self.call({"contracts_for": symbol, "currency": "USD"})

    def ticks_history(self, symbol, count=5000, end="latest", start=None):
        p = {"ticks_history": symbol, "count": count, "end": end, "style": "ticks"}
        if start: p["adjust_start_time"] = 1; p["start"] = start
        return self.call(p)

    def history_paged(self, symbol, total, sleep=0.35, progress=None):
        """Fetch `total` most-recent ticks by paging backwards. Returns (times, prices, pip)."""
        times, prices, pip = [], [], None
        end = "latest"
        while len(times) < total:
            r = self.ticks_history(symbol, count=min(5000, total - len(times)), end=end)
            if "error" in r:
                raise RuntimeError(f"{symbol}: {r['error']}")
            h = r.get("history", {})
            t, p = h.get("times", []), h.get("prices", [])
            if pip is None: pip = r.get("pip_size") or len(str(p[0]).split(".")[-1])
            if not t: break
            times = t + times; prices = p + prices
            end = t[0] - 1
            if progress: progress(len(times))
            time.sleep(sleep)
        return times, prices, r.get("pip_size")

    def proposal(self, **kw):
        p = {"proposal": 1, "subscribe": 0}
        p.update(kw)
        p.pop("subscribe", None)
        return self.call({"proposal": 1, **kw})

    def buy(self, proposal_id, price):
        return self.call({"buy": proposal_id, "price": price})

    def open_contract(self, contract_id):
        return self.call({"proposal_open_contract": 1, "contract_id": contract_id})

    def close(self):
        try: self.ws.close()
        except Exception: pass


def last_digit(price, pip_size):
    """Exact last digit per Deriv display convention: format to pip_size decimals."""
    s = f"{float(price):.{int(pip_size)}f}"
    return int(s[-1])


if __name__ == "__main__":
    ws = DerivWS()
    r = ws.call({"ping": 1})
    print("ping:", r.get("ping"))
    if ws.token:
        print("account:", ws.account.get("loginid"), "virtual:", ws.account.get("is_virtual"))
    ws.close()
