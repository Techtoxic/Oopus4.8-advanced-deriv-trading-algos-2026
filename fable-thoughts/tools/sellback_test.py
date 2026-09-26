"""sellback_test.py — is Deriv's early-close price fair? The one non-linear avenue left.

WHY THIS IS THE LAST STRUCTURAL AVENUE
Expectation is linear: E[X+Y] = E[X]+E[Y] for ANY dependence. So every hold-to-expiry
combination — different durations, symbols, staggered entries, N legs — has EV equal to the
sum of the individual EVs. Every Step contract carries a 2.35%-4.57% house margin, so every
combination is negative. That single argument closes the entire hedging family.

Linearity assumes FIXED payoffs. Early close breaks that: the payoff becomes a function of
when you act, so the algebra no longer forces the result. It is the only avenue on this
platform that the linearity argument does not automatically kill, and it is untested here.

WHY STEP INDEX IS THE RIGHT PLACE TO TEST IT
The process is a symmetric +/-1 walk with a fixed step. So the fair value of an OPEN
contract is exactly computable — no model, just binomial arithmetic.

    d = current displacement from entry, in steps
    k = intervals remaining
    CALL wins iff d + S_k > 0, where S_k is a sum of k independent +/-1 steps

    P(S_k = j) = C(k, (k+j)/2) / 2^k   for j with the same parity as k

    fair_bid = P(win) * payout * stake

Their quoted bid_price divided by that fair value gives the sell-back spread directly.

WHAT WE ARE HUNTING
Not "is there a spread" — there will be. We want STATES where the spread goes NEGATIVE,
i.e. their model OVERVALUES the contract. Buy, wait for that state, sell. Their pricing has
to handle parity (an odd k can never tie) and absorbing-ish regions (|d| > k means already
decided). Those are exactly the places a continuous approximation breaks on a lattice.

If bid/fair is a flat constant across all (d, k), they are computing the binomial exactly
and this closes too.

DEMO ONLY. Buys real contracts and polls their bid price; sells nothing unless --sell.

Run: python3 sellback_test.py --rounds 12 --duration 20 --unit t
     python3 sellback_test.py --rounds 8 --duration 30 --unit s
"""
import argparse, math, time
import numpy as np
from deriv_api import DerivWS


def p_walk(k, j):
    """P(sum of k +/-1 steps == j). Zero unless j matches k's parity and |j| <= k."""
    if k < 0 or abs(j) > k or ((k + j) % 2):
        return 0.0
    return math.comb(k, (k + j) // 2) / 2 ** k


def p_call_win(d, k):
    """P(final displacement > 0 | currently d, k steps left)."""
    if k <= 0:
        return 1.0 if d > 0 else 0.0
    return sum(p_walk(k, j) for j in range(-k, k + 1) if d + j > 0)


def p_put_win(d, k):
    if k <= 0:
        return 1.0 if d < 0 else 0.0
    return sum(p_walk(k, j) for j in range(-k, k + 1) if d + j < 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="stpRNG")
    ap.add_argument("--duration", type=int, default=60)
    ap.add_argument("--unit", default="s", choices=["t", "s"],
                    help="ticks cap at 10 and are likely unsellable; use seconds")
    ap.add_argument("--stake", type=float, default=1.0)
    ap.add_argument("--rounds", type=int, default=12)
    ap.add_argument("--poll", type=float, default=1.0)
    ap.add_argument("--sell", action="store_true",
                    help="actually sell when bid/fair exceeds --sell-thresh")
    ap.add_argument("--sell-thresh", type=float, default=1.0)
    ap.add_argument("--out", default="../results/sellback_test.md")
    a = ap.parse_args()

    pub = DerivWS(token="")
    h = pub.call({"ticks_history": a.symbol, "count": 400, "end": "latest",
                  "style": "ticks"})
    pip = int(h.get("pip_size", 1))
    ps = [float(x) for x in h["history"]["prices"]]
    v = np.round(np.array(ps) * 10 ** pip).astype(np.int64)
    step = int(np.median(np.abs(np.diff(v))))
    print(f"{a.symbol}: pip 1e-{pip}, |step| {step} pips")

    # intervals the contract will span
    K = a.duration if a.unit == "t" else a.duration - 1
    print(f"duration {a.duration}{a.unit} -> {K} intervals "
          f"({'odd, tie-free' if K % 2 else 'even, ties possible'})")

    if a.unit == "t" and not (1 <= a.duration <= 10):
        print(f"tick durations are 1-10 only (got {a.duration}). Use --unit s."); return
    if a.unit == "s" and not (15 <= a.duration <= 60):
        print(f"second durations are 15-60 (got {a.duration})."); return

    q = pub.call({"proposal": 1, "amount": a.stake, "basis": "stake",
                  "contract_type": "CALL", "currency": "USD",
                  "underlying_symbol": a.symbol, "duration": a.duration,
                  "duration_unit": a.unit})
    if "proposal" not in q:
        print("no proposal:", q.get("error", {}).get("message")); return
    M = float(q["proposal"]["payout"]) / a.stake
    print(f"payout {M:.4f}  (fair for tie-free is 2.0000)\n")
    pub.close()

    tr = DerivWS()
    acct = tr.account or {}
    if acct.get("account_type") != "demo":
        print(f"NOT demo ({acct.get('account_type')}) — refusing."); return
    print(f"[demo {acct.get('account_id')}]  buying {a.rounds} CALLs, "
          f"polling bid vs exact fair value\n")

    obs = []
    for i in range(a.rounds):
        b = tr.call({"buy": 1, "price": round(a.stake * 5, 2), "parameters": dict(
            amount=a.stake, basis="stake", contract_type="CALL", currency="USD",
            underlying_symbol=a.symbol, duration=a.duration, duration_unit=a.unit)})
        if "buy" not in b:
            print(f"  [{i+1}] buy error: {b.get('error', {}).get('message','')[:60]}")
            time.sleep(1); continue
        cid = b["buy"]["contract_id"]
        pay = float(b["buy"]["payout"])
        entry = None
        sold = False
        t0 = time.time()
        while time.time() - t0 < (K + 15) * (1 if a.unit == "t" else 1) + 20:
            c = tr.open_contract(cid).get("proposal_open_contract", {})
            if not c:
                time.sleep(a.poll); continue
            if c.get("is_sold") or c.get("status") in ("won", "lost"):
                break
            es, cs = c.get("entry_spot"), c.get("current_spot")
            bid = c.get("bid_price")
            if es is None or cs is None or bid is None:
                time.sleep(a.poll); continue
            if entry is None:
                entry = float(es)
                sellable = c.get("is_valid_to_sell")
                if i == 0:
                    print(f"  is_valid_to_sell = {sellable}")
                    if sellable in (0, False):
                        print("  *** this contract cannot be sold early — early close is")
                        print("      not offered here, so there is no non-linearity to test.")
            d_pips = round((float(cs) - entry) * 10 ** pip)
            d = int(round(d_pips / step))
            et, ct_ = c.get("entry_spot_time"), c.get("current_spot_time")
            elapsed = int(ct_) - int(et) if (et and ct_) else None
            if elapsed is None:
                time.sleep(a.poll); continue
            used = elapsed if a.unit == "s" else elapsed  # 1s per tick on stpRNG
            k = K - used
            if k < 0:
                break
            pw = p_call_win(d, k)
            fair = pw * pay
            bid = float(bid)
            ratio = bid / fair if fair > 1e-9 else float("nan")
            obs.append(dict(rnd=i + 1, d=d, k=k, pw=pw, fair=fair, bid=bid,
                            ratio=ratio, sellable=c.get('is_valid_to_sell')))
            if a.sell and fair > 1e-9 and ratio > a.sell_thresh:
                s = tr.call({"sell": cid, "price": 0})
                if "sell" in s:
                    print(f"  [{i+1}] SOLD at bid {bid:.4f} vs fair {fair:.4f} "
                          f"(ratio {ratio:.4f})")
                    sold = True
                    break
            time.sleep(a.poll)
        if not sold:
            print(f"  [{i+1}/{a.rounds}] done, {len([o for o in obs if o['rnd']==i+1])} quotes")
        time.sleep(0.5)

    if not obs:
        print("\nno observations"); return

    print(f"\n{'='*78}")
    print("BID vs EXACT FAIR VALUE")
    print("=" * 78)
    valid = [o for o in obs if o["fair"] > 1e-9 and np.isfinite(o["ratio"])]
    r = np.array([o["ratio"] for o in valid])
    ns = sum(1 for o in valid if o.get("sellable") in (1, True))
    print(f"  quotes flagged sellable: {ns}/{len(valid)}")
    print(f"  {len(valid)} quotes   mean bid/fair {r.mean():.4f}   "
          f"sd {r.std():.4f}   min {r.min():.4f}   max {r.max():.4f}")
    print(f"  implied sell-back spread: {(1-r.mean())*100:.2f}%")

    print(f"\n  by intervals remaining:")
    print(f"  {'k':>4}{'n':>6}{'mean P(win)':>13}{'mean bid/fair':>15}{'min':>9}{'max':>9}")
    for k in sorted({o["k"] for o in valid}):
        g = [o for o in valid if o["k"] == k]
        rr = np.array([o["ratio"] for o in g])
        print(f"  {k:>4}{len(g):>6}{np.mean([o['pw'] for o in g]):>13.4f}"
              f"{rr.mean():>15.4f}{rr.min():>9.4f}{rr.max():>9.4f}")

    over = [o for o in valid if o["ratio"] > 1.0]
    print(f"\n{'='*78}")
    print("VERDICT")
    print("=" * 78)
    if over:
        print(f"  *** {len(over)} quotes ABOVE fair value ***")
        for o in sorted(over, key=lambda x: -x["ratio"])[:10]:
            print(f"    d={o['d']:+3} k={o['k']:>3} P(win)={o['pw']:.4f} "
                  f"fair={o['fair']:.4f} bid={o['bid']:.4f} ratio={o['ratio']:.4f}")
        print("  If these cluster in a specific (d,k) region, that is a tradeable state:")
        print("  buy, wait for it, sell. Verify with --sell before believing it.")
    else:
        print(f"  No quote exceeded fair value. Sell-back carries a flat "
              f"{(1-r.mean())*100:.2f}% spread.")
        print("  Early close is priced. The non-linearity does not create edge, and")
        print("  with linearity closing every hold-to-expiry combination, this closes")
        print("  the structural search on Step Index entirely.")

    with open(a.out, "w") as f:
        f.write(f"# Sell-back vs exact fair value — {a.symbol} {a.duration}{a.unit}\n\n"
                f"{len(valid)} quotes, mean bid/fair {r.mean():.4f}, "
                f"spread {(1-r.mean())*100:.2f}%\n\n"
                "| d | k | P(win) | fair | bid | ratio |\n|---|---|---|---|---|---|\n")
        for o in valid:
            f.write(f"| {o['d']:+d} | {o['k']} | {o['pw']:.4f} | {o['fair']:.4f} | "
                    f"{o['bid']:.4f} | {o['ratio']:.4f} |\n")
    print(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
