"""family_probe.py — find the correct API request shape for the untested contract families.

surface_scan.py backtested runs / asian / highlowticks / staysinout / endsinout on real
ticks and got clean empirical probabilities, but EVERY payout came back "--": the proposals
failed and the script swallowed the errors. So we have P_empirical and no price to compare
it against, which is the whole point.

This probe tries multiple request shapes per family and PRINTS THE ERROR when one fails, so
we learn the actual schema instead of guessing. It also dumps contracts_for entries for each
family, which carry the real parameter names, durations and barrier formats.

Why this matters, from surface_scan on 600k JD100 ticks:

    RUNS_UP_5  binary model 0.03125   measured 0.01746   (56% of binary)
    RUNS_UP_7  binary model 0.00781   measured 0.00344   (44% of binary)

Runs are far rarer than a binary model implies because P(tie) = 0.1022. If any runs payout
was set from a binary assumption, the complement is systematically cheap. Cannot confirm
without the payout.

Also unresolved: highlowticks measured 0.309/0.159/0.141/0.151/0.239 across five positions
vs 0.20 uniform. Part of that skew is np.argmax tie-breaking (first occurrence wins), not
market structure. contracts_for should reveal how Deriv resolves a tied high.

Read-only. Places no trades.

Run: python3 family_probe.py
     python3 family_probe.py --symbol JD100 --families runs asian highlowticks
"""
import argparse, json, time
from deriv_api import DerivWS

FAMILIES = ["runs", "asian", "highlowticks", "staysinout", "endsinout",
            "touchnotouch", "turbos", "vanilla"]

# candidate request shapes per family; first that returns a proposal wins
CANDIDATES = {
    "runs": [
        ("RUNHIGH", lambda n: dict(duration=n, duration_unit="t")),
        ("RUNLOW",  lambda n: dict(duration=n, duration_unit="t")),
        ("ONLYUPS", lambda n: dict(duration=n, duration_unit="t")),
        ("ONLYDOWNS", lambda n: dict(duration=n, duration_unit="t")),
    ],
    "asian": [
        ("ASIANU", lambda n: dict(duration=n, duration_unit="t")),
        ("ASIThenD", lambda n: dict(duration=n, duration_unit="t")),
        ("ASIAND", lambda n: dict(duration=n, duration_unit="t")),
    ],
    "highlowticks": [
        ("TICKHIGH", lambda n: dict(duration=n, duration_unit="t", selected_tick=1)),
        ("TICKLOW",  lambda n: dict(duration=n, duration_unit="t", selected_tick=1)),
        ("TICKHIGH", lambda n: dict(duration=n, duration_unit="t", barrier="1")),
    ],
    "staysinout": [
        ("RANGE",   lambda n: dict(duration=n, duration_unit="t",
                                   barrier="+0.10", barrier2="-0.10")),
        ("UPORDOWN", lambda n: dict(duration=n, duration_unit="t",
                                    barrier="+0.10", barrier2="-0.10")),
    ],
    "endsinout": [
        ("EXPIRYRANGE", lambda n: dict(duration=n, duration_unit="t",
                                       barrier="+0.10", barrier2="-0.10")),
        ("EXPIRYMISS",  lambda n: dict(duration=n, duration_unit="t",
                                       barrier="+0.10", barrier2="-0.10")),
    ],
    "touchnotouch": [
        ("ONETOUCH",  lambda n: dict(duration=n, duration_unit="t", barrier="+0.10")),
        ("NOTOUCH",   lambda n: dict(duration=n, duration_unit="t", barrier="+0.10")),
    ],
    "turbos": [
        ("TURBOSLONG",  lambda n: dict(duration=n, duration_unit="t", barrier="-1.00")),
        ("TURBOSSHORT", lambda n: dict(duration=n, duration_unit="t", barrier="+1.00")),
    ],
    "vanilla": [
        ("VANILLALONGCALL", lambda n: dict(duration=n, duration_unit="t", barrier="+0.10")),
        ("VANILLALONGPUT",  lambda n: dict(duration=n, duration_unit="t", barrier="-0.10")),
    ],
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="JD100")
    ap.add_argument("--also", nargs="*", default=["1HZ100V"],
                    help="extra symbols to compare families against")
    ap.add_argument("--families", nargs="+", default=FAMILIES)
    ap.add_argument("--stake", type=float, default=1.0)
    ap.add_argument("--durations", type=int, nargs="+", default=[5])
    a = ap.parse_args()

    ws = DerivWS(token="")

    # ---- 1. what does contracts_for actually say? -----------------------
    print("=" * 78)
    print(f"1. contracts_for {a.symbol} — real parameter shapes")
    print("=" * 78)
    r = ws.call({"contracts_for": a.symbol})
    avail = (r.get("contracts_for") or {}).get("available", [])
    if not avail:
        print("  no contracts_for data:", r.get("error"))
        return

    by_cat = {}
    for c in avail:
        by_cat.setdefault(c.get("contract_category"), []).append(c)

    print(f"  {len(avail)} contract entries, {len(by_cat)} categories")
    print(f"  ALL categories present: {sorted(k for k in by_cat if k)}\n")

    # everything with a TICK expiry — those are the ones we can backtest per-tick
    tick_types = {}
    for c in avail:
        if str(c.get("expiry_type", "")).lower() != "tick":
            continue
        ct = c.get("contract_type")
        lo, hi = c.get("min_contract_duration"), c.get("max_contract_duration")
        tick_types[ct] = (c.get("contract_category"), lo, hi, c)

    print("  TICK-EXPIRY contract types (these are backtestable):")
    print(f"  {'type':20}{'category':16}{'min':>7}{'max':>7}  barriers")
    for ct, (cat, lo, hi, c) in sorted(tick_types.items(), key=lambda x: (x[1][0] or "", x[0])):
        bar = c.get("barriers")
        bs = f"n={bar}" if bar not in (None, "", 0) else ""
        for k in ("barrier", "high_barrier", "low_barrier"):
            if c.get(k) not in (None, ""):
                bs += f" {k}={c[k]}"
        print(f"  {ct:20}{str(cat):16}{str(lo):>7}{str(hi):>7}  {bs}")

    for fam in a.families:
        entries = by_cat.get(fam, [])
        if not entries:
            continue
        print(f"\n--- {fam} ({len(entries)} entries) ---")
        for c in entries[:8]:
            print(f"  type={str(c.get('contract_type')):18} "
                  f"dur={c.get('min_contract_duration')}..{c.get('max_contract_duration')} "
                  f"expiry={c.get('expiry_type')} sentiment={c.get('sentiment')}")
            for k in ("barriers", "barrier", "high_barrier", "low_barrier", "selected_tick"):
                if c.get(k) not in (None, ""):
                    print(f"      {k}: {c[k]}")

    # ---- 2. live proposal probe -----------------------------------------
    print(f"\n{'='*78}")
    print("2. PROPOSAL PROBE — which request shape actually works")
    print(f"{'='*78}")

    working = {}
    for fam in a.families:
        print(f"\n--- {fam} ---")
        # prefer types actually listed in contracts_for
        listed = [c.get("contract_type") for c in by_cat.get(fam, [])]
        listed = [t for t in dict.fromkeys(listed) if t]
        shapes = CANDIDATES.get(fam, [])
        if listed:
            print(f"  contracts_for lists: {listed}")
            extra = [(t, shapes[0][1] if shapes else
                      (lambda n: dict(duration=n, duration_unit="t")))
                     for t in listed]
            shapes = extra + shapes

        for ct, mk in shapes:
            # use the durations contracts_for allows for THIS type, not a guess
            durs = list(a.durations)
            if ct in tick_types:
                _, lo, hi, _ = tick_types[ct]
                try:
                    lo_i, hi_i = int(lo), int(hi)
                    durs = sorted({lo_i, min(hi_i, lo_i + 1), min(hi_i, 5), hi_i})
                except (TypeError, ValueError):
                    pass
            for n in durs:
                req = {"proposal": 1, "amount": a.stake, "basis": "stake",
                       "contract_type": ct, "currency": "USD",
                       "underlying_symbol": a.symbol}
                req.update(mk(n))
                res = ws.call(req)
                if "proposal" in res:
                    p = res["proposal"]
                    pay = float(p["payout"]) / a.stake
                    print(f"  OK  {ct:18} dur={n}  payout {pay:.4f} "
                          f"(breakeven {1/pay*100:.2f}%)")
                    working.setdefault(fam, []).append((ct, n, pay, req))
                    break
                else:
                    e = res.get("error", {})
                    print(f"  --  {ct:18} dur={n}  [{e.get('code')}] "
                          f"{str(e.get('message'))[:70]}")
                time.sleep(0.12)

    # ---- 3. summary ------------------------------------------------------
    print(f"\n{'='*78}")
    print("3. WORKING SHAPES")
    print(f"{'='*78}")
    if not working:
        print("  none. these families may not be tradeable on this symbol despite")
        print("  contracts_for listing them — same pattern as ACCU on BOOM/CRASH.")
    for fam, lst in working.items():
        for ct, n, pay, req in lst:
            print(f"  {fam:14} {ct:18} dur={n} payout={pay:.4f}")
            print(f"       req: {json.dumps({k: v for k, v in req.items() if k != 'proposal'})}")

    # ---- 4. do other symbols carry families this one lacks? -------------
    if a.also:
        print(f"\n{'='*78}")
        print("4. FAMILY COVERAGE BY SYMBOL")
        print(f"{'='*78}")
        base = set(k for k in by_cat if k)
        print(f"  {a.symbol:12} {sorted(base)}")
        for other in a.also:
            r2 = ws.call({"contracts_for": other})
            av2 = (r2.get("contracts_for") or {}).get("available", [])
            cats2 = set(c.get("contract_category") for c in av2) - {None}
            print(f"  {other:12} {sorted(cats2)}")
            extra = cats2 - base
            if extra:
                print(f"     ^ has families {a.symbol} lacks: {sorted(extra)}")
            time.sleep(0.2)

    print("\nNext: wire the working shapes into surface_scan.py's catalog, then")
    print("re-run to get EV = P_empirical * payout - 1 for these families.")
    print("Remember proposals serve the PRE-CUT grid — confirm with --execute.")
    ws.close()


if __name__ == "__main__":
    main()
