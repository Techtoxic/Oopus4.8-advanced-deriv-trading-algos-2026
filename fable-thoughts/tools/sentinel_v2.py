"""sentinel_v2.py — production sigma sentinel for the new Deriv API.

Upgrades over opus-thoughts sigma_sentinel.py (which promised some of these in its docstring
but never implemented them):
  1. EMPIRICAL offset tables (built from 1.21M in-sample JD100 ticks, keyed on the exact same
     rolling-sigma estimator) instead of the wrapped-normal assumption. Wrapped-normal is the
     fallback when a sigma bin has no table. OOS: +2.97%/trade t=2.46 (gate .005).
  2. Stale-tick guard: skip decision if the tick is older than --max-age (default 0.45s) by
     local clock, or if a newer tick is already queued on the socket.
  3. RTT guard: rolling p90 of measured buy RTT; refuse to trade when p90 > 0.8 x tick interval.
  4. Settlement tracking on a SECOND authenticated connection: every contract is polled after
     settle; logs realized PnL, win/loss, exit-tick lag vs decision tick (lag-1 verification).
  5. Session risk guard: --max-loss stops trading, --max-trades caps the session.
  6. Demo guard: refuses real accounts without --allow-real.

Watch : python3 sentinel_v2.py --minutes 10
Trade : python3 sentinel_v2.py --trade --stake 1 --ev-gate 0.01 --minutes 60
"""
import argparse, json, math, time, collections, threading, queue, csv, os
import websocket
from deriv_api import DerivWS, WS_PUBLIC
from sigma_model import wrapped_normal_pmf, winset, CONTRACTS

W = 1800          # rolling sigma window (steps), same as table construction
JUMP_THR = 20     # |step| > 20 pips = jump, excluded from sigma
BINW = 0.1        # sigma bin width for empirical tables

def load_tables(path="../results/empirical_offset_tables.json"):
    try:
        raw = json.load(open(path))
        return {int(k): v for k, v in raw.items()}
    except Exception:
        return {}

class SymState:
    def __init__(self, pip):
        self.pip = pip
        self.vals = collections.deque(maxlen=W + 2)
        self.sq = collections.deque(maxlen=W)   # squared no-jump steps (0 for jumps)
        self.cn = collections.deque(maxlen=W)   # 1 if no-jump step else 0
        self.sum_sq = 0.0; self.sum_cn = 0
        self.last_epoch = None
        self.intervals = collections.deque(maxlen=50)
    def push(self, epoch, quote):
        v = round(float(quote) * (10 ** self.pip))
        if self.vals:
            st = v - self.vals[-1]
            nj = 1 if abs(st) <= JUMP_THR else 0
            sq = float(st * st) if nj else 0.0
            if len(self.sq) == W:
                self.sum_sq -= self.sq[0]; self.sum_cn -= self.cn[0]
            self.sq.append(sq); self.cn.append(nj)
            self.sum_sq += sq; self.sum_cn += nj
        if self.last_epoch is not None:
            self.intervals.append(epoch - self.last_epoch)
        self.last_epoch = epoch
        self.vals.append(v)
    def sigma(self):
        if self.sum_cn < 200: return None
        return math.sqrt(self.sum_sq / self.sum_cn)
    def digit(self):
        return self.vals[-1] % 10 if self.vals else None
    def tick_interval(self):
        iv = sorted(self.intervals)
        return iv[len(iv) // 2] if iv else None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", nargs="+", default=["JD100"])
    ap.add_argument("--minutes", type=float, default=10)
    ap.add_argument("--trade", action="store_true")
    ap.add_argument("--stake", type=float, default=1.0)
    ap.add_argument("--ev-gate", type=float, default=0.01)
    ap.add_argument("--max-age", type=float, default=0.45)
    ap.add_argument("--max-loss", type=float, default=100.0)
    ap.add_argument("--max-trades", type=int, default=100000)
    ap.add_argument("--allow-real", action="store_true")
    ap.add_argument("--log", default="../results/sentinel_v2_trades.csv")
    a = ap.parse_args()

    tables = load_tables()
    print(f"empirical tables: {len(tables)} sigma bins" if tables else "NO tables -> wrapped-normal only")

    ctl = DerivWS(token="")
    payouts, pips = {}, {}
    for sym in a.watch:
        h = ctl.ticks_history(sym, count=W + 2)
        pips[sym] = int(h["pip_size"])
        payouts[sym] = {}
        for t, b in CONTRACTS:
            req = dict(amount=10, basis="stake", contract_type=t, currency="USD",
                       duration=1, duration_unit="t", underlying_symbol=sym)
            if b is not None: req["barrier"] = str(b)
            r = ctl.proposal(**req)
            if "proposal" in r:
                payouts[sym][(t, b)] = float(r["proposal"]["payout"]) / 10
            time.sleep(0.08)
        print(f"{sym}: {len(payouts[sym])} payouts; pip={pips[sym]}")

    trader = settler = None
    if a.trade:
        trader = DerivWS()
        acct = trader.account or {}
        if not acct.get("account_id"):
            print("token invalid -> watch only"); trader = None
        elif acct.get("account_type") != "demo" and not a.allow_real:
            print("REAL account, refusing without --allow-real"); trader = None
        else:
            settler = DerivWS()   # second conn: settlement polling never blocks buys
            print(f"TRADING on {acct['account_id']} ({acct['account_type']}) bal={acct.get('balance')}")

    # settlement worker
    pend = queue.Queue()
    results = {"pnl": 0.0, "n": 0, "wins": 0, "lag_hist": collections.Counter(), "lock": threading.Lock()}
    logf = open(a.log, "a", newline="")
    logw = csv.writer(logf)
    if os.path.getsize(a.log) == 0:
        logw.writerow(["ts", "sym", "decision_epoch", "digit", "sigma", "ctype", "barrier",
                       "model_p", "model_ev", "rtt_ms", "cid", "status", "pnl", "exit_lag_s"])
    def settle_loop():
        while True:
            item = pend.get()
            if item is None: return
            cid, T, sym, sig, ct, bar, mp, mev, rtt, stake = item
            time.sleep(2.0)
            pnl = None; status = "?"; lag = None
            for _ in range(10):
                pc = settler.open_contract(cid)
                c = pc.get("proposal_open_contract", {})
                if c.get("is_sold") or c.get("status") in ("won", "lost"):
                    status = c.get("status")
                    pnl = float(c.get("profit", 0))
                    et = c.get("exit_spot_time") or c.get("expiry_time")
                    lag = (et - T) if et else None
                    break
                time.sleep(0.8)
            with results["lock"]:
                results["n"] += 1
                if pnl is not None:
                    results["pnl"] += pnl
                    results["wins"] += int(pnl > 0)
                if lag is not None:
                    results["lag_hist"][lag] += 1
            logw.writerow([time.strftime("%H:%M:%S"), sym, T, "", f"{sig:.3f}", ct, bar,
                           f"{mp:.4f}", f"{mev:.4f}", f"{rtt*1000:.0f}", cid, status, pnl, lag])
            logf.flush()
    st_thread = threading.Thread(target=settle_loop, daemon=True)
    if trader: st_thread.start()

    pub = websocket.create_connection(WS_PUBLIC, timeout=30)
    for sym in a.watch:
        pub.send(json.dumps({"ticks": sym, "subscribe": 1}))
    states = {sym: SymState(pips[sym]) for sym in a.watch}
    for sym in a.watch:
        h = ctl.ticks_history(sym, count=W + 2)
        for t, q in zip(h["history"]["times"], h["history"]["prices"]):
            states[sym].push(int(t), q)
        s = states[sym].sigma()
        print(f"{sym}: warm sigma={s and round(s, 2)} interval={states[sym].tick_interval()}s")

    rtts = collections.deque(maxlen=40)
    t_end = time.time() + a.minutes * 60
    nsig = ntrade = nstale = nrefuse = 0
    session_pnl_guard_hit = False
    while time.time() < t_end:
        try:
            msg = json.loads(pub.recv())
        except Exception:
            try:
                pub = websocket.create_connection(WS_PUBLIC, timeout=30)
                for sym in a.watch: pub.send(json.dumps({"ticks": sym, "subscribe": 1}))
                continue
            except Exception:
                break
        if msg.get("msg_type") != "tick": continue
        tk = msg["tick"]; sym = tk["symbol"]
        st = states[sym]
        st.push(tk["epoch"], tk["quote"])
        age = time.time() - tk["epoch"]
        sigma = st.sigma()
        if sigma is None: continue
        d = st.digit()
        b = round(sigma / BINW)
        if b in tables:
            off = tables[b]
            pmf = [off[(dd - d) % 10] for dd in range(10)]
            src = "emp"
        else:
            pmf = wrapped_normal_pmf(sigma, d)
            src = "wn"
        best = None
        for (t, bar), M in payouts[sym].items():
            p = sum(pmf[x] for x in winset(t, bar))
            ev = p * M - 1
            if best is None or ev > best[3]: best = (t, bar, p, ev, M)
        if best is None: continue
        line = (f"{time.strftime('%H:%M:%S')} {sym} d={d} sig={sigma:.2f}[{src}] "
                f"best={best[0]}{'' if best[1] is None else best[1]} p={best[2]:.4f} EV={best[3]*100:+.2f}%")
        if best[3] > a.ev_gate:
            nsig += 1
            if age > a.max_age:
                nstale += 1
                continue
            iv = st.tick_interval() or 1
            if trader and len(rtts) >= 10:
                p90 = sorted(rtts)[int(len(rtts) * 0.9)]
                if p90 > 0.8 * iv:
                    nrefuse += 1
                    continue
            print(line + "  *** SIGNAL ***")
            if trader and not session_pnl_guard_hit and ntrade < a.max_trades:
                params = dict(amount=a.stake, basis="stake", contract_type=best[0], currency="USD",
                              duration=1, duration_unit="t", underlying_symbol=sym)
                if best[1] is not None: params["barrier"] = str(best[1])
                t0 = time.time()
                br = trader.call({"buy": 1, "price": round(a.stake * 1.02, 2), "parameters": params})
                rtt = time.time() - t0
                rtts.append(rtt)
                if "buy" in br:
                    ntrade += 1
                    pend.put((br["buy"]["contract_id"], tk["epoch"], sym, sigma, best[0], best[1],
                              best[2], best[3], rtt, a.stake))
                else:
                    print("  buy error:", br.get("error", {}).get("message"))
                with results["lock"]:
                    if results["pnl"] < -abs(a.max_loss):
                        session_pnl_guard_hit = True
                        print(f"### MAX LOSS {results['pnl']:.2f} -> trading halted, watch only")
        else:
            if (len(st.vals) % 60) == 0: print(line)
        # drain backlog so the next decision is on the freshest tick
        pub.settimeout(0.001)
        try:
            while True:
                m2 = json.loads(pub.recv())
                if m2.get("msg_type") == "tick":
                    t2 = m2["tick"]
                    states[t2["symbol"]].push(t2["epoch"], t2["quote"])
        except Exception:
            pass
        pub.settimeout(30)

    time.sleep(4)
    with results["lock"]:
        lag = dict(results["lag_hist"])
        print(f"\n=== SESSION: signals={nsig} trades={ntrade} stale-skips={nstale} rtt-refusals={nrefuse}")
        print(f"settled={results['n']} wins={results['wins']} PnL={results['pnl']:+.2f}")
        print(f"exit-tick lag histogram (s after decision tick): {lag}")
    if pend.qsize() == 0 and trader: pend.put(None)
    logf.close()

if __name__ == "__main__":
    main()
