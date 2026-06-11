"""Cheapest replication of any digit-set view, priced off the LIVE payout surface.
Your '5 digits, one win pays the round' exists as a single cheaper contract — this proves it
per-case with an LP over every available instrument.

Usage:
  python3 coverage_optimizer.py --symbol JD100 --set 5,6,7,8,9
  python3 coverage_optimizer.py --symbol R_100 --set 1,3,5,7,9 --live   (quote live now)
"""
import argparse, json, os
import numpy as np
from scipy.optimize import linprog

def instruments_from_surface(sym, live=False):
    rows = None
    if live:
        from deriv_api import DerivWS
        ws = DerivWS(token="")
        rows = []
        CONTRACTS = ([("DIGITMATCH", d) for d in range(10)] + [("DIGITDIFF", d) for d in range(10)] +
                     [("DIGITOVER", k) for k in range(9)] + [("DIGITUNDER", k) for k in range(1, 10)] +
                     [("DIGITEVEN", None), ("DIGITODD", None)])
        for ctype, b in CONTRACTS:
            req = dict(amount=10, basis="stake", contract_type=ctype, currency="USD",
                       duration=1, duration_unit="t", symbol=sym)
            if b is not None: req["barrier"] = str(b)
            r = ws.proposal(**req)
            if "proposal" in r:
                rows.append(dict(type=ctype, barrier=b, M=float(r["proposal"]["payout"]) / 10))
        ws.close()
    else:
        path = os.path.join(os.path.dirname(__file__), "../results/payout_surface.json")
        surf = json.load(open(path))["surface"][sym]["1"]
        rows = [r for r in surf if "M" in r]
    out = []
    for r in rows:
        t, b, M = r["type"], r.get("barrier"), r["M"]
        if t == "DIGITMATCH": w = {b}
        elif t == "DIGITDIFF": w = set(range(10)) - {b}
        elif t == "DIGITOVER": w = set(range(b + 1, 10))
        elif t == "DIGITUNDER": w = set(range(0, b))
        elif t == "DIGITEVEN": w = {0, 2, 4, 6, 8}
        else: w = {1, 3, 5, 7, 9}
        out.append((f"{t}{b if b is not None else ''}", M, w))
    return out

def cheapest_cover(instruments, target_set, budget_payoff=1.0):
    """min total stake s.t. total payoff >= budget_payoff for every digit in target_set
    (payoff outside S unconstrained >= 0). Returns (cost, picks, worst-case EV vs naive)."""
    S = sorted(target_set)
    A, names = [], []
    for d in S:
        A.append([-(M if d in w else 0.0) for _, M, w in instruments])
    res = linprog(c=[1.0] * len(instruments), A_ub=A, b_ub=[-budget_payoff] * len(S), method="highs")
    picks = [(instruments[j][0], round(x, 4), instruments[j][1]) for j, x in enumerate(res.x) if x > 1e-6]
    return res.fun, picks

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="JD100")
    ap.add_argument("--set", required=True, help="digits, e.g. 5,6,7,8,9")
    ap.add_argument("--live", action="store_true")
    a = ap.parse_args()
    S = {int(x) for x in a.set.split(",")}
    ins = instruments_from_surface(a.symbol, a.live)
    cost, picks = cheapest_cover(ins, S)
    p_hit = len(S) / 10
    match_M = np.mean([M for n, M, w in ins if n.startswith("DIGITMATCH")])
    naive_cost_per_payout = len(S) / match_M
    print(f"target set {sorted(S)} on {a.symbol}")
    print(f"  naive (MATCH x{len(S)}): cost {naive_cost_per_payout:.4f} per $1 locked-if-hit "
          f"-> EV {p_hit - naive_cost_per_payout:+.4f} per $1")
    print(f"  optimal replication:   cost {cost:.4f} per $1 locked-if-hit "
          f"-> EV {p_hit - cost:+.4f} per $1")
    print(f"  portfolio: {picks}")
    print(f"  edge saved vs naive: {(naive_cost_per_payout - cost) / naive_cost_per_payout * 100:.1f}% cheaper")

if __name__ == "__main__":
    main()
