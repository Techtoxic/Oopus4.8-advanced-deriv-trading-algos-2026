"""trade_matrix.py — the complete contract-type x symbol matrix, straight from the API.

WHY THIS, AND WHY NOW
info_universe.py measured mutual information across 28 symbols and found two families
carrying far more information than any diffusion model predicts:

    stpRNG   2.321748 bits  = log2(10) - log2(2) exactly. The next digit has TWO possible
             values (d +/- 1). Eight of ten digits are IMPOSSIBLE on the next tick.
    stpRNG2  1.321892      stpRNG3  2.321703
    stpRNG4  1.321903      stpRNG5  0.999998
    BOOM/CRASH  0.0066-0.0617 bits, 54.8-64.5% max win rate

Boom/Crash turned out not to offer digit contracts, so that information is unreachable.

But the dtrader UI, with symbol=stpRNG4 selected, shows a "Digit based" section listing
Matches/Differs, Over/Under and Even/Odd. That may be the platform-wide trade-type list
rather than a symbol-specific one — sell_scan.py previously got "Trading is not offered for"
when buying DIGITOVER on stpRNG (Step 100). But it never tested stpRNG2-5.

If ANY step index offers digit contracts, eight of ten digits are impossible on the next
tick and DIGITDIFF on an impossible digit wins with certainty. That is not an edge, it is a
free contract. The prior is overwhelmingly that Deriv does not offer it — which is exactly
why it takes two minutes to confirm rather than assume.

WHAT THIS BUILDS
The full matrix. Every symbol from active_symbols, every contract type from contracts_for,
including barriers, duration ranges and units. This supersedes family_matrix.py, which was
written before several product changes and before the Boom/Crash and step families were
examined.

Output is organised so the question "which symbols support contract X" and "which contracts
does symbol Y support" are both one lookup, and so any future scan can be pointed at the
right cells instead of probing blindly.

Read-only. No trades.

Run: python3 trade_matrix.py
     python3 trade_matrix.py --grep DIGIT
"""
import argparse, json, time
from collections import defaultdict
from deriv_api import DerivWS


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--grep", default=None,
                    help="only show contract types containing this string")
    ap.add_argument("--pause", type=float, default=0.25)
    ap.add_argument("--out", default="../results/trade_matrix.md")
    ap.add_argument("--json", default="../results/trade_matrix.json")
    a = ap.parse_args()

    ws = DerivWS(token="")
    r = ws.call({"active_symbols": "brief"})
    syms = r.get("active_symbols", [])
    if not syms:
        print("active_symbols failed:", r.get("error", {}).get("message")); return

    # group by market/submarket so the families are visible
    meta = {}
    for s in syms:
        u = s.get("underlying_symbol") or s.get("symbol")
        if not u:
            continue
        meta[u] = dict(
            name=s.get("underlying_symbol_name") or s.get("display_name") or "",
            market=s.get("market") or "",
            sub=s.get("submarket") or "",
            open=s.get("exchange_is_open", 1),
        )
    print(f"{len(meta)} symbols from active_symbols\n")

    matrix = {}
    for i, (sym, m) in enumerate(sorted(meta.items()), 1):
        # CORRECT SHAPE: {"contracts_for": "<symbol>"} and NOTHING else. Adding
        # currency (or underlying_symbol) fails validation outright:
        #   "Input validation failed: Properties not allowed: currency."
        # The first version sent currency and swallowed the error, reporting 89/89
        # failures as if the endpoint were broken. Always print the server's message.
        rr = ws.call({"contracts_for": sym})
        cf = rr.get("contracts_for", {})
        if not cf:
            matrix[sym] = {}
            err = rr.get("error", {}).get("message", "no contracts_for in response")
            print(f"  [{i:>3}/{len(meta)}] {sym:12} FAILED: {err[:56]}")
            time.sleep(a.pause)
            continue
        types = defaultdict(lambda: dict(durations=set(), units=set(), barriers=0))
        for av in cf.get("available", []):
            ct = av.get("contract_type")
            if not ct:
                continue
            e = types[ct]
            mn, mx = av.get("min_contract_duration"), av.get("max_contract_duration")
            if mn:
                e["durations"].add(str(mn))
            if mx:
                e["durations"].add(str(mx))
            if av.get("barriers"):
                e["barriers"] = max(e["barriers"], int(av["barriers"]))
            bc = av.get("barrier_category")
            if bc:
                e["units"].add(bc)
        matrix[sym] = {k: dict(durations=sorted(v["durations"]),
                               units=sorted(v["units"]),
                               barriers=v["barriers"]) for k, v in types.items()}
        print(f"  [{i:>3}/{len(meta)}] {sym:12} {len(types):>3} types  {m['name'][:34]}")
        time.sleep(a.pause)
    ws.close()

    # ---- contract type -> symbols ---------------------------------------
    by_type = defaultdict(list)
    for sym, d in matrix.items():
        for ct in d:
            by_type[ct].append(sym)

    print(f"\n{'='*88}")
    print("CONTRACT TYPE -> SYMBOLS")
    print("=" * 88)
    for ct in sorted(by_type):
        if a.grep and a.grep.upper() not in ct.upper():
            continue
        s = sorted(by_type[ct])
        print(f"\n  {ct}  ({len(s)} symbols)")
        line = "    "
        for x in s:
            if len(line) + len(x) > 84:
                print(line); line = "    "
            line += x + " "
        if line.strip():
            print(line)

    # ---- the specific question -------------------------------------------
    print(f"\n{'='*88}")
    print("DIGIT CONTRACTS ON HIGH-INFORMATION FAMILIES")
    print("=" * 88)
    digit_types = [ct for ct in by_type if ct.startswith("DIGIT")]
    watch = {
        "step": [s for s in matrix if s.startswith("stpRNG")],
        "boom/crash": [s for s in matrix
                       if s.startswith("BOOM") or s.startswith("CRASH")],
        "jump": [s for s in matrix if s.startswith("JD")],
    }
    for fam, members in watch.items():
        print(f"\n  {fam}:")
        for sym in sorted(members):
            has = sorted(ct for ct in matrix.get(sym, {}) if ct.startswith("DIGIT"))
            mark = "  <<< DIGITS OFFERED" if has else ""
            print(f"    {sym:12} {has if has else 'no digit contracts'}{mark}")

    step_with_digits = [s for s in watch["step"]
                        if any(ct.startswith("DIGIT") for ct in matrix.get(s, {}))]

    print(f"\n{'='*88}")
    print("VERDICT")
    print("=" * 88)
    if step_with_digits:
        print(f"  *** DIGIT CONTRACTS OFFERED ON STEP INDICES: {step_with_digits} ***")
        print()
        print("  On a step index the next digit has exactly two possible values (d +/- 1),")
        print("  measured at 2.3219 bits = log2(10) - log2(2). Eight of ten digits are")
        print("  IMPOSSIBLE on the next tick.")
        print()
        print("  IMMEDIATE CHECKS, in this order:")
        print("   1. quote DIGITDIFF on a digit that cannot occur; read the payout")
        print("   2. buy it on DEMO and confirm it settles as a win")
        print("   3. verify the step really is fixed at the moment of trade — a variable")
        print("      step would break the parity and the whole thing")
        print("   4. check duration: parity holds per TICK, so only 1-tick contracts have")
        print("      the two-value property. step_units_scan showed even tick counts")
        print("      reintroduce ties.")
        print()
        print("  If all four hold, this is not an edge, it is a mispriced certainty, and")
        print("  the correct response is to verify it is not a data error before anything")
        print("  else. Deriv pricing a certain outcome at 1.09 would be a serious error,")
        print("  and serious errors are rare.")
    else:
        print("  No step index offers digit contracts. The 2.32 bits measured on stpRNG")
        print("  is real and unreachable — Deriv does not sell the contract that would")
        print("  expose it, exactly as with Boom/Crash.")
        print()
        print("  That pattern is itself the finding: digit contracts are withheld from")
        print("  precisely those symbols whose digit distribution carries information.")
        print("  Where digits ARE offered, the distribution is uniform by construction.")

    with open(a.json, "w") as f:
        json.dump(matrix, f, indent=1, sort_keys=True)
    with open(a.out, "w") as f:
        f.write("# Trade type x symbol matrix\n\n")
        f.write("## contract type -> symbols\n\n")
        for ct in sorted(by_type):
            f.write(f"- **{ct}** ({len(by_type[ct])}): "
                    f"{', '.join(sorted(by_type[ct]))}\n")
        f.write("\n## symbol -> contract types\n\n")
        for sym in sorted(matrix):
            f.write(f"- **{sym}** ({meta.get(sym,{}).get('name','')}): "
                    f"{', '.join(sorted(matrix[sym]))}\n")
    print(f"\nwrote {a.out} and {a.json}")


if __name__ == "__main__":
    main()
