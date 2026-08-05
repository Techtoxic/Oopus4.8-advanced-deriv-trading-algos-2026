"""entry_tick_test.py — which tick becomes the contract's ENTRY?

(v2: the threaded tick-tape version swallowed connection errors and reported "no ticks
received". This uses plain request/response calls through DerivWS — no websocket-client
dependency, no background thread, no hidden failures.)

WHY THIS IS THE MOST IMPORTANT UNVERIFIED ASSUMPTION IN THE PROJECT
The digit strategy conditions on the LAST OBSERVED digit and then buys. That is only correct
if the contract's entry spot is the tick you just saw.

But step_pair_test.py showed a 15-SECOND contract with entry->exit elapsed of 14 seconds,
not 15 — entry is the tick AFTER purchase. If the same holds for 1-tick digit contracts:

    observe digit at T  ->  entry at T+1  ->  settle at T+2

so the outcome is TWO steps from the conditioning digit while the tables encode ONE.

duration_surface.py measured the cost on clean ticks: conditioned lift +24.77% at N=1,
+4.54% at N=2, +1.01% at N=3. Four fifths of the effect is gone by N=2.

If the off-by-one is real, sigma decay cannot rescue the thesis: N=2 means effective sigma
= sigma*sqrt(2), so the fitted curve (EV% = 25.18 - 7.07*sigma) needs TRUE sigma <= 2.52,
i.e. spot <= 130 against 223 today. A 42% fall, with noise four times the drift. Dead on
arithmetic rather than on patience.

METHOD
Bracket each purchase with tick-history calls:

    1. ticks_history count=1 -> epoch of the last tick BEFORE the buy   (t_before)
    2. buy the 1-tick contract
    3. ticks_history count=1 -> epoch of the last tick AFTER the buy    (t_after)
    4. read entry_spot_time from the settled contract

If t_before == t_after, no tick arrived during the round trip and the comparison is exact.
Those clean rounds decide it. Rounds where a tick slipped through are reported separately
rather than silently averaged in.

DEMO ONLY. Minimum stake; only the timestamps matter.

Run: python3 entry_tick_test.py --rounds 20
"""
import argparse
import time
import numpy as np
from deriv_api import DerivWS


def last_tick_epoch(ws, sym):
    r = ws.call({"ticks_history": sym, "count": 1, "end": "latest", "style": "ticks"})
    ts = r.get("history", {}).get("times", [])
    return int(ts[-1]) if ts else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="JD100")
    ap.add_argument("--rounds", type=int, default=20)
    ap.add_argument("--stake", type=float, default=0.35)
    ap.add_argument("--contract", default="DIGITOVER")
    ap.add_argument("--barrier", default="4")
    a = ap.parse_args()

    pub = DerivWS(token="")
    e0 = last_tick_epoch(pub, a.symbol)
    if e0 is None:
        print("could not read tick history"); return
    print(f"{a.symbol}: latest tick epoch {e0}")

    tr = DerivWS()
    acct = tr.account or {}
    if acct.get("account_type") != "demo":
        print(f"NOT demo ({acct.get('account_type')}) — refusing."); return
    print(f"[demo {acct.get('account_id')}] {a.rounds} x 1-tick {a.contract}{a.barrier}\n")

    print(f"{'#':>3}{'t_before':>12}{'t_after':>11}{'entry':>12}{'exit':>12}"
          f"{'e-before':>10}{'exit-entry':>12}{'rtt_ms':>8}  clean")
    print("-" * 82)

    rows = []
    for i in range(a.rounds):
        t_before = last_tick_epoch(pub, a.symbol)
        t0 = time.time()
        b = tr.call({"buy": 1, "price": round(a.stake * 20, 2), "parameters": dict(
            amount=a.stake, basis="stake", contract_type=a.contract,
            currency="USD", underlying_symbol=a.symbol, duration=1,
            duration_unit="t", barrier=a.barrier)})
        rtt = (time.time() - t0) * 1000
        if "buy" not in b:
            print(f"{i+1:>3}  buy error: {b.get('error', {}).get('message', '')[:50]}")
            time.sleep(1.5)
            continue
        t_after = last_tick_epoch(pub, a.symbol)
        cid = b["buy"]["contract_id"]

        c = {}
        t1 = time.time()
        while time.time() - t1 < 30:
            c = tr.open_contract(cid).get("proposal_open_contract", {})
            if c and (c.get("is_sold") or c.get("status") in ("won", "lost")):
                break
            time.sleep(0.5)
        et, xt = c.get("entry_spot_time"), c.get("exit_spot_time")
        if not et or not xt or t_before is None or t_after is None:
            print(f"{i+1:>3}  incomplete")
            time.sleep(1)
            continue
        et, xt = int(et), int(xt)
        clean = (t_before == t_after)
        rows.append(dict(tb=t_before, ta=t_after, et=et, xt=xt,
                         d=et - t_before, dx=xt - et, clean=clean, rtt=rtt))
        print(f"{i+1:>3}{t_before:>12}{t_after:>11}{et:>12}{xt:>12}"
              f"{et - t_before:>+10}{xt - et:>+12}{rtt:>8.0f}"
              f"{'  yes' if clean else '   no'}")
        time.sleep(1.3)

    if not rows:
        print("\nno data")
        return

    clean = [r for r in rows if r["clean"]]
    print(f"\n{'=' * 82}")
    print("VERDICT")
    print("=" * 82)
    print(f"  {len(clean)}/{len(rows)} rounds clean (no tick arrived during the round trip)")
    if not clean:
        print("  No clean rounds — retry when the round trip is faster than one tick.")
        return
    d = np.array([r["d"] for r in clean])
    dx = np.array([r["dx"] for r in clean])
    print(f"  entry - last_tick_before_buy : median {int(np.median(d)):+d}   "
          f"values {sorted(set(d.tolist()))}")
    print(f"  exit  - entry                : median {int(np.median(dx)):+d}   "
          f"values {sorted(set(dx.tolist()))}")
    print(f"  median round-trip {np.median([r['rtt'] for r in clean]):.0f} ms")
    print()

    med = int(np.median(d))
    if med == 0:
        print("  ENTRY IS THE TICK ALREADY OBSERVED.")
        print("  Conditioning on the last seen digit is CORRECT and the 1-step offset")
        print("  tables are the right model. The thesis stands as analysed; what remains")
        print("  is a waiting problem, and waiting is a coin flip not a schedule.")
    else:
        horizon = med + int(np.median(dx))
        need = 3.56 / np.sqrt(horizon)
        print(f"  ENTRY IS {med} TICK(S) AFTER THE LAST OBSERVED TICK.")
        print(f"  You condition on T, entry is T+{med}, settlement is T+{horizon}.")
        print(f"  True horizon is {horizon} steps; the tables encode 1.")
        print()
        print("  duration_surface conditioned lift: +24.77% at N=1, +4.54% at N=2.")
        print(f"  At N={horizon} effective sigma is sigma*sqrt({horizon}), so the fitted")
        print(f"  curve EV% = 25.18 - 7.07*sigma requires TRUE sigma <= {need:.2f},")
        print(f"  i.e. spot <= {(need - 0.51126) / 1.543886e-4 / 100:.0f} against 223 today.")
        print()
        print("  NEXT: rebuild the offset tables at the TRUE horizon and re-derive the")
        print("  gate. If the edge does not survive there, the thesis is dead on")
        print("  arithmetic and no amount of waiting fixes it.")


if __name__ == "__main__":
    main()
