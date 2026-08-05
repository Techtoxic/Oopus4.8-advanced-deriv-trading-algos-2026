"""step_parity_scan.py — does Deriv price the odd/even parity on Step Index?

WHAT WE ESTABLISHED
step_tie.py, 300,000 unique stpRNG ticks: |step| = 1 pip 100%, zero flat ticks, and
P(tie) EXACTLY 0.00000 on every odd tick count. Parity, not statistics — a fixed-step
process cannot return to its start in an odd number of steps.

step_pair_test.py then bought 20 real CALL+PUT pairs at 15s and found:
  - executed payout 2.4600 on both legs (quote was honest here)
  - entry->exit elapsed 14s EVERY round, never 15
  - 7/20 rounds tied exactly (d = +0.0), pair P&L -20.05%/round

THE OFF-BY-ONE
A "15 second" contract spans 14 tick INTERVALS: entry is the tick after purchase, exit is
15 ticks later, leaving 14 gaps. 14 is EVEN, so ties are permitted, and Deriv's 2.46 is
priced for exactly that (implied P(tie) 18.70% vs ~21% measured at 14 ticks).

So the parity insight was right and the seconds->ticks mapping killed it.

THE QUESTION THIS ANSWERS
Duration D seconds -> (D-1) intervals. So D=16 gives 15 intervals, which is ODD, which
means P(tie) = 0 and exactly one leg must win.

  If Deriv still quotes ~2.46 at D=16, the pair returns 2.46 on 2.00 staked: +23% risk
  free, every round, no variance.
  If Deriv quotes ~1.95 at D=16, they have priced parity and the file closes.

Either way we learn it from proposals alone — no money at risk.

This scans payout across durations and looks for ALTERNATION between odd and even
interval counts. Flat payouts across parity = they are not modelling it. Alternating
payouts = they are.

Read-only. Places no trades. Verify any candidate with executed buys before believing it.

Run: python3 step_parity_scan.py
     python3 step_parity_scan.py --symbol stpRNG2 --max-dur 40
"""
import argparse, math, time
from deriv_api import DerivWS


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+",
                    default=["stpRNG", "stpRNG2", "stpRNG3", "stpRNG4", "stpRNG5"])
    ap.add_argument("--min-dur", type=int, default=5)
    ap.add_argument("--max-dur", type=int, default=30)
    ap.add_argument("--stake", type=float, default=10.0)
    ap.add_argument("--out", default="../results/step_parity_scan.md")
    a = ap.parse_args()

    ws = DerivWS(token="")
    md = ["# Step Index parity pricing scan\n\n",
          "Duration D seconds spans (D-1) tick intervals (entry is the tick after ",
          "purchase). Odd interval counts make ties impossible on a fixed-step process, ",
          "so a Rise+Fall pair has exactly one winner and profits iff payout > 2.00.\n\n"]

    for sym in a.symbols:
        print("=" * 76)
        print(f"{sym}")
        print("=" * 76)
        print(f"{'dur':>5}{'intervals':>11}{'parity':>8}{'CALL':>9}{'PUT':>9}"
              f"{'pair ret':>10}{'EV':>9}  note")
        rows = []
        for D in range(a.min_dur, a.max_dur + 1):
            q = {}
            for ct in ("CALL", "PUT"):
                r = ws.call({"proposal": 1, "amount": a.stake, "basis": "stake",
                             "contract_type": ct, "currency": "USD",
                             "underlying_symbol": sym, "duration": D,
                             "duration_unit": "s"})
                if "proposal" in r:
                    q[ct] = float(r["proposal"]["payout"]) / a.stake
                time.sleep(0.06)
            if len(q) < 2:
                continue
            k = D - 1
            odd = (k % 2 == 1)
            worst = min(q.values())          # you only ever collect one leg
            ev = (worst - 2.0) / 2.0 if odd else None
            note = ""
            if odd:
                note = "TIES IMPOSSIBLE -> " + ("+EV" if worst > 2.0 else "priced")
            rows.append((D, k, odd, q["CALL"], q["PUT"], worst, ev))
            evs = f"{ev*100:>8.2f}%" if ev is not None else f"{'--':>9}"
            print(f"{D:>4}s{k:>11}{('odd' if odd else 'even'):>8}"
                  f"{q['CALL']:>9.4f}{q['PUT']:>9.4f}{worst:>10.4f}{evs}  {note}")

        odds = [r for r in rows if r[2]]
        evens = [r for r in rows if not r[2]]
        if odds and evens:
            mo = sum(r[5] for r in odds) / len(odds)
            me = sum(r[5] for r in evens) / len(evens)
            print(f"\n  mean payout on ODD intervals  {mo:.4f}  (ties impossible)")
            print(f"  mean payout on EVEN intervals {me:.4f}  (ties possible)")
            print(f"  difference {me-mo:+.4f}")
            if abs(me - mo) < 0.02:
                print("  -> FLAT ACROSS PARITY. Deriv is not modelling it.")
            else:
                print("  -> ALTERNATES. Deriv prices parity correctly.")

        live = [r for r in odds if r[5] > 2.0]
        if live:
            print(f"\n  *** ODD-INTERVAL DURATIONS PAYING ABOVE 2.00 ***")
            for D, k, _, c, p, w, ev in live:
                print(f"      {D}s ({k} intervals): payout {w:.4f} -> {ev*100:+.2f}%/round")
            print("      VERIFY WITH EXECUTED BUYS before believing any of this.")
            print("      step_pair_test.py --duration <D> --rounds 20")
        md.append(f"\n## {sym}\n\n| dur | intervals | parity | CALL | PUT | pair | EV |\n"
                  "|---|---|---|---|---|---|---|\n")
        for D, k, odd, c, p, w, ev in rows:
            md.append(f"| {D}s | {k} | {'odd' if odd else 'even'} | {c:.4f} | {p:.4f} "
                      f"| {w:.4f} | {f'{ev*100:+.2f}%' if ev is not None else ''} |\n")
        print()
        time.sleep(0.2)

    open(a.out, "w").write("".join(md))
    print(f"wrote {a.out}")
    ws.close()


if __name__ == "__main__":
    main()
