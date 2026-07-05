"""captain_sentinel.py — v3: the physics-complete sentinel. No tables. No retraining. Ever.

WHY V3 (what was wrong with v2/adaptive_sentinel):
  * v2/adaptive estimated sigma with a 1800-tick rolling window: 30 min of lag after any spot
    level change + estimation noise that leaks trades into the dead zone (and sat out good dips
    late). But JD100 is a constant-100%-vol 1s index: sigma_pips = c*spot with
    c = 1/sqrt(365*86400)*100 = 0.017807, verified to <1% error on 1.38M ticks across spot
    218-440. Sigma is DETERMINISTIC. v3 derives it from the current tick's spot. Zero lag,
    zero noise, exact behavior in regimes never seen before.
  * adaptive_sentinel's trailing table was a noise source (optimizer's curse on narrow
    contracts; fixed by Fable via the wide ladder, but the table itself remained). v3 needs no
    table at all: normalized steps are Gaussian (kurtosis -0.06 on 1.2M steps, quantiles match
    to 3 decimals, shape invariant across regimes) so the digit pmf is a continuity-corrected
    discrete Gaussian folded mod 10. Calibration verified within +-0.5pp on 3 separated periods.
  * Walk-forward (zero fitted params): jun11 wide +2.49%/t t=2.9 (auto-idles the entire high-
    spot regime that bled -$19.5k static); jun29 +1.66%/t t=3.5; jul05 +1.25%/t.

WHAT IT DOES:
  sigma = c*spot -> discrete-Gaussian digit pmf -> best wide-ladder contract (OVER3/OVER4/
  UNDER5/UNDER6, conditioned on current digit) -> trade iff (p-haircut)*payout-1 > gate.
  Flat stake. Hard sigma ceiling 4.6 as belt-and-braces. Universe watch: hourly sigma scan of
  all digit-capable symbols, alerts if anything else enters the zone.

Watch : python3 captain_sentinel.py --minutes 10
Trade : python3 captain_sentinel.py --trade --stake 0.35 --ev-gate 0.005 --minutes 480 --max-loss 40
Smoke : add --smoke 3  (places 3 min-stake execution-test trades at start, ignoring the gate)
"""
import argparse, json, math, time, collections, threading, queue, csv, os, sys
import websocket
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
from deriv_api import DerivWS, WS_PUBLIC

C_THEORY = 100.0 / math.sqrt(365 * 86400)   # 0.017807 pips per unit spot
JUMP = 20
LADDER = [("DIGITOVER", "3"), ("DIGITOVER", "4"), ("DIGITUNDER", "5"), ("DIGITUNDER", "6")]
WINSET = {(ct, b): (frozenset(range(int(b) + 1, 10)) if ct == "DIGITOVER"
                    else frozenset(range(0, int(b)))) for ct, b in LADDER}
DIGIT_SYMS = ["JD100", "JD75", "JD50", "JD25", "JD10", "R_100", "R_75", "R_50", "R_25", "R_10",
              "1HZ100V", "1HZ75V", "1HZ50V", "1HZ25V", "1HZ10V", "RDBULL", "RDBEAR"]

def phi(v): return 0.5 * (1 + math.erf(v / math.sqrt(2)))

def dg_off(sigma):
    off = [0.0] * 10
    for k in range(-JUMP, JUMP + 1):
        off[k % 10] += phi((k + 0.5) / sigma) - phi((k - 0.5) / sigma)
    t = sum(off)
    return [o / t for o in off]

class Brain:
    """precomputed (sigma_grid, digit) -> (ct, barrier, p). EV computed live vs live payouts."""
    def __init__(self, lo=3.0, hi=5.0, step=0.01):
        self.lo, self.step = lo, step
        self.grid = [round(lo + i * step, 4) for i in range(int((hi - lo) / step) + 1)]
        self.tab = []
        for s in self.grid:
            off = dg_off(s)
            row = []
            for d in range(10):
                best = None
                for cb in LADDER:
                    p = sum(off[(w - d) % 10] for w in WINSET[cb])
                    # rank by EV with the canonical grid payouts; live payout applied at decision
                    M = 1.953 if cb[1] in ("4", "5") else 1.626
                    ev = p * M - 1
                    if best is None or ev > best[2]: best = (cb, p, ev)
                row.append((best[0], best[1]))
            self.tab.append(row)
    def pick(self, sigma, digit):
        i = int(round((sigma - self.lo) / self.step))
        i = max(0, min(len(self.tab) - 1, i))
        return self.tab[i][digit]   # ((ct,b), p)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="JD100")
    ap.add_argument("--minutes", type=float, default=10)
    ap.add_argument("--trade", action="store_true")
    ap.add_argument("--stake", type=float, default=0.35)
    ap.add_argument("--ev-gate", type=float, default=0.005)
    ap.add_argument("--haircut", type=float, default=0.001,
                    help="probability safety margin vs model p (calibration wobble ~0.1-0.5pp)")
    ap.add_argument("--sigma-ceiling", type=float, default=4.6)
    ap.add_argument("--max-age", type=float, default=0.45)
    ap.add_argument("--max-loss", type=float, default=40.0)
    ap.add_argument("--max-trades", type=int, default=100000)
    ap.add_argument("--smoke", type=int, default=0, help="N execution-test trades at start (ignore gate)")
    ap.add_argument("--allow-real", action="store_true")
    ap.add_argument("--universe-scan-min", type=float, default=60)
    ap.add_argument("--log", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                  "results", "captain_trades.csv"))
    a = ap.parse_args()
    os.makedirs(os.path.dirname(a.log), exist_ok=True)
    sym = a.symbol

    ctl = DerivWS(token="")
    # ---- warmup: verify physics constant + cadence on fresh ticks ----
    h = ctl.ticks_history(sym, count=1000)
    pr = h["history"]["prices"]; tsx = h["history"]["times"]
    pip = int(h["pip_size"]); scale = 10 ** pip
    xs = [round(float(p) * scale) for p in pr]
    steps = [(xs[i + 1] - xs[i], xs[i]) for i in range(len(xs) - 1)]
    njr = [(s / (x / scale)) ** 2 for s, x in steps if abs(s) <= JUMP]
    c_hat = math.sqrt(sum(njr) / len(njr))
    cadence = (tsx[-1] - tsx[0]) / (len(tsx) - 1)
    print(f"{sym}: pip={pip} c_hat={c_hat:.6f} theory={C_THEORY:.6f} "
          f"({100 * (c_hat / C_THEORY - 1):+.1f}%) cadence={cadence:.2f}s")
    if abs(c_hat / C_THEORY - 1) > 0.05:
        print("!! c deviates >5% from theory — index re-spec? ABORT"); return
    if not (0.9 < cadence < 1.1):
        print("!! cadence not 1s — ABORT"); return

    # ---- live payouts for the ladder (and grid sanity) ----
    payouts = {}
    for ct, b in LADDER:
        r = ctl.proposal(amount=10, basis="stake", contract_type=ct, currency="USD",
                         duration=1, duration_unit="t", underlying_symbol=sym, barrier=b)
        if "proposal" in r:
            payouts[(ct, b)] = float(r["proposal"]["payout"]) / 10
        time.sleep(0.06)
    print("payouts:", {f"{c}{b}": p for (c, b), p in payouts.items()})
    exp = {"3": 1.626, "4": 1.953, "5": 1.953, "6": 1.626}
    for (ct, b), M in payouts.items():
        if abs(M / exp[b] - 1) > 0.01:
            print(f"!! payout {ct}{b}={M} off the verified grid ({exp[b]}) — Deriv repriced? ABORT")
            return
    if len(payouts) < 4:
        print("!! missing ladder payouts — ABORT"); return

    brain = Brain()
    c_use = C_THEORY  # theory constant; c_hat verified consistent

    trader = settler = None
    if a.trade:
        trader = DerivWS(timeout=8); acct = trader.account or {}
        if not acct.get("account_id"):
            print("token invalid -> watch only"); trader = None
        elif acct.get("account_type") != "demo" and not a.allow_real:
            print("REAL account, refusing without --allow-real"); trader = None
        else:
            settler = DerivWS(timeout=15)
            print(f"TRADING {acct['account_id']} ({acct['account_type']}) bal={acct.get('balance')}")
    if trader:
        def ka():
            while True:
                time.sleep(25)
                try: trader.call({"ping": 1}, retries=1)
                except Exception:
                    try: trader._connect()
                    except Exception: pass
        threading.Thread(target=ka, daemon=True).start()

    pend = queue.Queue()
    R = {"pnl": 0.0, "n": 0, "wins": 0, "stakes": 0.0, "lock": threading.Lock(), "lag": collections.Counter()}
    logf = open(a.log, "a", newline=""); logw = csv.writer(logf)
    if os.path.getsize(a.log) == 0:
        logw.writerow(["ts", "sym", "epoch", "spot", "digit", "sigma", "ct", "bar", "model_p",
                       "ev", "stake", "rtt_ms", "cid", "status", "pnl", "lag", "smoke"])
    def settle_loop():
        while True:
            try: item = pend.get(timeout=20)
            except queue.Empty:
                try: settler.call({"ping": 1}, retries=1)
                except Exception:
                    try: settler._connect()
                    except Exception: pass
                continue
            if item is None: return
            cid, T, spot, dig, sg, ct, bar, p, ev, stake, rtt, smoke = item
            time.sleep(2.0); pnl = None; status = "?"; lag = None
            for _ in range(10):
                pc = settler.open_contract(cid); c = pc.get("proposal_open_contract", {})
                if c.get("is_sold") or c.get("status") in ("won", "lost"):
                    status = c.get("status"); pnl = float(c.get("profit", 0))
                    et = c.get("exit_spot_time") or c.get("expiry_time")
                    lag = (et - T) if et else None
                    break
                time.sleep(0.8)
            with R["lock"]:
                if not smoke:
                    R["n"] += 1
                    if pnl is not None:
                        R["pnl"] += pnl; R["wins"] += int(pnl > 0); R["stakes"] += stake
                if lag is not None: R["lag"][lag] += 1
            logw.writerow([time.strftime("%H:%M:%S"), sym, T, f"{spot:.2f}", dig, f"{sg:.3f}",
                           ct, bar, f"{p:.4f}", f"{ev:.4f}", f"{stake:.2f}", f"{rtt*1000:.0f}",
                           cid, status, pnl, lag, int(smoke)])
            logf.flush()
    if trader: threading.Thread(target=settle_loop, daemon=True).start()

    # ---- universe watch (hourly) ----
    def universe_watch():
        uws = DerivWS(token="")
        while True:
            rows = []
            for s2 in DIGIT_SYMS:
                try:
                    hh = uws.ticks_history(s2, count=600)
                    pp = hh["history"]["prices"]; pip2 = int(hh["pip_size"])
                    xs2 = [round(float(q) * 10 ** pip2) for q in pp]
                    st2 = [xs2[i + 1] - xs2[i] for i in range(len(xs2) - 1)]
                    nj2 = [q for q in st2 if abs(q) <= JUMP] or [0]
                    if len(nj2) > 200:
                        sg2 = math.sqrt(sum(q * q for q in nj2) / len(nj2))
                        rows.append((sg2, s2))
                except Exception: pass
                time.sleep(0.15)
            rows.sort()
            inz = [f"{s2}:{sg2:.2f}" for sg2, s2 in rows if sg2 < 4.6]
            print(f"{time.strftime('%H:%M:%S')} [universe] lowest sigma: " +
                  ", ".join(f"{s2}={sg2:.2f}" for sg2, s2 in rows[:4]) +
                  (f"  *** IN ZONE: {inz} ***" if inz else ""))
            time.sleep(a.universe_scan_min * 60)
    threading.Thread(target=universe_watch, daemon=True).start()

    # ---- main loop ----
    pub = websocket.create_connection(WS_PUBLIC, timeout=30)
    pub.send(json.dumps({"ticks": sym, "subscribe": 1}))
    rtts = collections.deque(maxlen=40)
    t_end = time.time() + a.minutes * 60
    nsig = ntrade = nskip = smoke_left = 0
    smoke_left = a.smoke
    halt = False; last_status = 0.0
    while time.time() < t_end:
        try: msg = json.loads(pub.recv())
        except Exception:
            try:
                pub = websocket.create_connection(WS_PUBLIC, timeout=30)
                pub.send(json.dumps({"ticks": sym, "subscribe": 1})); continue
            except Exception: break
        if msg.get("msg_type") != "tick": continue
        tk = msg["tick"]; quote = float(tk["quote"])
        x = round(quote * scale); dig = x % 10
        sg = c_use * quote
        cb, p = brain.pick(sg, dig)
        M = payouts[cb]
        ev = (p - a.haircut) * M - 1
        now = time.time()
        if now - last_status > 300:
            with R["lock"]:
                wr = R["wins"] / R["n"] if R["n"] else 0
                print(f"{time.strftime('%H:%M:%S')} spot={quote:.2f} sig={sg:.3f} d={dig} "
                      f"best={cb[0]}{cb[1]} EV={ev*100:+.2f}% | trades={R['n']} wr={wr:.4f} "
                      f"PnL={R['pnl']:+.2f} (trigger: spot<{(a.ev_gate + 1) and ''}"
                      f"{_trigger_spot(brain, payouts, a) :.1f} for d=2/7)")
            last_status = now
        do_smoke = smoke_left > 0
        if not do_smoke:
            if sg > a.sigma_ceiling: continue
            if ev <= a.ev_gate: nskip += 1; continue
        nsig += 1
        age = now - tk["epoch"]
        if age > a.max_age: continue
        if not trader or halt: continue
        if len(rtts) >= 10:
            p90 = sorted(rtts)[int(len(rtts) * 0.9)]
            if p90 > 0.8: continue
        stake = max(0.35, round(a.stake, 2))
        params = dict(amount=stake, basis="stake", contract_type=cb[0], currency="USD",
                      duration=1, duration_unit="t", underlying_symbol=sym, barrier=cb[1])
        t0 = time.time()
        try: br = trader.call({"buy": 1, "price": round(stake * 1.02, 2), "parameters": params})
        except Exception as e:
            print("buy transport err", e)
            try: trader._connect()
            except Exception: pass
            continue
        rtt = time.time() - t0; rtts.append(rtt)
        if "buy" in br:
            ntrade += 1
            tag = " SMOKE" if do_smoke else ""
            if do_smoke: smoke_left -= 1
            print(f"{time.strftime('%H:%M:%S')} spot={quote:.2f} sig={sg:.3f} d={dig} "
                  f"{cb[0]}{cb[1]} p={p:.4f} EV={ev*100:+.2f}% ${stake:.2f} "
                  f"BUY rtt={rtt*1000:.0f}ms{tag}")
            pend.put((br["buy"]["contract_id"], tk["epoch"], quote, dig, sg, cb[0], cb[1],
                      p, ev, stake, rtt, do_smoke))
        else:
            err = br.get("error", {}).get("message")
            print("buy err:", err)
            if do_smoke: smoke_left -= 1
        with R["lock"]:
            if R["pnl"] < -abs(a.max_loss): halt = True; print(f"### MAX LOSS {R['pnl']:.2f} -> halt")
        if ntrade >= a.max_trades: halt = True
        # drain backlog for freshest tick
        pub.settimeout(0.001)
        try:
            while True: json.loads(pub.recv())
        except Exception: pass
        pub.settimeout(30)
    time.sleep(4)
    with R["lock"]:
        wr = R["wins"] / R["n"] if R["n"] else 0
        be = 1 / 1.953
        print(f"\n=== CAPTAIN v3 SESSION: signals={nsig} trades={ntrade} skips={nskip}")
        print(f"settled={R['n']} wins={R['wins']} wr={wr:.4f} (breakeven~{be:.4f}) "
              f"PnL={R['pnl']:+.2f} on ${R['stakes']:.2f} staked")
        print(f"lag histogram: {dict(R['lag'])}")
    if trader: pend.put(None)
    logf.close()

def _trigger_spot(brain, payouts, a):
    """spot below which the best case (d=2/7) clears the gate — for the status line"""
    for s in [x / 100 for x in range(500, 300, -1)]:
        cb, p = brain.pick(s, 2)
        if (p - a.haircut) * payouts.get(cb, 1.953) - 1 > a.ev_gate:
            return s / C_THEORY
    return 0.0

if __name__ == "__main__":
    main()
