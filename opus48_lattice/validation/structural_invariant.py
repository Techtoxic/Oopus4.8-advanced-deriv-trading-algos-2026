"""
structural_invariant.py — exhaust the outcome space and prove the invariant.

The friend asked for "every single possible outcome" and noted outcomes occur in
compound chains. This enumerates, for every digit 0-9, the full resolution of
every REAL last-digit contract.

CORRECTION TO PRIOR FRAMEWORK: the friend/Claude wrote "42 contracts, 20 win /
22 lose per digit." That counts Over 9 and Under 0 as contracts. Both are
impossible (a last digit is never > 9 nor < 0); Deriv does not offer them. The
real tradeable universe is 40 contracts: Match 0-9 (10), Differ 0-9 (10),
Over 0-8 (9), Under 1-9 (9), Even, Odd. Under the real universe the invariant is
20 WIN / 20 LOSE for every digit. The phantom "22 lose" only appears if you add
two bets that always lose and can never be placed.

It also evaluates compound chains using the exact (deterministic) intersection
probability — demonstrating why no chain escapes negative EV on a uniform
generator: the joint win prob is exactly |wins|/10 and each leg still pays below
its own break-even.
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lattice import common


def main(out=None):
    L = []
    p = L.append
    universe = common.contract_universe()
    p("=" * 70)
    p("EXHAUSTIVE OUTCOME SPACE — real last-digit contracts x 10 digits")
    p("=" * 70)
    p(f"Real tradeable universe size: {len(universe)} "
      f"(Match10 + Differ10 + Over0-8=9 + Under1-9=9 + Even + Odd)")
    p("Prior framework said 42 (it added impossible Over9 + Under0). Corrected.")
    p("")
    all_ok = True
    for d in range(10):
        wins = common.winning_contracts_for_digit(d)
        nwin = len(wins)
        nlose = len(universe) - nwin
        ok = (nwin == 20 and nlose == 20)
        all_ok &= ok
        p(f"digit {d}: WIN={nwin:2d} LOSE={nlose:2d}  {'OK' if ok else 'VIOLATION'}")
    p("")
    p(f"STRUCTURAL INVARIANT (every digit -> 20 win / 20 lose of 40 real): "
      f"{'PROVEN' if all_ok else 'FALSE'}")
    p("")
    # Example compound chain the friend gave: Over3 & Under8 & Odd & Differ0
    chain = [("OVER", 3), ("UNDER", 8), ("ODD", None), ("DIFFER", 0)]
    inter = [d for d in range(10)
             if all(common.resolves_win(k, b, d) for (k, b) in chain)]
    p("Compound chain example  Over3 & Under8 & Odd & Differ0:")
    p(f"  wins on digits {inter}  -> joint P = {len(inter)/10:.2f}")
    p("  (matches the friend's hand calc: {5,7}, P=0.20)")
    p("")
    # EV of that chain as equal-stake legs on a UNIFORM generator with ~edge.
    # Each leg pays (1-edge)/q; on uniform p_win=q so each leg EV=-edge<0.
    p("Why no compound chain escapes negative EV on a uniform RNG:")
    p("  joint win prob = |intersection|/10 (exact, deterministic given digit);")
    p("  each leg's payout = (1-edge)/q_leg, and realized p_leg = q_leg, so")
    p("  every leg returns -edge per $1. A sum of negative-EV legs is negative-EV.")
    text = "\n".join(L)
    print(text)
    if out:
        with open(out, "w") as f:
            f.write(text + "\n")
    return all_ok


if __name__ == "__main__":
    rp = os.path.join(os.path.dirname(__file__), "..", "results", "structural_invariant.txt")
    os.makedirs(os.path.dirname(rp), exist_ok=True)
    ok = main(rp)
    sys.exit(0 if ok else 1)
