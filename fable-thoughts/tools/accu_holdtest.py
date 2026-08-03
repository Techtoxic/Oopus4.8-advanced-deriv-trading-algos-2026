"""accu_holdtest.py — measure accumulator survival DIRECTLY. Ground truth, no inference.

Why this exists: both indirect approaches to H5b failed.

  - accu_boom.py assumed i.i.d. per-tick survival -> predicted mean survival ~1031-1149 ticks.
  - The "first-passage on cumulative drift" explanation was FITTED at g=0.05 and then fails
    across the growth-rate range: it predicts mean ~ tsb^2, so a 1.59x spread from g=.01 to
    g=.05, while Deriv's own field shows 3.63x. Off by 2.3x.
  - Deriv's contract_details.ticks_stayed_in cannot be used as ground truth: it contains
    values of 104/98/99 against a stated maximum_ticks of 50, and a 0 for R_100. It is not
    a clean barrier-breach distribution for the current product.

So: stop inferring. Buy contracts on DEMO, never sell, and record how long they actually live.

Decision metric is the hold-to-N EV factor, computed from the empirical survival curve:

    EV_factor(N) = (1+g)^N * S(N)          S(N) = P(survive at least N ticks)

because a surviving contract pays stake*(1+g)^N and a breached one pays 0.

The two hypotheses are far apart, so this needs very few contracts:

    if p_tick = 0.999 (accu_boom):  S(50) = 0.951 -> EV_factor(50) = 10.9  (+990%)
    if mean = 19.16 (ticks_stayed_in): S(50) = 0.068 -> EV_factor(50) = 0.78 (-22%)

~20-30 contracts separates these decisively.

DEMO ONLY. Refuses to run on a real account. Never calls sell.

Run: python3 accu_holdtest.py --symbol BOOM900 --rate 0.05 --n 25
     python3 accu_holdtest.py --symbol BOOM900 --rate 0.01 --n 25 --stake 1
"""
import argparse, json, time
import numpy as np
from deriv_api import DerivWS


def poll_until_dead(tr, cid, timeout=400, interval=0.7, dump_schema=False):
    """Poll a contract until it settles. Returns the final contract dict."""
    t0 = time.time()
    last = {}
    while time.time() - t0 < timeout:
        c = tr.open_contract(cid).get("proposal_open_contract", {})
        if c:
            last = c
            if dump_schema:
                print("\n  --- first contract schema ---")
                for k, v in sorted(c.items()):
                    s = json.dumps(v)[:90] if isinstance(v, (dict, list)) else v
                    print(f"    {k:28} {s}")
                print("  --- end schema ---\n")
                dump_schema = False
            if c.get("is_sold") or c.get("is_expired") or c.get("status") in ("won", "lost"):
                return last
        time.sleep(interval)
    return last


def tick_count(c):
    """Best-effort extraction of how many ticks the contract survived."""
    for k in ("tick_count", "ticks_stayed_in", "current_tick"):
        v = c.get(k)
        if isinstance(v, (int, float)):
            return int(v)
    ts = c.get("tick_stream")
    if isinstance(ts, list) and ts:
        return len(ts) - 1
    et, xt = c.get("entry_tick_time"), c.get("exit_tick_time")
    if et and xt:
        return int(xt - et)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BOOM900")
    ap.add_argument("--rate", type=float, default=0.05)
    ap.add_argument("--stake", type=float, default=1.0)
    ap.add_argument("--n", type=int, default=25)
    ap.add_argument("--out", default="../results/accu_holdtest.csv")
    a = ap.parse_args()

    tr = DerivWS()
    acct = tr.account or {}
    if acct.get("account_type") != "demo":
        print(f"NOT demo ({acct.get('account_type')}) — refusing."); return
    print(f"acct {acct.get('account_id')} bal={acct.get('balance')}")

    r = tr.call({"proposal": 1, "amount": a.stake, "basis": "stake", "contract_type": "ACCU",
                 "currency": "USD", "underlying_symbol": a.symbol, "growth_rate": a.rate})
    cd = r.get("proposal", {}).get("contract_details", {})
    max_ticks = int(cd.get("maximum_ticks", 0) or 0)
    tsb = float(cd.get("tick_size_barrier", 0) or 0)
    print(f"{a.symbol} g={a.rate}  tsb={tsb:.6e}  maximum_ticks={max_ticks}")
    print(f"holding {a.n} contracts to death, never selling\n")

    rows = []
    for i in range(a.n):
        br = tr.call({"buy": 1, "price": round(a.stake * 1.05, 2), "parameters":
                      dict(amount=a.stake, basis="stake", contract_type="ACCU",
                           currency="USD", underlying_symbol=a.symbol,
                           growth_rate=a.rate)})
        if "buy" not in br:
            print(f"  [{i+1}] buy error: {br.get('error', {}).get('message')}")
            time.sleep(2); continue
        cid = br["buy"]["contract_id"]
        c = poll_until_dead(tr, cid, dump_schema=(i == 0))
        if not c:
            print(f"  [{i+1}] timeout"); continue

        n_ticks = tick_count(c)
        profit = float(c.get("profit", 0))
        status = c.get("status")
        hit_max = (max_ticks and n_ticks is not None and n_ticks >= max_ticks)
        rows.append(dict(i=i + 1, ticks=n_ticks, status=status, profit=profit,
                         hit_max=int(bool(hit_max)),
                         entry=c.get("entry_spot"), exit=c.get("exit_spot")))
        print(f"  [{i+1}/{a.n}] ticks={n_ticks} status={status} profit={profit:+.4f}"
              + ("  <== hit max_ticks" if hit_max else ""))

    if not rows:
        print("no data"); return

    ts = np.array([r["ticks"] for r in rows if r["ticks"] is not None], dtype=float)
    if len(ts) == 0:
        print("could not extract tick counts — inspect the schema dump above"); return

    import csv, os
    new = not os.path.exists(a.out)
    with open(a.out, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        if new: w.writeheader()
        w.writerows(rows)

    print(f"\n{'='*66}")
    print(f"n={len(ts)}  mean={ts.mean():.2f}  median={np.median(ts):.1f}  "
          f"min={ts.min():.0f}  max={ts.max():.0f}")
    n_max = sum(r["hit_max"] for r in rows)
    print(f"hit max_ticks: {n_max}/{len(rows)}")
    total = sum(r["profit"] for r in rows)
    print(f"realised PnL holding to death: {total:+.4f} on {len(rows)*a.stake:.2f} staked "
          f"= {total/(len(rows)*a.stake)*100:+.2f}%")

    print(f"\n=== EMPIRICAL SURVIVAL CURVE + HOLD-TO-N EV ===")
    print(f"{'N':>5}{'S(N)':>9}{'(1+g)^N':>10}{'EV factor':>11}{'EV %':>10}")
    best = None
    for N in [1, 2, 3, 5, 10, 15, 20, 30, 40, 50]:
        if max_ticks and N > max_ticks: break
        S = float((ts >= N).mean())
        gr = (1 + a.rate) ** N
        ev = gr * S
        if best is None or ev > best[1]: best = (N, ev)
        print(f"{N:>5}{S:>9.4f}{gr:>10.4f}{ev:>11.4f}{(ev-1)*100:>9.2f}%")

    print(f"\nbest hold: N={best[0]}  EV factor {best[1]:.4f} ({(best[1]-1)*100:+.2f}%)")

    print(f"\n=== HYPOTHESIS TEST ===")
    print(f"  accu_boom predicted mean ~1031-1149 ticks (p_tick 0.999)")
    print(f"  ticks_stayed_in implied mean 19.16 at g=0.05")
    print(f"  MEASURED mean: {ts.mean():.2f}")
    if ts.mean() > 200:
        print("  -> supports accu_boom. Barrier rarely breached. INVESTIGATE FURTHER.")
    elif ts.mean() < 60:
        print("  -> supports ticks_stayed_in. Contracts die fast. G<1, hypothesis dead.")
    else:
        print("  -> between both. Need more contracts / another growth rate.")
    if best[1] > 1.05:
        print(f"  -> hold-to-{best[0]} shows +EV on this sample. Repeat at another rate")
        print(f"     and symbol before believing it; n={len(ts)} is small.")


if __name__ == "__main__":
    main()
