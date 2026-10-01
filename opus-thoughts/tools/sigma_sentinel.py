"""sigma_sentinel.py — watches digit-enabled symbols' tick streams, maintains rolling
sigma_pips, prices the conditional next-digit distribution (wrapped-normal, validated on JD100
data), joins LIVE payouts, and reports/fires any contract whose model EV clears the gate.

MIGRATED TO THE NEW DERIV API (api.derivws.com) — see deriv_api.py.
NOTE: the maintained production bot is `fable-thoughts/tools/sentinel_v2.py` (empirical tables,
sigma-max physics gate, RTT/stale guards, settlement tracking). This file is kept as the
faithful new-API port of the original opus sentinel.

Watch mode  : python3 sigma_sentinel.py --watch JD100 R_100 1HZ100V --minutes 10
Trade mode  : python3 sigma_sentinel.py --watch JD100 --trade --stake 1 --ev-gate 0.01
"""
import argparse, json, math, time, collections, os
import websocket
from deriv_api import DerivWS, URL, TOKEN, last_digit

def wrapped_normal_pmf(sigma, center):
    from math import erf
    def cdf(x): return 0.5 * (1 + erf(x / (sigma * math.sqrt(2))))
    pmf = [0.0] * 10
    for d in range(10):
        for wrap in (-30, -20, -10, 0, 10, 20, 30):
            off = (d - center) + wrap
            pmf[d] += cdf(off + 0.5) - cdf(off - 0.5)
    return pmf

CONTRACTS = ([("DIGITMATCH", d) for d in range(10)] + [("DIGITDIFF", d) for d in range(10)] +
             [("DIGITOVER", k) for k in range(9)] + [("DIGITUNDER", k) for k in range(1, 10)] +
             [("DIGITEVEN", None), ("DIGITODD", None)])

def winset(t, b):
    if t == "DIGITMATCH": return {b}
    if t == "DIGITDIFF": return set(range(10)) - {b}
    if t == "DIGITOVER": return set(range(b + 1, 10))
    if t == "DIGITUNDER": return set(range(0, b))
    if t == "DIGITEVEN": return {0, 2, 4, 6, 8}
    return {1, 3, 5, 7, 9}

class SymState:
    def __init__(self, pip):
        self.pip = pip
        self.vals = collections.deque(maxlen=2000)
        self.last_t = None
        self.interval = None
    def push(self, epoch, quote):
        v = round(float(quote) * (10 ** self.pip))
        if self.last_t: self.interval = epoch - self.last_t
        self.last_t = epoch
        self.vals.append(v)
    def sigma(self):
        if len(self.vals) < 300: return None
        vs = list(self.vals)
        st = [b - a for a, b in zip(vs, vs[1:]) if abs(b - a) <= 20]
        if len(st) < 200: return None
        m = sum(st) / len(st)
        return (sum((x - m) ** 2 for x in st) / len(st)) ** 0.5
    def digit(self):
        return self.vals[-1] % 10 if self.vals else None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", nargs="+", default=["JD100"])
    ap.add_argument("--minutes", type=float, default=5)
    ap.add_argument("--trade", action="store_true")
    ap.add_argument("--stake", type=float, default=1.0)
    ap.add_argument("--ev-gate", type=float, default=0.005)
    ap.add_argument("--sigma-max", type=float, default=4.35)
    ap.add_argument("--allow-real", action="store_true")
    a = ap.parse_args()

    # Public connection for market data + proposals (no auth needed)
    ctl = DerivWS(token="")
    payouts = {}
    pips = {}
    for sym in a.watch:
        h = ctl.ticks_history(sym, count=10)
        pips[sym] = int(h["pip_size"])
        payouts[sym] = {}
        for t, b in CONTRACTS:
            req = dict(amount=10, basis="stake", contract_type=t, currency="USD",
                       duration=1, duration_unit="t", underlying_symbol=sym)
            if b is not None: req["barrier"] = str(b)
            r = ctl.proposal(**req)
            if "proposal" in r:
                payouts[sym][(t, b)] = float(r["proposal"]["payout"]) / 10
            time.sleep(0.1)
        print(f"{sym}: {len(payouts[sym])} live payouts loaded; pip={pips[sym]}")

    trader = None
    if a.trade:
        # Authenticated connection — new API handles REST→OTP→WS internally
        trader = DerivWS()
        acct = getattr(trader, "account", {}) or {}
        if not acct.get("account_id"):
            print("trade mode: token invalid/disabled -> watch only"); trader = None
        elif acct.get("account_type") != "demo" and not a.allow_real:
            print("trade mode: REAL account, refusing without --allow-real"); trader = None

    # Raw public WebSocket for tick stream (no auth required for ticks)
    ws = websocket.create_connection(URL, timeout=30)
    for sym in a.watch:
        ws.send(json.dumps({"ticks": sym, "subscribe": 1}))
    states = {sym: SymState(pips[sym]) for sym in a.watch}
    # warm-start sigma from history so signals are live immediately
    for sym in a.watch:
        h = ctl.ticks_history(sym, count=1500)
        for t, q in zip(h["history"]["times"], h["history"]["prices"]):
            states[sym].push(int(t), q)
        s = states[sym].sigma()
        print(f"{sym}: warm sigma={s if s is None else round(s, 2)} pips")
    t_end = time.time() + a.minutes * 60
    nsig = ntrade = 0
    os.makedirs("../results", exist_ok=True)
    log = open("../results/sentinel.log", "a")
    while time.time() < t_end:
        try:
            msg = json.loads(ws.recv())
        except Exception:
            break
        if msg.get("msg_type") != "tick": continue
        tk = msg["tick"]; sym = tk["symbol"]
        st = states[sym]
        st.push(tk["epoch"], tk["quote"])
        sigma = st.sigma()
        if sigma is None: continue
        if sigma > a.sigma_max: continue   # hard physics gate (see results/walkforward.md)
        d = st.digit()
        pmf = wrapped_normal_pmf(sigma, d)
        best = None
        for (t, b), M in payouts[sym].items():
            p = sum(pmf[x] for x in winset(t, b))
            ev = p * M - 1
            if best is None or ev > best[3]:
                best = (t, b, p, ev, M)
        if best is None: continue  # no payouts loaded for this symbol yet
        line = (f"{time.strftime('%H:%M:%S')} {sym} spot={tk['quote']} d={d} sigma={sigma:.2f} "
                f"best={best[0]}{best[1] if best[1] is not None else ''} p={best[2]:.4f} EV={best[3]*100:+.2f}%")
        if best[3] > a.ev_gate:
            nsig += 1
            line += "  *** SIGNAL ***"
            print(line); log.write(line + "\n"); log.flush()
            if trader:
                t0 = time.time()
                params = dict(amount=a.stake, basis="stake", contract_type=best[0], currency="USD",
                              duration=1, duration_unit="t", underlying_symbol=sym)
                if best[1] is not None: params["barrier"] = str(best[1])
                # Single RTT buy — fastest path, no separate proposal call
                br = trader.call({"buy": 1, "price": a.stake * 1.01, "parameters": params})
                rtt = time.time() - t0
                ntrade += 1
                cid = br.get("buy", {}).get("contract_id") or br.get("error", {}).get("message")
                print(f"  -> buy rtt={rtt*1000:.0f}ms contract={cid}")
        elif len(st.vals) % 30 == 0:
            print(line)
    summary = (f"sentinel run done: signals={nsig} trades={ntrade} over {a.minutes}min on {a.watch}; "
               f"sigmas now: " + ", ".join(f"{s}={states[s].sigma() and round(states[s].sigma(),2)}" for s in a.watch))
    print(summary); log.write(summary + "\n"); log.close()

if __name__ == "__main__":
    main()
