"""family_matrix.py — which symbols actually offer which contract families?

Established facts, from the docs (legacy-docs.deriv.com/llms.txt indexes every page as .md):
  - Only Ups = RUNHIGH, Only Downs = RUNLOW. Contract types were never wrong.
  - "No payout if any tick falls OR IS EQUAL to any of the previous ticks" -> ties LOSE,
    which confirms surface_scan's strict-inequality rule and its P(runs) measurements.
  - Accumulators are volatility-indices-only, which is why BOOM/CRASH ACCU was a dead end.

What the docs do NOT give is per-symbol availability. That lives only in contracts_for, and
for JD100 it returns ZERO entries for runs / asian / highlowticks / staysinout / endsinout /
touchnotouch / turbos / vanilla. TradingDurationNotAllowed was just the generic rejection.

So the surface_scan backtests for those families describe payoffs that cannot be traded on
JD100. This builds the coverage matrix once so we stop guessing: for every synthetic symbol,
which families exist, with tick-expiry duration ranges.

Read-only, no trades, ~1 min.

Run: python3 family_matrix.py
     python3 family_matrix.py --detail 1HZ100V
"""
import argparse, time
from deriv_api import DerivWS

FAMS = ["digits", "callput", "callputequal", "higherlower", "runs", "asian",
        "highlowticks", "staysinout", "endsinout", "touchnotouch", "reset",
        "lookback", "accumulator", "multiplier", "turbos", "vanilla"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--detail", default=None,
                    help="print full tick-expiry contract table for this symbol")
    a = ap.parse_args()

    ws = DerivWS(token="")
    r = ws.call({"active_symbols": "brief"})
    syms = [s for s in r.get("active_symbols", [])
            if s.get("market") == "synthetic_index"
            and s.get("exchange_is_open", 1)
            and not s.get("is_trading_suspended", 0)]
    names = sorted(s["underlying_symbol"] for s in syms)
    print(f"{len(names)} synthetic symbols\n")

    hdr = "symbol".ljust(12) + "".join(f.replace("highlowticks", "hltick")
                                       .replace("callputequal", "cpeq")
                                       .replace("touchnotouch", "touch")
                                       .replace("staysinout", "stayio")
                                       .replace("endsinout", "endio")
                                       .replace("accumulator", "accu")
                                       .replace("multiplier", "mult")
                                       .replace("higherlower", "hilo")[:6].rjust(8)
                                       for f in FAMS)
    print(hdr)
    print("-" * len(hdr))

    coverage = {}
    for sym in names:
        rr = ws.call({"contracts_for": sym})
        avail = (rr.get("contracts_for") or {}).get("available", [])
        cats = set(c.get("contract_category") for c in avail) - {None}
        coverage[sym] = (cats, avail)
        row = sym.ljust(12) + "".join(("  YES" if f in cats else "    .").rjust(8)
                                      for f in FAMS)
        print(row)
        time.sleep(0.12)

    print()
    print("=" * 70)
    print("FAMILY -> SYMBOLS THAT OFFER IT")
    print("=" * 70)
    for f in FAMS:
        has = [s for s, (c, _) in coverage.items() if f in c]
        print(f"  {f:14} {len(has):>2}  {', '.join(has) if has else '(none)'}")

    if a.detail and a.detail in coverage:
        _, avail = coverage[a.detail]
        print(f"\n{'='*70}")
        print(f"TICK-EXPIRY CONTRACTS ON {a.detail}")
        print(f"{'='*70}")
        print(f"  {'type':20}{'category':16}{'min':>6}{'max':>6}  barriers")
        seen = set()
        for c in avail:
            if str(c.get("expiry_type", "")).lower() != "tick":
                continue
            ct = c.get("contract_type")
            if ct in seen:
                continue
            seen.add(ct)
            b = c.get("barriers")
            bs = f"n={b}" if b not in (None, "", 0) else ""
            print(f"  {str(ct):20}{str(c.get('contract_category')):16}"
                  f"{str(c.get('min_contract_duration')):>6}"
                  f"{str(c.get('max_contract_duration')):>6}  {bs}")

    print("\nNext: for any family with symbols, re-run surface_scan.py on one of those")
    print("symbols. Backtesting a payoff on a symbol that does not offer it is wasted work.")
    ws.close()


if __name__ == "__main__":
    main()
