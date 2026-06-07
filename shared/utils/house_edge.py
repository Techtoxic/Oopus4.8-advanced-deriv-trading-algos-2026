"""
Pull REAL proposal payouts for every digit contract type/barrier and compute
the exact house edge:  edge = 1 - p_win * (payout / stake).
This is the ground-truth EV per contract, straight from Deriv's pricing engine.
"""
import os, sys, json, csv
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from api.deriv_client import DerivClient

STAKE = 10.0
SYMS = ["R_100", "R_10", "1HZ100V"]

def p_win(ct, barrier):
    if ct == "DIGITOVER":  return (9 - barrier) / 10.0
    if ct == "DIGITUNDER": return barrier / 10.0
    if ct == "DIGITEVEN":  return 0.5
    if ct == "DIGITODD":   return 0.5
    if ct == "DIGITMATCH": return 0.1
    if ct == "DIGITDIFF":  return 0.9
    return None

def get_payout(client, sym, ct, barrier):
    req = dict(amount=STAKE, basis="stake", contract_type=ct,
               currency="USD", duration=1, duration_unit="t", symbol=sym)
    if barrier is not None:
        req["barrier"] = str(barrier)
    res = client.proposal(**req)
    if "error" in res:
        return None, res["error"]["message"]
    return res["proposal"]["payout"], None

def main():
    client = DerivClient(); client.connect()
    rows = []
    for sym in SYMS:
        cases = []
        for b in range(0, 9):  cases.append(("DIGITOVER", b))
        for b in range(1, 10): cases.append(("DIGITUNDER", b))
        cases.append(("DIGITEVEN", None)); cases.append(("DIGITODD", None))
        for b in range(0, 10): cases.append(("DIGITMATCH", b))
        cases.append(("DIGITDIFF", 0))
        for ct, b in cases:
            payout, err = get_payout(client, sym, ct, b)
            if err:
                print(f"{sym} {ct} {b}: ERR {err}"); continue
            pw = p_win(ct, b)
            ev = pw * payout - STAKE          # expected profit on $10 stake
            edge = -ev / STAKE                 # house edge fraction
            mult = payout / STAKE
            rows.append(dict(symbol=sym, contract=ct, barrier=b, p_win=pw,
                             stake=STAKE, payout=round(payout,2),
                             payout_mult=round(mult,4),
                             ev_per_10=round(ev,4),
                             house_edge_pct=round(edge*100,3)))
    client.close()

    # save + print
    out_csv = os.path.join(os.path.dirname(__file__), "..", "..", "digits", "data", "house_edge.csv")
    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)

    print(f"{'sym':<8}{'contract':<12}{'bar':<4}{'p_win':<7}{'payout':<8}{'mult':<8}{'edge%':<8}")
    for r in rows:
        print(f"{r['symbol']:<8}{r['contract']:<12}{str(r['barrier']):<4}{r['p_win']:<7}"
              f"{r['payout']:<8}{r['payout_mult']:<8}{r['house_edge_pct']:<8}")
    # summary: min edge per symbol
    print("\n=== LOWEST HOUSE EDGE PER SYMBOL ===")
    for sym in SYMS:
        sr = [r for r in rows if r['symbol']==sym]
        best = min(sr, key=lambda r: r['house_edge_pct'])
        print(f"{sym}: min edge {best['house_edge_pct']}% via {best['contract']} barrier {best['barrier']}")
    print("Saved", out_csv)

if __name__ == "__main__":
    main()
