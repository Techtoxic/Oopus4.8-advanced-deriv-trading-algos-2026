"""step_units_scan.py — does the parity rule hold across ticks, seconds AND minutes?

WHAT WE KNOW
step_parity_scan.py established that Deriv prices the exact binomial tie probability on
Step Index, in SECONDS:

    every ODD interval count quotes exactly 1.9530  (fair 2.0000, flat 2.35% margin)
    every EVEN interval count tracks 2/(1-P_tie), P_tie = C(k,k/2)/2^k, margin 2.7-3.0%

The key detail is the seconds->intervals mapping: a D-second contract spans D-1 tick
INTERVALS, because entry is the tick AFTER purchase. That off-by-one is what defeated the
Rise+Fall hedge at 15s (14 intervals, even, ties permitted).

THE PREDICTION THIS TESTS
The three duration units map to interval counts differently:

    ticks   N        ->  N intervals            odd N (1,3,5,7,9) is tie-free
    seconds D        ->  D-1 intervals          even D (16,18,20...) is tie-free
    minutes M        ->  60M-1 intervals        ALWAYS odd -> ALWAYS tie-free

That last line is the sharp one. 60M is even for every M, so 60M-1 is always odd. If the
rule is consistent, EVERY minute duration must quote 1.9530 exactly.

Any duration whose quoted payout disagrees with its interval parity is an inconsistency:
  - tie-free interval count quoting ABOVE 2.0000 -> Rise+Fall pair is +EV there
  - tie-bearing count quoting 1.9530             -> single-leg is mispriced against you

Deriv has been exactly right everywhere tested so far, so the expected result is full
consistency. But the units are handled by different code paths, and off-by-ones live at
boundaries — which is precisely what the seconds mapping already demonstrated.

Read-only, proposals only. Verify any candidate with executed buys before believing it.

Run: python3 step_units_scan.py
     python3 step_units_scan.py --symbol stpRNG3
"""
import argparse, math, time
from deriv_api import DerivWS

NO_TIE_PAYOUT = 1.9530     # observed on every tie-free duration in seconds
BREAKEVEN = 2.0000         # a pair with exactly one winner needs payout > 2


def p_tie(k):
    """P(fixed-step walk returns to start after k steps)."""
    if k <= 0 or k % 2:
        return 0.0
    return math.comb(k, k // 2) / 2 ** k


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="stpRNG")
    ap.add_argument("--stake", type=float, default=10.0)
    ap.add_argument("--out", default="../results/step_units_scan.md")
    a = ap.parse_args()

    ws = DerivWS(token="")

    plan = []
    for n in range(1, 11):
        plan.append(("t", n, n, f"{n}t"))
    for d in range(15, 31):
        plan.append(("s", d, d - 1, f"{d}s"))
    for d in (40, 50, 60):
        plan.append(("s", d, d - 1, f"{d}s"))
    for m in range(1, 11):
        plan.append(("m", m, 60 * m - 1, f"{m}m"))

    print(f"symbol {a.symbol}   stake ${a.stake}")
    print("=" * 88)
    print(f"{'dur':>6}{'unit':>6}{'intervals':>11}{'parity':>8}{'quoted':>10}"
          f"{'fair':>9}{'pair EV':>10}  status")
    print("=" * 88)

    rows, anomalies = [], []
    for unit, dur, k, label in plan:
        q = {}
        for ct in ("CALL", "PUT"):
            r = ws.call({"proposal": 1, "amount": a.stake, "basis": "stake",
                         "contract_type": ct, "currency": "USD",
                         "underlying_symbol": a.symbol,
                         "duration": dur, "duration_unit": unit})
            if "proposal" in r:
                q[ct] = float(r["proposal"]["payout"]) / a.stake
            time.sleep(0.06)
        if len(q) < 2:
            print(f"{label:>6}{unit:>6}{k:>11}{'':>8}{'--':>10}  not offered")
            continue

        M = min(q.values())
        pt = p_tie(k)
        fair = 2.0 / (1 - pt) if pt < 1 else float("inf")
        tie_free = (pt == 0.0)
        ev = (M - 2.0) / 2.0 if tie_free else None

        status = ""
        if tie_free:
            if M > BREAKEVEN + 1e-9:
                status = f"*** TIE-FREE AND PAYS >2.00 -> {ev*100:+.2f}%/round ***"
                anomalies.append((label, k, M, ev))
            elif abs(M - NO_TIE_PAYOUT) < 0.005:
                status = "tie-free, priced (1.9530)"
            else:
                status = f"tie-free but quotes {M:.4f}, not 1.9530 — CHECK"
                anomalies.append((label, k, M, ev))
        else:
            implied = 1 - 2 / M
            if abs(M - NO_TIE_PAYOUT) < 0.005:
                status = "*** TIES POSSIBLE BUT PRICED AS TIE-FREE — CHECK ***"
                anomalies.append((label, k, M, None))
            else:
                status = f"implied P(tie) {implied:.4f} vs true {pt:.4f}"

        evs = f"{ev*100:>+9.2f}%" if ev is not None else f"{'--':>10}"
        print(f"{label:>6}{unit:>6}{k:>11}{('odd' if k % 2 else 'even'):>8}"
              f"{M:>10.4f}{fair:>9.4f}{evs}  {status}")
        rows.append((label, unit, k, M, fair, pt, ev))
        time.sleep(0.1)

    print("=" * 88)
    print("VERDICT")
    print("=" * 88)

    tf = [r for r in rows if r[5] == 0.0]
    tb = [r for r in rows if r[5] > 0.0]
    if tf:
        vals = sorted({round(r[3], 4) for r in tf})
        print(f"  tie-free durations ({len(tf)}): payouts {vals}")
        print(f"    all at 1.9530? {'YES — consistent' if vals == [NO_TIE_PAYOUT] else 'NO'}")
    if tb:
        errs = [abs((1 - 2 / r[3]) - r[5]) for r in tb]
        print(f"  tie-bearing durations ({len(tb)}): mean |implied - true| P(tie) "
              f"= {sum(errs)/len(errs):.4f}")

    mins = [r for r in rows if r[1] == "m"]
    if mins:
        allodd = all(r[2] % 2 == 1 for r in mins)
        allpriced = all(abs(r[3] - NO_TIE_PAYOUT) < 0.005 for r in mins)
        print(f"\n  MINUTES: 60M-1 intervals, always odd? {allodd}")
        print(f"           all quoting 1.9530? {allpriced}")
        if allodd and allpriced:
            print("           -> consistent. every minute duration is tie-free and priced.")

    print()
    if anomalies:
        print("  ANOMALIES — verify with executed buys before believing any of these:")
        for label, k, M, ev in anomalies:
            e = f", pair EV {ev*100:+.2f}%" if ev is not None else ""
            print(f"    {label} ({k} intervals): payout {M:.4f}{e}")
        print("    step_pair_test.py --duration <D> --unit <t|s> --rounds 20")
    else:
        print("  NO ANOMALIES. Parity is priced consistently across ticks, seconds")
        print("  and minutes. The Rise+Fall hedge is dead on Step Index at every")
        print("  duration and every unit.")

    md = [f"# Step Index parity across duration units — {a.symbol}\n\n",
          "ticks N -> N intervals; seconds D -> D-1; minutes M -> 60M-1 (always odd).\n\n",
          "| dur | unit | intervals | parity | quoted | fair | pair EV |\n",
          "|---|---|---:|---|---:|---:|---:|\n"]
    for label, unit, k, M, fair, pt, ev in rows:
        md.append(f"| {label} | {unit} | {k} | {'odd' if k % 2 else 'even'} | "
                  f"{M:.4f} | {fair:.4f} | {f'{ev*100:+.2f}%' if ev is not None else ''} |\n")
    open(a.out, "w").write("".join(md))
    print(f"\nwrote {a.out}")
    ws.close()


if __name__ == "__main__":
    main()
