"""accu_canbuy.py — can you actually BUY an accumulator on BOOM/CRASH?

contracts_for lists `accumulator` for all 14 BOOM/CRASH symbols, and the ACCU *proposal*
endpoint returns full contract_details (tick_size_barrier, barriers, maximum_ticks). But the
dtrader UI reportedly only offers multipliers on these symbols.

This repo has already seen the API misreport once: the CALLE/PUTE proposal endpoint quotes
1.9530 while executions fill at 1.800. A proposal returning data is NOT proof a contract is
tradeable.

Only a buy settles it. This attempts exactly one ACCU buy on the test symbol and one on
R_100 as control, then immediately sells whatever fills so nothing is left open.

  BOOM errors + R_100 fills  -> ACCU not offered on BOOM/CRASH. H5b dead, product doesn't exist.
  both error                 -> account/permission issue, not a product issue. Check the message.
  both fill                  -> tradeable. Run accu_holdtest.py for the survival distribution.

DEMO ONLY. Stake $1. Sells immediately.

Run: python3 accu_canbuy.py
     python3 accu_canbuy.py --symbols BOOM900 BOOM1000 CRASH1000 R_100
"""
import argparse, json, time
from deriv_api import DerivWS


def try_buy(tr, sym, rate, stake):
    out = {"symbol": sym}

    p = tr.call({"proposal": 1, "amount": stake, "basis": "stake", "contract_type": "ACCU",
                 "currency": "USD", "underlying_symbol": sym, "growth_rate": rate})
    if "proposal" in p:
        cd = p["proposal"].get("contract_details", {})
        out["proposal"] = "OK"
        out["tsb"] = cd.get("tick_size_barrier")
        out["max_ticks"] = cd.get("maximum_ticks")
        out["spot"] = p["proposal"].get("spot")
    else:
        e = p.get("error", {})
        out["proposal"] = f"ERROR [{e.get('code')}] {e.get('message')}"
        return out

    b = tr.call({"buy": 1, "price": round(stake * 1.10, 2), "parameters":
                 dict(amount=stake, basis="stake", contract_type="ACCU",
                      currency="USD", underlying_symbol=sym, growth_rate=rate)})
    if "buy" in b:
        cid = b["buy"]["contract_id"]
        out["buy"] = "FILLED"
        out["contract_id"] = cid
        out["buy_price"] = b["buy"].get("buy_price")
        time.sleep(2.0)
        s = tr.call({"sell": cid, "price": 0})
        if "sell" in s:
            out["sell"] = f"sold, proceeds {s['sell'].get('sold_for')}"
        else:
            out["sell"] = f"SELL FAILED: {s.get('error', {}).get('message')} (cid {cid})"
    else:
        e = b.get("error", {})
        out["buy"] = f"ERROR [{e.get('code')}] {e.get('message')}"
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+",
                    default=["BOOM900", "BOOM1000", "CRASH1000", "R_100"])
    ap.add_argument("--rate", type=float, default=0.05)
    ap.add_argument("--stake", type=float, default=1.0)
    a = ap.parse_args()

    tr = DerivWS()
    acct = tr.account or {}
    if acct.get("account_type") != "demo":
        print(f"NOT demo ({acct.get('account_type')}) — refusing."); return
    print(f"acct {acct.get('account_id')} bal={acct.get('balance')}  g={a.rate} stake=${a.stake}\n")

    results = []
    for sym in a.symbols:
        print(f"--- {sym} ---")
        r = try_buy(tr, sym, a.rate, a.stake)
        results.append(r)
        for k in ("proposal", "spot", "tsb", "max_ticks", "buy", "buy_price", "sell"):
            if k in r:
                print(f"  {k:12} {r[k]}")
        print()
        time.sleep(0.5)

    print("=" * 66)
    print("VERDICT")
    print("=" * 66)
    filled = [r["symbol"] for r in results if r.get("buy") == "FILLED"]
    failed = [r for r in results if r.get("buy", "").startswith("ERROR")]

    for r in results:
        st = "FILLED" if r.get("buy") == "FILLED" else "REJECTED"
        print(f"  {r['symbol']:12} proposal={'OK' if r.get('proposal')=='OK' else 'ERR':4} buy={st}")

    bc = [r for r in results if r["symbol"].startswith(("BOOM", "CRASH"))]
    ctrl = [r for r in results if not r["symbol"].startswith(("BOOM", "CRASH"))]
    bc_ok = any(r.get("buy") == "FILLED" for r in bc)
    ctrl_ok = any(r.get("buy") == "FILLED" for r in ctrl)

    print()
    if bc and not bc_ok and ctrl_ok:
        print("  ACCU is NOT tradeable on BOOM/CRASH despite contracts_for listing it.")
        print("  -> H5b is dead: the product does not exist on these symbols.")
        print("  -> accu_boom.py measured a barrier for a contract you cannot buy.")
        print("  -> Add ACCU/BOOM to the list of endpoints that misreport (with CALLE/PUTE).")
    elif bc_ok:
        print("  ACCU FILLS on BOOM/CRASH. The product is real and the G>1 question is open.")
        print("  -> Next: python3 accu_holdtest.py --symbol BOOM900 --rate 0.05 --n 25")
    elif not ctrl_ok:
        print("  Nothing filled, including the control. This is an account/permission")
        print("  problem, not a product one. Read the error codes above.")
    if failed:
        print("\n  error messages:")
        for r in failed:
            print(f"    {r['symbol']:12} {r['buy']}")


if __name__ == "__main__":
    main()
