#!/usr/bin/env python3
"""
EDGE SENTINEL — the only honest +EV bot for Deriv synthetics.

Premise (verified 2026-06-09 on 150k fresh ticks, see FINDINGS_FABLE5.md):
the synthetic RNG is currently indistinguishable from IID-uniform and every
contract is priced below fair. A profit bot that trades unconditionally is a
guaranteed loser. The ONLY +EV strategy is conditional: monitor the generator
continuously, and trade ONLY when a statistical deviation appears that is
(a) significant after multiple-testing correction, and (b) larger than the
live measured house edge. This bot implements exactly that, with the exploit
engine armed and Kelly-sized — it simply refuses to fire on a fair coin.

It is adaptive by construction: it re-estimates everything from live data and
re-fetches live payouts, so it remains correct 6 months or 6 years from now.

Usage:
  python3 edge_sentinel.py --symbols 1HZ100V,R_100 --window 20000 [--token XXX --live]

Without --live it paper-trades (logs the trade it WOULD place).
"""
import asyncio, argparse, json, math, time, sys
from collections import deque
import numpy as np
from scipy import stats
import websockets

APP_ID = 1089
URL = f"wss://ws.binaryws.com/websockets/v3?app_id={APP_ID}"

# Statistical policy
ALPHA = 0.001            # per-battery family-wise significance
MIN_WINDOW = 5000        # don't test before this many ticks
RETEST_EVERY = 500       # run battery every N new ticks
KELLY_FRACTION = 0.25    # fraction of full Kelly if an edge ever appears
MAX_STAKE_PCT = 0.02     # hard cap: 2% of balance per trade


def last_digit(price: float, pip: int) -> int:
    return int(round(price * 10 ** pip)) % 10


class DigitBattery:
    """All tests + exploit scan on a rolling digit window."""

    def __init__(self, window):
        self.window = window

    def run(self, digs: np.ndarray):
        n = len(digs)
        res = {"n": n, "alarms": [], "candidates": []}
        cnt = np.bincount(digs, minlength=10)
        chi2, p_uni = stats.chisquare(cnt)
        res["p_uniform"] = p_uni

        # lag-1 / lag-2 transition independence
        for lag in (1, 2):
            T = np.zeros((10, 10))
            for a, b in zip(digs[:-lag], digs[lag:]):
                T[a, b] += 1
            exp = T.sum(1, keepdims=True) @ (T.sum(0, keepdims=True) / T.sum())
            c2 = ((T - exp) ** 2 / np.where(exp == 0, 1, exp)).sum()
            res[f"p_lag{lag}"] = 1 - stats.chi2.cdf(c2, 81)

        # Bonferroni: 3 family tests
        for k in ("p_uniform", "p_lag1", "p_lag2"):
            if res[k] < ALPHA / 3:
                res["alarms"].append(k)

        # Exploit scan — per-digit over/under/differ/match win rates with
        # Wilson 99.9% lower bounds (200 cells → Bonferroni inside bound z)
        z = stats.norm.ppf(1 - ALPHA / 200)
        freq = cnt / n
        for d in range(10):
            p_match = freq[d]
            lo_diff = self._wilson_lo(1 - p_match, n, z)
            res["candidates"].append(("DIGITDIFF", d, 1 - p_match, lo_diff))
            p_over = freq[d + 1:].sum() if d < 9 else 0.0
            res["candidates"].append(("DIGITOVER", d, p_over, self._wilson_lo(p_over, n, z)))
            p_under = freq[:d].sum() if d > 0 else 0.0
            res["candidates"].append(("DIGITUNDER", d, p_under, self._wilson_lo(p_under, n, z)))
        p_even = freq[::2].sum()
        res["candidates"].append(("DIGITEVEN", None, p_even, self._wilson_lo(p_even, n, z)))
        res["candidates"].append(("DIGITODD", None, 1 - p_even, self._wilson_lo(1 - p_even, n, z)))
        return res

    @staticmethod
    def _wilson_lo(p, n, z):
        if n == 0:
            return 0.0
        den = 1 + z * z / n
        ctr = p + z * z / (2 * n)
        rad = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
        return (ctr - rad) / den


class Sentinel:
    def __init__(self, symbols, window, token=None, live=False):
        self.symbols = symbols
        self.window = window
        self.token = token
        self.live = live
        self.buf = {s: deque(maxlen=window) for s in symbols}
        self.pips = {}
        self.since_test = {s: 0 for s in symbols}
        self.battery = DigitBattery(window)
        self.balance = None

    async def req(self, ws, payload):
        await ws.send(json.dumps(payload))
        while True:
            r = json.loads(await ws.recv())
            if r.get("msg_type") in ("ping",):
                continue
            if r.get("msg_type") == "tick":
                self._on_tick(r["tick"])
                continue
            return r

    def _on_tick(self, t):
        s = t["symbol"]
        if s in self.buf:
            self.buf[s].append(t["quote"])
            self.since_test[s] += 1

    async def payout_ratio(self, ws, symbol, ctype, barrier):
        p = {"proposal": 1, "amount": 10, "basis": "stake", "currency": "USD",
             "contract_type": ctype, "symbol": symbol, "duration": 1, "duration_unit": "t"}
        if barrier is not None:
            p["barrier"] = str(barrier)
        r = await self.req(ws, p)
        if "error" in r:
            return None
        pr = r["proposal"]
        return pr["payout"] / pr["ask_price"]

    async def maybe_exploit(self, ws, symbol, res):
        """Check every candidate against its LIVE payout threshold."""
        best = None
        for ctype, barrier, p_hat, lo in sorted(res["candidates"], key=lambda x: -x[3]):
            if lo <= 0.5:  # nothing with conviction
                continue
            ratio = await self.payout_ratio(ws, symbol, ctype, barrier)
            if ratio is None:
                continue
            req_wr = 1.0 / ratio
            if lo > req_wr:
                ev = p_hat * ratio - 1
                best = (ctype, barrier, p_hat, lo, ratio, req_wr, ev)
                break
            # candidates are sorted by conviction; if the strongest one with a
            # quote fails, weaker ones at similar payouts will too — but
            # payouts differ per contract, so test the top 5 anyway
        return best

    def kelly_stake(self, p, ratio):
        b = ratio - 1
        f = (p * ratio - 1) / b if b > 0 else 0
        f = max(0.0, f) * KELLY_FRACTION
        bal = self.balance or 1000.0
        return min(f * bal, MAX_STAKE_PCT * bal)

    async def place_trade(self, ws, symbol, ctype, barrier, stake):
        p = {"buy": 1, "price": stake,
             "parameters": {"amount": stake, "basis": "stake", "currency": "USD",
                            "contract_type": ctype, "symbol": symbol,
                            "duration": 1, "duration_unit": "t"}}
        if barrier is not None:
            p["parameters"]["barrier"] = str(barrier)
        r = await self.req(ws, p)
        return r

    async def run(self):
        async with websockets.connect(URL, max_size=2**24) as ws:
            if self.token:
                auth = await self.req(ws, {"authorize": self.token})
                if "error" in auth:
                    print("AUTH FAILED:", auth["error"]["message"]); return
                self.balance = auth["authorize"]["balance"]
                print(f"Authorized {auth['authorize']['loginid']} balance={self.balance} "
                      f"{'LIVE' if self.live else 'PAPER'}")
            # warm start from history, then stream
            for s in self.symbols:
                h = await self.req(ws, {"ticks_history": s, "count": min(self.window, 5000),
                                        "end": "latest", "style": "ticks"})
                if "history" in h:
                    self.pips[s] = int(h["pip_size"]) if "pip_size" in h else 2
                    for q in h["history"]["prices"]:
                        self.buf[s].append(q)
                # subscribe: response stream IS tick messages, don't await a
                # non-tick reply or we deadlock
                await ws.send(json.dumps({"ticks": s, "subscribe": 1}))
                self.since_test[s] = RETEST_EVERY  # run first battery immediately
            print(f"Sentinel armed on {self.symbols}. window={self.window} "
                  f"alpha={ALPHA} retest_every={RETEST_EVERY}")
            while True:
                msg = json.loads(await ws.recv())
                if msg.get("msg_type") == "tick":
                    self._on_tick(msg["tick"])
                    s = msg["tick"]["symbol"]
                    if len(self.buf[s]) >= MIN_WINDOW and self.since_test[s] >= RETEST_EVERY:
                        self.since_test[s] = 0
                        pip = self.pips.get(s, 2)
                        digs = np.array([last_digit(q, pip) for q in self.buf[s]])
                        res = self.battery.run(digs)
                        stamp = time.strftime("%H:%M:%S")
                        line = (f"[{stamp}] {s} n={res['n']} p_uni={res['p_uniform']:.3f} "
                                f"p_lag1={res['p_lag1']:.3f} p_lag2={res['p_lag2']:.3f}")
                        if res["alarms"]:
                            line += f"  *** ALARM {res['alarms']} — scanning exploits"
                            print(line, flush=True)
                            hit = await self.maybe_exploit(ws, s, res)
                            if hit:
                                ctype, barrier, p_hat, lo, ratio, req_wr, ev = hit
                                stake = self.kelly_stake(p_hat, ratio)
                                print(f"  +EV CONFIRMED {ctype} b={barrier} p̂={p_hat:.4f} "
                                      f"lo={lo:.4f} > req={req_wr:.4f} EV={ev:+.4%} stake=${stake:.2f}")
                                if self.live and self.token:
                                    r = await self.place_trade(ws, s, ctype, barrier, stake)
                                    print("  TRADE:", json.dumps(r)[:300])
                                else:
                                    print("  PAPER MODE — trade logged, not sent")
                            else:
                                print("  alarm did not clear live payout threshold — no trade")
                        else:
                            print(line, flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="1HZ100V,R_100")
    ap.add_argument("--window", type=int, default=20000)
    ap.add_argument("--token", default=None)
    ap.add_argument("--live", action="store_true")
    a = ap.parse_args()
    s = Sentinel(a.symbols.split(","), a.window, a.token, a.live)
    try:
        asyncio.run(s.run())
    except KeyboardInterrupt:
        sys.exit(0)
