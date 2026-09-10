"""Minimal robust sync Deriv WS client — migrated to new Deriv API (api.derivws.com)."""
import json, os, time, threading, itertools
import urllib.request, urllib.error
import websocket

# ── Credentials — env first, fallback to pasted values ──────────────────────
TOKEN  = os.environ.get("DERIV_TOKEN",  "pat_23c350f49ef832db6e7b117d69417416d804a610875e8899f6dc4936fa884f34")
APP_ID = os.environ.get("DERIV_APP_ID", "33wYNr1doMQUdym9qvsMk")

# ── New API endpoints ────────────────────────────────────────────────────────
REST_BASE = "https://api.derivws.com"
WS_PUBLIC = "wss://api.derivws.com/trading/v1/options/ws/public"  # no auth, market data
URL = WS_PUBLIC  # exported for sigma_sentinel tick stream


class DerivWS:
    def __init__(self, token=None, app_id=None, timeout=30):
        self.timeout = timeout
        self.token   = token  if token  is not None else TOKEN
        self.app_id  = app_id if app_id is not None else APP_ID
        self._req    = itertools.count(1)
        self._lock   = threading.Lock()
        self.account = {}
        self._connect()

    # ── REST helper (no extra deps — uses built-in urllib) ───────────────────
    def _rest(self, method, path, body=None):
        url  = REST_BASE + path
        data = json.dumps(body).encode() if body else None
        req  = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Deriv-App-ID",  self.app_id)
        req.add_header("Content-Type",  "application/json")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read())

    # ── Connect: REST → OTP → WebSocket ─────────────────────────────────────
    def _connect(self):
        if self.token:
            # Step 1: get all accounts to find our account ID
            accounts_resp = self._rest("GET", "/trading/v1/options/accounts")
            acct_list = accounts_resp.get("data", [])
            if not acct_list:
                raise RuntimeError("No accounts found for this token")
            # Prefer demo account; fall back to first available
            acct = next((a for a in acct_list if a.get("account_type") == "real"), acct_list[0])
            self.account = acct
            account_id = acct["account_id"]   # new API uses account_id, not loginid
            # Step 2: get OTP → the response contains the ready-to-use WS URL
            otp_resp = self._rest("POST", f"/trading/v1/options/accounts/{account_id}/otp")
            ws_url = otp_resp["data"]["url"]
        else:
            # No token → public endpoint (proposals and market data, no trading)
            ws_url = WS_PUBLIC

        self.ws = websocket.create_connection(ws_url, timeout=self.timeout)

    # ── Core send/receive ────────────────────────────────────────────────────
    def _call(self, payload):
        rid = next(self._req)
        payload = dict(payload)
        payload["req_id"] = rid
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

    # ── Helpers ──────────────────────────────────────────────────────────────
    def active_symbols(self):
        return self.call({"active_symbols": "full", "product_type": "basic"}).get("active_symbols", [])

    def contracts_for(self, symbol):
        # new API rejects the legacy currency property
        return self.call({"contracts_for": symbol})

    def ticks_history(self, symbol, count=5000, end="latest", start=None):
        p = {"ticks_history": symbol, "count": count, "end": end, "style": "ticks"}
        if start: p["adjust_start_time"] = 1; p["start"] = start
        return self.call(p)

    def history_paged(self, symbol, total, sleep=0.35, progress=None):
        """Fetch `total` most-recent ticks by paging backwards. Returns (times, prices, pip).

        FIXED 2026-08-13: always send an explicit `start` and dedup on epoch. Without
        `start`, style=ticks defaults the window to 1 day ago; once `end` pages earlier
        than that the server returns the LATEST page again and the loop silently
        duplicates data (the tick_integrity.py bug — 87k unique of 1.6M fetched).
        Same logic as clean_fetch.fetch_backward, now at the root so every tool gets it."""
        seen, pip = {}, None
        end, page, stall = "latest", 5000, 0
        while len(seen) < total:
            req = {"ticks_history": symbol, "count": min(page, total - len(seen)),
                   "end": end, "style": "ticks"}
            if isinstance(end, int):
                req["start"] = int(end - page * 2 - 60)
            r = self.call(req)
            if "error" in r:
                raise RuntimeError(f"{symbol}: {r['error']}")
            h = r.get("history", {})
            t, p = h.get("times", []), h.get("prices", [])
            if not t: break
            if pip is None: pip = r.get("pip_size") or len(str(p[0]).split(".")[-1])
            before = len(seen)
            for t_, p_ in zip(t, p):
                seen[int(t_)] = float(p_)
            if len(seen) == before:
                stall += 1
                if stall >= 3: break
            else:
                stall = 0
            end = min(int(x) for x in t) - 1
            if progress: progress(len(seen))
            time.sleep(sleep)
        ks = sorted(seen)
        return ks, [seen[k] for k in ks], pip

    def proposal(self, **kw):
        kw.pop("subscribe", None)
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
    print("account:", ws.account.get("account_id"), "type:", ws.account.get("account_type"),
          "balance:", ws.account.get("balance"))
    ws.close()
