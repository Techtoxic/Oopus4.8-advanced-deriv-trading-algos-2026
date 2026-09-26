"""slippage_probe.py — how often does the bot's entry land one tick late?

WHY THIS IS THE ONLY NUMBER THAT MATTERS RIGHT NOW
redo_all.py on 1.2M clean ticks says the sigma 3.10 bin wins 58.85% of the time, with a
99% lower bound of +5.97% EV. The model is validated on real tick data.

adaptive_sentinel_v3 ran 851 settled trades and won 54.52%.

Those two numbers are reconciled by one thing. Section 4 of redo_all measures the T+2 win
rate at 50.39%. The live result sits 49% of the way from T+2 back to T+1, which implies
roughly half of all entries are landing one tick late.

    slippage    win rate    EV at payout 1.794
        0%       58.85%       +5.58%
       30%       56.31%       +1.03%
       37%       55.74%        breakeven
       51%       54.45%       -2.31%   <- where the bot is

**Below 37% slippage the strategy is profitable. Above it, it is not.** Nothing else in the
system is currently in question — the physics, the payout, the sigma gate and the offset
tables all check out.

WHY entry_tick_test.py IS NOT ENOUGH
That tool measured 6/20 = 30% slippage, but it fired a BARE buy with nothing else running.
Production has a subscription callback, rolling-table maintenance, sigma computation and an
EV calculation in front of the order. This probe reproduces the real sequence.

WHAT IT MEASURES
For each trade it records:
  - the epoch of the tick the decision was made on
  - the wall-clock cost of each stage: table update, sigma, EV, then the buy round trip
  - the entry_spot_time the contract actually received
  - slip = entry_epoch - decision_epoch, which should be exactly 1

and reports the slippage rate against the 37% breakeven, plus which stage is eating the time.

Two modes:
  default   full production sequence, the honest number
  --bare    buy immediately on tick receipt with no computation, to separate network
            latency from the bot's own processing

DEMO ONLY. Costs stake x rounds.

Run: python3 slippage_probe.py --rounds 60
     python3 slippage_probe.py --rounds 60 --bare
"""
import argparse
import json
import ssl
import time
import numpy as np
from collections import deque
from deriv_api import DerivWS

try:
    import websocket
except ImportError:
    websocket = None

WS_PUBLIC = "wss://api.derivws.com/trading/v1/options/ws/public"
BREAKEVEN_SLIP = 0.37


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="JD100")
    ap.add_argument("--rounds", type=int, default=60)
    ap.add_argument("--stake", type=float, default=0.35)
    ap.add_argument("--bare", action="store_true",
                    help="skip all computation; isolates pure network latency")
    ap.add_argument("--window", type=int, default=1800)
    ap.add_argument("--warmup", type=int, default=60,
                    help="ticks to buffer before the first trade. 200 meant 200 seconds "
                         "of silence on a 1s symbol; 60 is enough to time the compute "
                         "path, which is what this tool measures.")
    a = ap.parse_args()

    if websocket is None:
        print("pip install websocket-client"); return

    tr = DerivWS()
    acct = tr.account or {}
    if acct.get("account_type") != "demo":
        print(f"NOT demo ({acct.get('account_type')}) — refusing."); return
    print(f"[demo {acct.get('account_id')}] {a.rounds} rounds, "
          f"{'BARE' if a.bare else 'FULL production sequence'}\n")

    ws = websocket.create_connection(WS_PUBLIC, timeout=30,
                                     sslopt={"cert_reqs": ssl.CERT_NONE})
    ws.send(json.dumps({"ticks": a.symbol, "subscribe": 1}))
    print(f"  subscribed to {a.symbol}, waiting for ticks...")

    hist = deque(maxlen=a.window)
    rows = []
    done = 0

    while done < a.rounds:
        # DRAIN THE SOCKET FIRST. While the previous round waited for settlement and
        # slept, ticks kept arriving and queued in the buffer. Calling recv() once
        # returns the OLDEST queued tick, not the current one — which made slip grow
        # monotonically (+4, +6, +8, ...) instead of varying randomly. That was a
        # measurement artifact, not slippage.
        raw = ws.recv()
        drained = 0
        ws.settimeout(0.001)
        try:
            while True:
                nxt = ws.recv()
                if nxt:
                    raw = nxt
                    drained += 1
        except Exception:
            pass
        ws.settimeout(30)

        t_recv = time.perf_counter()
        m = json.loads(raw)
        tk = m.get("tick")
        if not tk:
            continue
        epoch = int(tk["epoch"])
        quote = float(tk["quote"])
        hist.append(quote)

        if len(hist) < a.warmup:
            if len(hist) % 20 == 0:
                print(f"  warming up {len(hist)}/{a.warmup} ticks "
                      f"(spot {quote})", end="\r", flush=True)
            continue
        if len(hist) == a.warmup:
            print(f"  warmup complete at {a.warmup} ticks — trading now"
                  + " " * 20)

        # ---- production sequence, timed stage by stage ----
        t0 = time.perf_counter()
        if not a.bare:
            v = np.round(np.array(hist) * 100).astype(np.int64)
            st = np.diff(v).astype(float)
            f = st[np.abs(st) <= 20]
            sigma = float(np.sqrt((f ** 2).mean())) if len(f) else 99.0
            t_sigma = time.perf_counter()
            dig = (v % 10).astype(int)
            off = (dig[1:] - dig[:-1]) % 10
            tab = np.bincount(off, minlength=10) / max(len(off), 1)
            t_table = time.perf_counter()
            d = int(round(quote * 100)) % 10
            wset = [(d + k) % 10 for k in (0, 1, 2, 8, 9)]
            pwin = float(sum(tab[(x - d) % 10] for x in wset))
            ev = pwin * 1.794 - 1
            t_ev = time.perf_counter()
        else:
            t_sigma = t_table = t_ev = t0

        t_send = time.perf_counter()
        b = tr.call({"buy": 1, "price": round(a.stake * 20, 2), "parameters": dict(
            amount=a.stake, basis="stake", contract_type="DIGITOVER",
            currency="USD", underlying_symbol=a.symbol, duration=1,
            duration_unit="t", barrier="4")})
        t_done = time.perf_counter()
        if "buy" not in b:
            print(f"  buy error: {b.get('error', {}).get('message', '')[:60]}")
            time.sleep(1)
            continue

        cid = b["buy"]["contract_id"]
        c = {}
        t1 = time.time()
        while time.time() - t1 < 25:
            c = tr.open_contract(cid).get("proposal_open_contract", {})
            if c and (c.get("is_sold") or c.get("status") in ("won", "lost")):
                break
            time.sleep(0.4)
        et = c.get("entry_spot_time")
        if not et:
            continue
        slip = int(et) - epoch
        rows.append(dict(
            slip=slip, drained=drained,
            compute_ms=(t_ev - t0) * 1000,
            sigma_ms=(t_sigma - t0) * 1000,
            table_ms=(t_table - t_sigma) * 1000,
            ev_ms=(t_ev - t_table) * 1000,
            buy_ms=(t_done - t_send) * 1000,
            total_ms=(t_done - t_recv) * 1000))
        done += 1
        print(f"  [{done:>3}/{a.rounds}] slip={slip:+d}  "
              f"compute {(t_ev-t0)*1000:>6.1f}ms  buy {(t_done-t_send)*1000:>6.1f}ms  "
              f"total {(t_done-t_recv)*1000:>6.1f}ms")
        time.sleep(0.6)

    ws.close()
    tr.close()

    if not rows:
        print("\nno completed rounds"); return

    slip = np.array([r["slip"] for r in rows])
    print(f"\n{'='*70}")
    print("SLIPPAGE")
    print("=" * 70)
    for s in sorted(set(slip.tolist())):
        n = int((slip == s).sum())
        lab = "on time (T+1)" if s == 1 else (f"LATE by {s-1}" if s > 1 else "early?")
        print(f"  slip {s:+d}: {n:>4} ({n/len(slip)*100:>5.1f}%)  {lab}")
    dr = np.array([r["drained"] for r in rows])
    print(f"\n  queued ticks drained per round: median {np.median(dr):.0f}, "
          f"max {dr.max()}")
    if np.median(dr) > 3:
        print("  a large drain means the settlement wait is eating several ticks —")
        print("  the live bot must not block on settlement between decisions.")
    late = float((slip > 1).mean())
    print(f"\n  late rate {late*100:.1f}%   breakeven threshold {BREAKEVEN_SLIP*100:.0f}%")
    t1, t2, M = 0.58853, 0.50389, 1.794
    p = late * t2 + (1 - late) * t1
    print(f"  implied win rate {p*100:.2f}%, EV {(p*M-1)*100:+.2f}%")
    print(f"  -> {'PROFITABLE' if late < BREAKEVEN_SLIP else 'LOSING at this slippage'}")

    print(f"\n{'='*70}")
    print("WHERE THE TIME GOES")
    print("=" * 70)
    print(f"  {'stage':14}{'median ms':>12}{'p90':>10}{'max':>10}")
    for k, lab in (("sigma_ms", "sigma calc"), ("table_ms", "table build"),
                   ("ev_ms", "EV calc"), ("buy_ms", "buy round trip"),
                   ("total_ms", "TOTAL")):
        v = np.array([r[k] for r in rows])
        print(f"  {lab:14}{np.median(v):>12.1f}{np.percentile(v,90):>10.1f}"
              f"{v.max():>10.1f}")
    comp = np.median([r["compute_ms"] for r in rows])
    buy = np.median([r["buy_ms"] for r in rows])
    print(f"\n  compute is {comp/(comp+buy)*100:.0f}% of the controllable path")
    print(f"  ticks arrive every 1000ms, so the budget is 1000ms minus the buy round trip")

    print(f"\n{'='*70}")
    print("WHAT TO DO")
    print("=" * 70)
    if late >= BREAKEVEN_SLIP:
        if comp > 50:
            print(f"  compute takes {comp:.0f}ms per tick. move the sigma and table")
            print("  maintenance OFF the decision path — update them incrementally")
            print("  between ticks, so the tick handler only reads a cached value.")
        if buy > 300:
            print(f"  the buy round trip is {buy:.0f}ms. that is network, not code.")
            print("  a VPS near the Deriv gateway is the only fix for that half.")
        print("\n  re-run with --bare to see the floor: if bare slippage is already")
        print("  above 37%, no code change helps and the strategy is not viable")
        print("  from this location.")
    else:
        print("  slippage is inside the profitable band. the earlier live result")
        print("  (51% late) may have come from a slower path or worse connectivity.")
        print("  re-run the bot and compare its realised win rate to 58.85%.")


if __name__ == "__main__":
    main()
