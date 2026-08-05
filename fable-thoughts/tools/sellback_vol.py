"""sellback_vol.py — is the mid-flight bid on 1HZ100V fair? The last structural avenue.

WHERE WE ARE
Linearity (E[X+Y] = E[X]+E[Y] under any dependence) closes every hold-to-expiry combination
at once: EV is the sum of the parts, every part carries a house margin, so every combination
is negative. The sole exception is EARLY CLOSE, where the payoff depends on when you act.

sell_scan.py mapped is_valid_to_sell. Findings:
  - Step Index: NOT sellable at any duration -> closed by linearity
  - JD100 / 1HZ100V at 1 TICK: flagged sellable, but this is an artifact. A 1-tick contract
    expires in a second and we polled 1.2s after buying, so the flag catches the settlement
    moment (bids 1.92 / 1.81 = already final value). No window to act in.
  - 1HZ100V CALL at 60s, 5m, 30m: GENUINELY sellable mid-flight. This is the live case.
  - MULT*: sellable, but the paired identity already kills it (long+short = -2*commission)
  - ACCU: correctly priced (G 0.9935-0.9979 from Deriv's own ticks_stayed_in)

THE PROBLEM 1HZ100V POSES
It is not a fixed-step lattice, so sellback_test.py's exact binomial does not apply. But
fair value is still measurable without any model: for a CALL currently d pips above entry
with k ticks remaining,

    P(win) = P(S_k > -d),  S_k = the k-tick displacement

and the distribution of S_k is directly observable from tick history. No Black-Scholes, no
volatility assumption — just the empirical CDF of k-step moves.

    fair_bid = P(win) * payout

WHAT WE ARE HUNTING
Not the average spread — there will be one, and the entry bids (~0.92 on a $1 stake against
a fair value near 0.96) suggest roughly 4%. We want STATES where bid/fair > 1, i.e. their
model overvalues. Those would be: buy, wait for the state, sell. Most likely places are deep
in-the-money or very near expiry, where a continuous approximation misprices the tail.

If bid/fair is a flat constant across all (d, k), they price it correctly and the structural
search is complete.

DEMO ONLY. Sells nothing unless --sell.

Run: python3 sellback_vol.py --duration 5 --unit m --rounds 6
"""
import argparse, math, time
import numpy as np
from deriv_api import DerivWS
from derivfetch import fetch_ticks


def build_displacement_cdf(v, ks):
    """Empirical distribution of k-tick displacements, for each k in ks."""
    out = {}
    for k in ks:
        if k <= 0 or k >= len(v):
            continue
        d = (v[k:] - v[:-k]).astype(np.int64)
        out[k] = np.sort(d)
    return out


def p_win_empirical(cdf, k, d):
    """P(S_k > -d) from the empirical displacement distribution."""
    if k <= 0:
        return 1.0 if d > 0 else 0.0
    arr = cdf.get(k)
    if arr is None:
        ks = sorted(cdf)
        if not ks:
            return float("nan")
        k = min(ks, key=lambda x: abs(x - k))
        arr = cdf[k]
    idx = np.searchsorted(arr, -d, side="right")
    return 1.0 - idx / len(arr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="1HZ100V")
    ap.add_argument("--duration", type=int, default=5)
    ap.add_argument("--unit", default="m", choices=["s", "m"])
    ap.add_argument("--stake", type=float, default=1.0)
    ap.add_argument("--rounds", type=int, default=6)
    ap.add_argument("--poll", type=float, default=3.0)
    ap.add_argument("--hist", type=int, default=200000)
    ap.add_argument("--sell", action="store_true")
    ap.add_argument("--out", default="../results/sellback_vol.md")
    a = ap.parse_args()

    total_ticks = a.duration * (60 if a.unit == "m" else 1)

    pub = DerivWS(token="")
    print(f"fetching {a.hist} ticks of {a.symbol} to measure fair value...")
    t, p, pip = fetch_ticks(pub, a.symbol, a.hist)
    v = np.round(p * (10 ** pip)).astype(np.int64)
    print(f"  building empirical displacement CDFs up to k={total_ticks}")
    ks = sorted({max(1, int(round(total_ticks * f)))
                 for f in np.linspace(0.02, 1.0, 45)})
    cdf = build_displacement_cdf(v, ks)
    print(f"  {len(cdf)} horizons, {len(v)} ticks\n")

    q = pub.call({"proposal": 1, "amount": a.stake, "basis": "stake",
                  "contract_type": "CALL", "currency": "USD",
                  "underlying_symbol": a.symbol, "duration": a.duration,
                  "duration_unit": a.unit})
    if "proposal" not in q:
        print("no proposal:", q.get("error", {}).get("message")); return
    M = float(q["proposal"]["payout"]) / a.stake
    print(f"payout {M:.4f}, contract spans ~{total_ticks} ticks")
    pub.close()

    tr = DerivWS()
    acct = tr.account or {}
    if acct.get("account_type") != "demo":
        print(f"NOT demo ({acct.get('account_type')}) — refusing."); return
    print(f"[demo {acct.get('account_id')}] buying {a.rounds} CALLs\n")

    obs = []
    for i in range(a.rounds):
        b = tr.call({"buy": 1, "price": round(a.stake * 5, 2), "parameters": dict(
            amount=a.stake, basis="stake", contract_type="CALL", currency="USD",
            underlying_symbol=a.symbol, duration=a.duration, duration_unit=a.unit)})
        if "buy" not in b:
            print(f"  [{i+1}] buy error: {b.get('error',{}).get('message','')[:60]}")
            time.sleep(1); continue
        cid = b["buy"]["contract_id"]
        pay = float(b["buy"]["payout"])
        entry = None
        t0 = time.time()
        nq = 0
        while time.time() - t0 < total_ticks + 30:
            c = tr.open_contract(cid).get("proposal_open_contract", {})
            if not c:
                time.sleep(a.poll); continue
            if c.get("is_sold") or c.get("status") in ("won", "lost"):
                break
            es, cs, bid = c.get("entry_spot"), c.get("current_spot"), c.get("bid_price")
            et, ct_ = c.get("entry_spot_time"), c.get("current_spot_time")
            if None in (es, cs, bid, et, ct_):
                time.sleep(a.poll); continue
            if entry is None:
                entry = float(es)
                if i == 0:
                    print(f"  is_valid_to_sell = {c.get('is_valid_to_sell')}")
            d = int(round((float(cs) - entry) * 10 ** pip))
            used = int(ct_) - int(et)
            k = total_ticks - used
            if k <= 0:
                break
            pw = p_win_empirical(cdf, k, d)
            fair = pw * pay
            bid = float(bid)
            if fair > 1e-9 and np.isfinite(pw):
                obs.append(dict(rnd=i+1, d=d, k=k, pw=pw, fair=fair, bid=bid,
                                ratio=bid/fair))
                nq += 1
                if a.sell and bid / fair > 1.0:
                    s = tr.call({"sell": cid, "price": 0})
                    if "sell" in s:
                        print(f"  [{i+1}] SOLD bid {bid:.4f} vs fair {fair:.4f} "
                              f"ratio {bid/fair:.4f}")
                        break
            time.sleep(a.poll)
        print(f"  [{i+1}/{a.rounds}] {nq} quotes")

    if not obs:
        print("\nno observations"); return

    r = np.array([o["ratio"] for o in obs])
    print(f"\n{'='*74}")
    print("BID vs EMPIRICAL FAIR VALUE")
    print("=" * 74)
    print(f"  {len(obs)} quotes   mean bid/fair {r.mean():.4f}   sd {r.std():.4f}")
    print(f"  min {r.min():.4f}   max {r.max():.4f}")
    print(f"  implied sell-back spread {(1-r.mean())*100:.2f}%")

    print(f"\n  by time remaining:")
    print(f"  {'k range':>14}{'n':>6}{'mean P(win)':>13}{'mean bid/fair':>15}{'max':>9}")
    edges = np.percentile([o["k"] for o in obs], [0, 20, 40, 60, 80, 100])
    for lo, hi in zip(edges[:-1], edges[1:]):
        g = [o for o in obs if lo <= o["k"] <= hi]
        if len(g) < 3:
            continue
        rr = np.array([o["ratio"] for o in g])
        print(f"  {int(lo):>6}-{int(hi):<7}{len(g):>6}"
              f"{np.mean([o['pw'] for o in g]):>13.4f}{rr.mean():>15.4f}{rr.max():>9.4f}")

    print(f"\n  by moneyness (displacement in pips):")
    print(f"  {'d range':>14}{'n':>6}{'mean P(win)':>13}{'mean bid/fair':>15}{'max':>9}")
    de = np.percentile([o["d"] for o in obs], [0, 20, 40, 60, 80, 100])
    for lo, hi in zip(de[:-1], de[1:]):
        g = [o for o in obs if lo <= o["d"] <= hi]
        if len(g) < 3:
            continue
        rr = np.array([o["ratio"] for o in g])
        print(f"  {int(lo):>+6}/{int(hi):<+7}{len(g):>6}"
              f"{np.mean([o['pw'] for o in g]):>13.4f}{rr.mean():>15.4f}{rr.max():>9.4f}")

    over = [o for o in obs if o["ratio"] > 1.0]
    print(f"\n{'='*74}")
    print("VERDICT")
    print("=" * 74)
    if over:
        print(f"  *** {len(over)}/{len(obs)} quotes ABOVE fair value ***")
        for o in sorted(over, key=lambda x: -x["ratio"])[:10]:
            print(f"    d={o['d']:+5} k={o['k']:>4} P(win)={o['pw']:.4f} "
                  f"fair={o['fair']:.4f} bid={o['bid']:.4f} ratio={o['ratio']:.4f}")
        print("  Check whether these cluster in one (d,k) region. If they do, that is a")
        print("  tradeable state. Re-run with --sell to confirm the fill matches the bid.")
    else:
        print(f"  No quote exceeded fair value. Flat {(1-r.mean())*100:.2f}% spread.")
        print("  Early close is priced. Combined with linearity closing every")
        print("  hold-to-expiry combination, the structural search is COMPLETE:")
        print("  the only route left is a single mispriced contract, which is what")
        print("  the JD100 sigma-decay thesis is.")

    with open(a.out, "w") as f:
        f.write(f"# Sell-back vs empirical fair value — {a.symbol} "
                f"{a.duration}{a.unit}\n\n{len(obs)} quotes, mean bid/fair "
                f"{r.mean():.4f}, spread {(1-r.mean())*100:.2f}%\n\n"
                "| d | k | P(win) | fair | bid | ratio |\n|---|---|---|---|---|---|\n")
        for o in obs:
            f.write(f"| {o['d']:+d} | {o['k']} | {o['pw']:.4f} | {o['fair']:.4f} "
                    f"| {o['bid']:.4f} | {o['ratio']:.4f} |\n")
    print(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
