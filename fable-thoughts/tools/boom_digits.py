"""boom_digits.py — are digit contracts offered on Boom/Crash, and at what payout?

WHY THIS IS THE ONLY QUESTION LEFT
info_universe.py measured mutual information across 28 symbols. Two families carry far more
information than the wrapped normal predicts:

  STEP indices   MI up to 2.3219 bits = log2(10) - log2(2), the exact signature of a
                 process whose next digit has only two possible values (d +/- 1). Real, and
                 already known from the parity work. But sell_scan.py recorded
                 "stpRNG DIGITOVER: Trading is not offered for" — Deriv does not offer digit
                 contracts on step indices at all. They know.

  BOOM/CRASH     BOOM300N 0.0617 bits -> 64.52% max win rate
                 CRASH1000 0.0364 -> 61.18%
                 CRASH500 0.0357 -> 61.08%
                 BOOM500 0.0153 -> 57.27%
                 against a 55.56% breakeven at payout 1.80.

The mechanism is not mysterious. Boom and Crash are ENGINEERED asymmetric: many small moves
in one direction, a rare large spike in the other. The step distribution is skewed and
peaked, so the digit offset concentrates. The wrapped normal underpredicts because the
distribution is not normal. This is the same physics as JD100's low-sigma concentration
arriving by a different route.

So the whole question reduces to: DOES DERIV OFFER DIGIT CONTRACTS ON BOOM/CRASH?

The session note for commit 8ee21a8 recorded that CALL/PUT buys were rejected 0/10 on these
symbols and that only ACCU and MULT are offered. But DIGIT contracts were never probed.
digit_universe.py swept 17 symbols and Boom/Crash were not among them.

WHAT THIS DOES
  1. contracts_for on each Boom/Crash symbol — what is actually listed
  2. proposal for every digit contract type
  3. EXECUTED buy on demo for anything quoted, because the proposal endpoint on this book
     has overstated payouts by up to 25%
  4. the measured win rate from ticks, at the executed payout, per entry digit
  5. Wilson 99% bound, and a split-half check, before any conclusion

If digit contracts are not offered, this closes in thirty seconds and the information is
unreachable — the same outcome as the step indices.

If they ARE offered near 1.80-1.95, then a 61-64% information ceiling against a 55.56%
breakeven is the largest gap this project has ever measured, and the next step is a careful
out-of-sample validation rather than excitement.

DEMO ONLY for the execution step.

Run: python3 boom_digits.py
     python3 boom_digits.py --symbols BOOM300N CRASH500 --execute
"""
import argparse, math, time
import numpy as np
from deriv_api import DerivWS
from derivfetch import fetch_ticks, contiguous_pairs, native_interval, wilson

DEFAULT = ["BOOM300N", "BOOM500", "BOOM1000", "CRASH300N", "CRASH500", "CRASH1000"]
DIGIT_TYPES = [("DIGITOVER", "4"), ("DIGITUNDER", "5"), ("DIGITMATCH", "0"),
               ("DIGITDIFF", "0"), ("DIGITEVEN", None), ("DIGITODD", None)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+", default=DEFAULT)
    ap.add_argument("--ticks", type=int, default=300000)
    ap.add_argument("--stake", type=float, default=10.0)
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--out", default="../results/boom_digits.md")
    a = ap.parse_args()

    ws = DerivWS(token="")

    # ---- 1. what is actually offered ------------------------------------
    print("=" * 84)
    print("1. CONTRACTS OFFERED")
    print("=" * 84)
    offered = {}
    for sym in a.symbols:
        r = ws.call({"contracts_for": sym, "currency": "USD"})
        c = r.get("contracts_for", {})
        if not c:
            print(f"  {sym:11} contracts_for failed: "
                  f"{r.get('error', {}).get('message', '')[:50]}")
            continue
        types = sorted({x.get("contract_type") for x in c.get("available", [])})
        digits = [t for t in types if t and t.startswith("DIGIT")]
        offered[sym] = digits
        print(f"  {sym:11} {len(types)} types; DIGIT types: "
              f"{digits if digits else 'NONE'}")
        time.sleep(0.2)

    live = [s for s, d in offered.items() if d]
    if not live:
        print(f"\n{'='*84}")
        print("VERDICT")
        print("=" * 84)
        print("  No digit contracts offered on any Boom/Crash symbol.")
        print("  The information measured by info_universe.py is real but UNREACHABLE,")
        print("  exactly as with the step indices. Deriv does not sell the contract that")
        print("  would expose it.")
        print()
        print("  That is itself informative: they appear to withhold digit contracts")
        print("  precisely on the symbols whose digit distribution is concentrated.")
        ws.close(); return

    # ---- 2. quoted payouts ------------------------------------------------
    print(f"\n{'='*84}")
    print("2. QUOTED PAYOUTS (proposals — verified by execution in step 3)")
    print("=" * 84)
    quotes = {}
    for sym in live:
        for ct, bar in DIGIT_TYPES:
            if ct not in offered[sym]:
                continue
            req = {"proposal": 1, "amount": a.stake, "basis": "stake",
                   "contract_type": ct, "currency": "USD",
                   "underlying_symbol": sym, "duration": 1, "duration_unit": "t"}
            if bar is not None:
                req["barrier"] = bar
            r = ws.call(req)
            if "proposal" in r:
                M = float(r["proposal"]["payout"]) / a.stake
                quotes[(sym, ct, bar)] = M
                print(f"  {sym:11}{ct + (bar or ''):14} payout {M:.4f}  "
                      f"breakeven {100/M:.2f}%")
            time.sleep(0.1)

    # ---- 3. measured win rate from ticks ----------------------------------
    print(f"\n{'='*84}")
    print("3. MEASURED WIN RATE FROM TICKS, PER ENTRY DIGIT")
    print("=" * 84)
    rows = []
    for sym in live:
        try:
            t, p, pip = fetch_ticks(ws, sym, a.ticks, verbose=False, strict=False)
        except Exception as e:
            print(f"  {sym:11} fetch failed: {str(e)[:50]}"); continue
        v = np.round(p * (10 ** pip)).astype(np.int64)
        iv = native_interval(t)
        cg = contiguous_pairs(t, v, iv)
        dig = (v % 10).astype(int)
        ent = dig[:-1][cg]
        nxt = dig[1:][cg]
        n = len(ent)
        half = n // 2
        print(f"\n  {sym} ({n} contiguous pairs, {iv}s)")
        for (s2, ct, bar), M in quotes.items():
            if s2 != sym:
                continue
            def wins(e_arr, n_arr):
                if ct == "DIGITOVER":
                    return n_arr > int(bar)
                if ct == "DIGITUNDER":
                    return n_arr < int(bar)
                if ct == "DIGITMATCH":
                    return n_arr == int(bar)
                if ct == "DIGITDIFF":
                    return n_arr != int(bar)
                if ct == "DIGITEVEN":
                    return n_arr % 2 == 0
                return n_arr % 2 == 1
            best = None
            for c in range(10):
                m = ent == c
                if m.sum() < 2000:
                    continue
                w = wins(ent[m], nxt[m])
                k, nn = int(w.sum()), int(m.sum())
                pw = k / nn
                lo, _ = wilson(k, nn)
                # split half
                m1 = m[:half]; m2 = m[half:]
                p1 = wins(ent[:half][m1], nxt[:half][m1]).mean() if m1.sum() > 500 else np.nan
                p2 = wins(ent[half:][m2], nxt[half:][m2]).mean() if m2.sum() > 500 else np.nan
                if best is None or lo > best[3]:
                    best = (pw, c, nn, lo, p1, p2)
            if best is None:
                continue
            pw, c, nn, lo, p1, p2 = best
            ev, evlo = pw * M - 1, lo * M - 1
            flag = "  <<<" if evlo > 0 else ""
            print(f"    {ct + (bar or ''):14} entry={c} n={nn:>6} p={pw:.5f} "
                  f"M={M:.4f} EV={ev*100:+.2f}% 99%low={evlo*100:+.2f}% "
                  f"h1={p1:.4f} h2={p2:.4f}{flag}")
            rows.append((sym, ct, bar, c, nn, pw, M, ev, evlo, p1, p2))
        time.sleep(1.0)

    # ---- 4. execute -------------------------------------------------------
    cands = [r for r in rows if r[8] > 0]
    if a.execute and cands:
        tr = DerivWS()
        acct = tr.account or {}
        if acct.get("account_type") != "demo":
            print(f"\n  NOT demo ({acct.get('account_type')}) — skipping execution")
        else:
            print(f"\n{'='*84}")
            print(f"4. EXECUTED PAYOUTS [demo {acct.get('account_id')}]")
            print("=" * 84)
            for sym, ct, bar, c, nn, pw, M, ev, evlo, p1, p2 in cands[:8]:
                par = dict(amount=a.stake, basis="stake", contract_type=ct,
                           currency="USD", underlying_symbol=sym,
                           duration=1, duration_unit="t")
                if bar is not None:
                    par["barrier"] = bar
                b = tr.call({"buy": 1, "price": round(a.stake * 30, 2),
                             "parameters": par})
                if "buy" in b:
                    bp = float(b["buy"].get("buy_price") or a.stake)
                    Me = float(b["buy"]["payout"]) / bp
                    lo_ = (pw - 2.576 * math.sqrt(pw * (1 - pw) / nn))
                    print(f"  {sym:11}{ct + (bar or ''):14} quoted {M:.4f} -> "
                          f"executed {Me:.4f}  EV {(pw*Me-1)*100:+.2f}% "
                          f"99%low {(lo_*Me-1)*100:+.2f}%"
                          + ("  SURVIVES" if lo_ * Me - 1 > 0 else "  dies on fill"))
                else:
                    print(f"  {sym:11}{ct + (bar or ''):14} buy error: "
                          f"{b.get('error', {}).get('message', '')[:45]}")
                time.sleep(0.4)
    ws.close()

    print(f"\n{'='*84}")
    print("VERDICT")
    print("=" * 84)
    if cands:
        print(f"  {len(cands)} cell(s) with a positive 99% lower bound at quoted payouts.")
        print("  BEFORE ANYTHING ELSE:")
        print("   1. re-run with --execute; proposals on this book have lied by 25%")
        print("   2. compare h1 and h2 — if they diverge, it is not stable")
        print("   3. re-measure on a fresh, non-overlapping tick block")
        print("   4. Boom/Crash spike direction is engineered; check the edge is not")
        print("      simply 'bet against the spike', which reverses when a spike lands")
    else:
        print("  Digit contracts are offered but no cell has a positive 99% lower bound")
        print("  at the quoted payouts. The information is real; the payout already")
        print("  prices it.")

    with open(a.out, "w") as f:
        f.write("# Boom/Crash digit contracts\n\n| symbol | contract | entry | n | p | "
                "payout | EV | 99% low |\n|---|---|---|---|---|---|---|---|\n")
        for sym, ct, bar, c, nn, pw, M, ev, evlo, p1, p2 in rows:
            f.write(f"| {sym} | {ct}{bar or ''} | {c} | {nn} | {pw:.5f} | {M:.4f} "
                    f"| {ev*100:+.2f}% | {evlo*100:+.2f}% |\n")
    print(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
