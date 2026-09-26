"""cancel_pair_live.py — does the cancellable multiplier pair actually pay?

WHERE WE ARE
deal_cancel.py priced Deriv's deal-cancellation fee against fair value from measured
volatility. Two findings:

  SINGLE LEG IS PRICED SENSIBLY. Without cancellation your loss is already capped at the
  stake by the stop-out, so the option cannot be worth more than the stake. On 1HZ100V at
  mult 400 / 60m the fee is 7.62 against a ceiling of 10.00 — ratio 0.76. Fine.

  THE PAIR LOOKS POSITIVE. Long + short, both cancellable: cancel the loser for a full
  stake refund, let the winner run. Commission turned out small (0.36-1.46) and does NOT
  kill it. Best cell: 1HZ100V mult 400 60m, gain 36.85 vs cost 18.16, EV +18.69.

WHY THIS MUST BE TESTED LIVE RATHER THAN BELIEVED
The pair number assumes ALL of the following at once, and none is verified:

  1. both MULTUP and MULTDOWN can be open on the same symbol simultaneously
  2. cancellation refunds the FULL stake, not stake minus commission
  3. cancellation genuinely SUSPENDS the stop-out for the whole window — so_x is 4.6 at
     mult 400/60m, meaning the losing leg would otherwise blow through its stop-out more
     than four times over
  4. the winning leg keeps running with no cap while the loser is protected
  5. the fee read from the proposal is what actually gets charged

Every positive result in this project so far has died at exactly this step. The proposal
endpoint on this book has overstated payouts by up to 25%.

WHAT THIS DOES
Opens a real cancellable pair on demo, watches both legs tick by tick, and records:
  - what was actually charged (stake, fee, commission) versus what the proposal said
  - whether the losing leg survives past its stop-out distance
  - both legs' bid prices over the window
  - the realised P&L of cancel-the-loser-keep-the-winner, against the predicted EV

Uses a SHORT window by default so a run completes in minutes. The 60m cells are the
attractive ones, but 5m/15m test the same mechanics for a fraction of the wait.

DEMO ONLY. Refuses to run on a real account.

Run: python3 cancel_pair_live.py --window 5 --mult 400 --rounds 3
     python3 cancel_pair_live.py --window 15 --mult 400 --rounds 2
"""
import argparse, math, time
import numpy as np
from deriv_api import DerivWS


def field(d, *names):
    for n in names:
        if isinstance(d, dict) and d.get(n) is not None:
            return d[n]
    return None


def open_leg(tr, sym, ct, stake, mult, window):
    r = tr.call({"buy": 1, "price": stake * 5, "parameters": dict(
        amount=stake, basis="stake", contract_type=ct, currency="USD",
        underlying_symbol=sym, multiplier=mult,
        cancellation=f"{window}m")})
    if "buy" not in r:
        return None, r.get("error", {}).get("message", "")[:70]
    return r["buy"], None


def snap(tr, cid):
    return tr.open_contract(cid).get("proposal_open_contract", {}) or {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="1HZ100V")
    ap.add_argument("--mult", type=int, default=400)
    ap.add_argument("--window", type=int, default=5)
    ap.add_argument("--stake", type=float, default=10.0)
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--poll", type=float, default=10.0)
    ap.add_argument("--no-cancel", action="store_true",
                    help="observe only; do not actually cancel the loser")
    a = ap.parse_args()

    pub = DerivWS(token="")
    pr = pub.call({"proposal": 1, "amount": a.stake, "basis": "stake",
                   "contract_type": "MULTUP", "currency": "USD",
                   "underlying_symbol": a.symbol, "multiplier": a.mult,
                   "cancellation": f"{a.window}m"})
    if "proposal" not in pr:
        print("no proposal:", pr.get("error", {}).get("message")); return
    P = pr["proposal"]
    q_fee = field(P.get("cancellation") or {}, "ask_price") or 0.0
    q_comm = float(P.get("commission") or 0.0)
    print(f"{a.symbol} mult {a.mult} window {a.window}m stake ${a.stake}")
    print(f"  PROPOSAL: fee {q_fee}, commission {q_comm}, "
          f"ask_price {P.get('ask_price')}")
    print(f"  stop-out distance = 1/{a.mult} = {100/a.mult:.3f}% adverse move")
    pub.close()

    tr = DerivWS()
    acct = tr.account or {}
    if acct.get("account_type") != "demo":
        print(f"NOT demo ({acct.get('account_type')}) — refusing."); return
    bal0 = float(acct.get("balance", 0))
    print(f"  [demo {acct.get('account_id')}] balance {bal0:.2f}\n")

    results = []
    for i in range(a.rounds):
        print("=" * 74)
        print(f"ROUND {i+1}/{a.rounds}")
        print("=" * 74)
        up, err1 = open_leg(tr, a.symbol, "MULTUP", a.stake, a.mult, a.window)
        if err1:
            print(f"  MULTUP failed: {err1}"); break
        time.sleep(0.4)
        dn, err2 = open_leg(tr, a.symbol, "MULTDOWN", a.stake, a.mult, a.window)
        if err2:
            print(f"  MULTDOWN failed: {err2}")
            print("  *** BOTH LEGS CANNOT BE HELD — the pair does not exist. ***")
            tr.call({"sell": up["contract_id"], "price": 0})
            break

        print(f"  opened both legs")
        for lab, b in (("UP", up), ("DOWN", dn)):
            print(f"    {lab:4} id {b['contract_id']} buy_price {b.get('buy_price')} "
                  f"payout {b.get('payout')}")
        paid = float(up.get("buy_price", 0)) + float(dn.get("buy_price", 0))
        print(f"  TOTAL PAID {paid:.2f}   (2 stakes = {2*a.stake:.2f}, "
              f"so implied extra = {paid - 2*a.stake:.2f})")

        t_end = time.time() + a.window * 60
        stopped = {"UP": False, "DOWN": False}
        while time.time() < t_end - 20:
            time.sleep(a.poll)
            cu, cd = snap(tr, up["contract_id"]), snap(tr, dn["contract_id"])
            if not cu or not cd:
                continue
            for lab, c in (("UP", cu), ("DOWN", cd)):
                if c.get("is_sold") or c.get("status") in ("won", "lost"):
                    if not stopped[lab]:
                        stopped[lab] = True
                        print(f"    !! {lab} TERMINATED early "
                              f"(status {c.get('status')}) — stop-out NOT suspended")
            pu, pd = float(cu.get("profit", 0)), float(cd.get("profit", 0))
            rem = int(t_end - time.time())
            print(f"    t-{rem:>4}s  UP {pu:+7.2f}  DOWN {pd:+7.2f}  "
                  f"sum {pu+pd:+7.2f}  "
                  f"valid_to_cancel UP={cu.get('is_valid_to_cancel')} "
                  f"DOWN={cd.get('is_valid_to_cancel')}")
            if all(stopped.values()):
                break

        cu, cd = snap(tr, up["contract_id"]), snap(tr, dn["contract_id"])
        pu, pd = float(cu.get("profit", 0)), float(cd.get("profit", 0))
        loser = ("UP", up, cu) if pu < pd else ("DOWN", dn, cd)
        winner = ("DOWN", dn, cd) if pu < pd else ("UP", up, cu)
        print(f"  loser is {loser[0]} at {float(loser[2].get('profit',0)):+.2f}, "
              f"winner {winner[0]} at {float(winner[2].get('profit',0)):+.2f}")

        if a.no_cancel:
            print("  --no-cancel set; leaving both open")
        else:
            cres = tr.call({"cancel": loser[1]["contract_id"]})
            if "cancel" in cres:
                print(f"  CANCELLED {loser[0]}: refund "
                      f"{cres['cancel'].get('sold_for')} (stake was {a.stake})")
            else:
                print(f"  cancel FAILED: "
                      f"{cres.get('error', {}).get('message','')[:70]}")
            time.sleep(0.5)
            wres = tr.call({"sell": winner[1]["contract_id"], "price": 0})
            if "sell" in wres:
                print(f"  sold winner for {wres['sell'].get('sold_for')}")

        time.sleep(1.5)
        bal = float((tr.account or {}).get("balance", 0)) or None
        results.append(dict(paid=paid, pu=pu, pd=pd,
                            stopped=any(stopped.values())))
        print()

    if results:
        print("=" * 74)
        print("SUMMARY")
        print("=" * 74)
        try:
            bal1 = float(tr.call({"balance": 1}).get("balance", {}).get("balance", bal0))
        except Exception:
            bal1 = bal0
        print(f"  rounds completed {len(results)}")
        print(f"  balance {bal0:.2f} -> {bal1:.2f}   net {bal1-bal0:+.2f}")
        print(f"  rounds where a leg terminated early: "
              f"{sum(r['stopped'] for r in results)}/{len(results)}")
        print()
        print("  predicted EV per round for this cell came from deal_cancel.py.")
        print("  If the realised net is far below it, the pair assumptions are wrong —")
        print("  most likely the stop-out is not suspended, or the refund is not the")
        print("  full stake. The per-round log above shows which.")
    tr.close()


if __name__ == "__main__":
    main()
