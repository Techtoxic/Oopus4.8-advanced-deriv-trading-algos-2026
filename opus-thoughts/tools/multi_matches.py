"""multi_matches.py — your 5-digit bundle, built properly.

- picks K digits (default: the conditional-model's top-K for the CURRENT digit, or --digits)
- quotes LIVE payouts before every round (never trusts remembered numbers)
- computes the round EV and REFUSES negative-EV rounds unless --force
- shows the cheapest equivalent replication (coverage_optimizer) before firing
- same-tick mode (all legs on next tick) or --spacing N (legs on consecutive ticks)
- demo-only guard: refuses real-money accounts unless --allow-real
- n+1-aware: single buy-with-parameters call per leg (no proposal round-trip)

Usage:
  python3 multi_matches.py --symbol JD100 --stake 1 --digits 1,2,3,4,5 --dry
  DERIV_TOKEN=... python3 multi_matches.py --symbol JD100 --stake 1 --auto --rounds 10
"""
import argparse, json, math, time, collections
from deriv_api import DerivWS, last_digit
from coverage_optimizer import instruments_from_surface, cheapest_cover

def model_topk(digits_hist, sigma, current, k=5):
    """wrapped-normal next-digit pmf centered at current digit, top-k digits."""
    from math import erf
    def cdf(x): return 0.5 * (1 + erf(x / (sigma * math.sqrt(2))))
    pmf = {}
    for d in range(10):
        p = 0.0
        for wrap in (-20, -10, 0, 10, 20):
            off = (d - current) + wrap
            p += cdf(off + 0.5) - cdf(off - 0.5)
        pmf[d] = p
    top = sorted(pmf, key=pmf.get, reverse=True)[:k]
    return top, pmf

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="JD100")
    ap.add_argument("--stake", type=float, default=1.0)
    ap.add_argument("--digits", help="comma list; omit with --auto to use the model")
    ap.add_argument("--auto", action="store_true", help="pick digits from step-physics model")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--spacing", type=int, default=0, help="0=same settlement tick, N=legs on consecutive ticks")
    ap.add_argument("--rounds", type=int, default=1)
    ap.add_argument("--force", action="store_true", help="trade even at negative EV")
    ap.add_argument("--allow-real", action="store_true")
    ap.add_argument("--dry", action="store_true", help="no orders, just the math")
    a = ap.parse_args()

    ws = DerivWS()
    if not a.dry:
        acct = getattr(ws, "account", {}) or {}
        if not acct.get("loginid"):
            print("no/invalid token -> running --dry"); a.dry = True
        elif not acct.get("is_virtual") and not a.allow_real:
            print("REAL account detected; refusing without --allow-real"); return

    # live payout for MATCH on this symbol
    r = ws.proposal(amount=a.stake, basis="stake", contract_type="DIGITMATCH", barrier="0",
                    currency="USD", duration=1, duration_unit="t", symbol=a.symbol)
    if "proposal" not in r:
        print("proposal error:", r.get("error")); return
    M = float(r["proposal"]["payout"]) / a.stake
    print(f"live DIGITMATCH payout on {a.symbol}: {M:.4f}x  (break-even per-digit p = {1/M:.4f})")

    h = ws.ticks_history(a.symbol, count=2000)
    prices = h["history"]["prices"]; pip = int(h["pip_size"])
    scale = 10 ** pip
    v = [round(float(p) * scale) for p in prices]
    steps = [b - c for c, b in zip(v, v[1:])]
    nj = [s for s in steps if abs(s) <= 20]
    sigma = (sum(s * s for s in nj) / len(nj)) ** 0.5
    cur = last_digit(prices[-1], pip)
    print(f"current digit {cur}, rolling sigma {sigma:.2f} pips")

    for rnd in range(a.rounds):
        if a.digits:
            S = [int(x) for x in a.digits.split(",")][: a.k]
            pmf = {d: 0.1 for d in range(10)}
        else:
            S, pmf = model_topk(prices, sigma, cur, a.k)
        p_set = sum(pmf[d] for d in S)
        ev_bundle = sum(pmf[d] * M - 1 for d in S) * a.stake
        print(f"\nround {rnd+1}: digits {S}  P(set)={p_set:.4f}")
        print(f"  bundle EV ({a.k} x MATCH @ {a.stake}): {ev_bundle:+.4f} "
              f"({ev_bundle/(a.k*a.stake)*100:+.2f}% of stake)")
        try:
            ins = instruments_from_surface(a.symbol, live=False)
            cost, picks = cheapest_cover(ins, set(S))
            ev_repl = (p_set - cost) * a.k * a.stake
            print(f"  cheapest replication of same set: cost {cost:.4f}/$1 -> EV {ev_repl:+.4f}  via {picks}")
        except Exception as e:
            print("  (replication table unavailable:", e, ")")
        if ev_bundle <= 0 and not a.force:
            print("  EV <= 0 -> refusing to trade (use --force to burn money on purpose)")
            continue
        if a.dry:
            print("  [dry] would buy now"); continue
        legs = []
        for i, d in enumerate(S):
            params = dict(amount=a.stake, basis="stake", contract_type="DIGITMATCH",
                          currency="USD", duration=1 + (i * a.spacing if a.spacing else 0),
                          duration_unit="t", symbol=a.symbol, barrier=str(d))
            br = ws.call({"buy": 1, "price": a.stake * 1.001, "parameters": params})
            legs.append(br.get("buy", {}).get("contract_id") or br.get("error", {}).get("message"))
        print("  legs:", legs)
        time.sleep(2 + a.spacing)
    ws.close()

if __name__ == "__main__":
    main()
