"""sell_scan.py — where is early close actually offered?

WHY THIS MATTERS
Expectation is linear: E[X+Y] = E[X]+E[Y] under ANY dependence. So every hold-to-expiry
combination has EV equal to the sum of its parts, and since every contract carries a house
margin, every combination is negative. That one argument closes the entire hedging family —
different durations, symbols, staggered entries, N legs.

Linearity assumes FIXED payoffs. EARLY CLOSE breaks it: the payoff becomes a function of
when you act. It is the only structural avenue the argument does not automatically kill.

sellback_test.py found `is_valid_to_sell = 0` on Step Index callput — early close is simply
not offered there, so the avenue is closed for that product without further testing.

This scans which (symbol, contract type, duration) combinations DO allow it. Wherever
is_valid_to_sell = 1, the linearity argument does not apply and the sell-back price needs
checking against fair value. Wherever it is 0, hold-to-expiry linearity closes it outright.

Known from earlier work: multipliers ARE sellable (mult_trunc.py used sell), but the paired
multiplier identity already killed that family — a long+short pair pays exactly -2*commission
because at the instant one leg hits its stop-out the other sits at +SO. Accumulators are
sellable but priced correctly (G = 0.9935-0.9979 from Deriv's own ticks_stayed_in).

So the live question is: is early close offered on DIGITS or CALLPUT anywhere?

Read-only apart from one minimum-stake demo buy per cell, immediately abandoned (not sold,
since the point is to learn whether selling is even permitted).

Run: python3 sell_scan.py
     python3 sell_scan.py --symbols JD100 1HZ100V R_100
"""
import argparse, time
from deriv_api import DerivWS

# (contract_type, duration, unit, extra params)
CELLS = [
    ("CALL", 1, "t", {}),
    ("CALL", 5, "t", {}),
    ("CALL", 10, "t", {}),
    ("CALL", 60, "s", {}),
    ("CALL", 5, "m", {}),
    ("CALL", 30, "m", {}),
    ("DIGITOVER", 1, "t", {"barrier": "4"}),
    ("DIGITOVER", 5, "t", {"barrier": "4"}),
    ("DIGITOVER", 10, "t", {"barrier": "4"}),
    ("DIGITDIFF", 5, "t", {"barrier": "0"}),
    ("MULTUP", 0, None, {"multiplier": 100}),
    ("ACCU", 0, None, {"growth_rate": 0.01}),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+", default=["stpRNG", "JD100", "1HZ100V"])
    ap.add_argument("--stake", type=float, default=1.0)
    a = ap.parse_args()

    tr = DerivWS()
    acct = tr.account or {}
    if acct.get("account_type") != "demo":
        print(f"NOT demo ({acct.get('account_type')}) — refusing."); return
    print(f"[demo {acct.get('account_id')}] probing is_valid_to_sell\n")

    print(f"{'symbol':10}{'contract':12}{'dur':>7}{'sellable':>10}  note")
    print("-" * 62)
    sellable = []
    for sym in a.symbols:
        for ct, dur, unit, extra in CELLS:
            params = dict(amount=a.stake, basis="stake", contract_type=ct,
                          currency="USD", underlying_symbol=sym, **extra)
            if unit:
                params["duration"] = dur
                params["duration_unit"] = unit
            label = f"{dur}{unit}" if unit else "-"

            b = tr.call({"buy": 1, "price": round(a.stake * 30, 2),
                         "parameters": params})
            if "buy" not in b:
                msg = b.get("error", {}).get("message", "")[:26]
                print(f"{sym:10}{ct:12}{label:>7}{'--':>10}  {msg}")
                time.sleep(0.2)
                continue
            cid = b["buy"]["contract_id"]
            time.sleep(1.2)
            c = tr.open_contract(cid).get("proposal_open_contract", {})
            iv = c.get("is_valid_to_sell")
            bid = c.get("bid_price")
            flag = "YES" if iv in (1, True) else "no"
            if iv in (1, True):
                sellable.append((sym, ct, label))
            print(f"{sym:10}{ct:12}{label:>7}{flag:>10}  "
                  f"bid {bid if bid is not None else '--'}")
            # close it if we can, so nothing is left running
            if iv in (1, True):
                tr.call({"sell": cid, "price": 0})
            time.sleep(0.4)
        print()

    print("=" * 62)
    print("VERDICT")
    print("=" * 62)
    if sellable:
        print("  Early close IS offered on:")
        for s, ct, d in sellable:
            print(f"    {s} {ct} {d}")
        print()
        print("  For these, linearity does NOT close the question — the payoff depends")
        print("  on when you act. Check the bid against fair value:")
        print("    sellback_test.py  (Step Index: exact binomial fair value)")
        print()
        print("  Already resolved elsewhere:")
        print("    MULT* — paired long+short pays exactly -2*commission (mult_trunc.py)")
        print("    ACCU  — correctly priced, G 0.9935-0.9979 (accu_verify.py)")
    else:
        print("  Early close is offered NOWHERE in this scan.")
        print("  Every contract here is hold-to-expiry, so E[sum] = sum of E, and every")
        print("  E is negative by the house margin. That closes the entire structural")
        print("  search: no combination of these contracts can be positive EV.")
        print()
        print("  The only remaining route is a SINGLE mispriced contract — which is")
        print("  exactly what the JD100 sigma-decay thesis is.")


if __name__ == "__main__":
    main()
