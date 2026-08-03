"""accu_verify.py — is the BOOM/CRASH accumulator G>1 real, or a units error?

accu_boom.py returned G>1 in 70/70 cells. Two facts make that a bug signature:

  1. tick_size_barrier was IDENTICAL across all 14 symbols (4.331e-05 at g=0.01 for both
     BOOM50, which spikes every 50 ticks, and BOOM1000, which spikes every 1000). A correctly
     priced book cannot use one barrier for both.
  2. Measured breach period ~= spike period in every row (ratio 1.0-1.2), i.e. drift ticks
     never breach. A fair book at g=0.05 needs a breach every ~17 ticks; BOOM900 breaches
     every 1149. That is a 65x gap. Nobody misprices a flagship product by 65x.

So before believing the result, establish what tick_size_barrier actually MEANS.

This script does four things, all read-only:

  A. RAW DUMP — prints every field of contract_details for a BOOM symbol and for R_100
     (which opus already validated as correctly priced at G~0.998). If the same tsb appears
     for both a 6000-spot BOOM index and a 1000-spot R_100, tsb is not spot-relative.

  B. UNITS TEST — if the proposal returns high_barrier / low_barrier as absolute prices,
     the true relative barrier is (high-spot)/spot. Compare that to tsb directly. This is
     the decisive check: it tells you whether accu_boom.py compared a relative return to a
     relative barrier, or to an absolute one.

  C. DERIV'S OWN NUMBER — contract_details.ticks_stayed_in is Deriv's published distribution
     of how long contracts survive. mean/(1+mean) implies THEIR p. If Deriv says p=0.94 and
     accu_boom measured 0.999, the barrier interpretation is wrong, full stop.

  D. STABILITY — re-proposal every 20s. If tsb moves with realised vol, a single snapshot
     is meaningless and the whole scan needs redoing on live barriers.

Run: python3 accu_verify.py
     python3 accu_verify.py --symbol BOOM900 --rounds 10
"""
import argparse, json, math, time
import numpy as np
from deriv_api import DerivWS


def proposal(ws, sym, g, amount=100):
    return ws.call({"proposal": 1, "amount": amount, "basis": "stake",
                    "contract_type": "ACCU", "currency": "USD",
                    "underlying_symbol": sym, "growth_rate": g})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BOOM900")
    ap.add_argument("--control", default="R_100")
    ap.add_argument("--rate", type=float, default=0.05)
    ap.add_argument("--rounds", type=int, default=6)
    ap.add_argument("--interval", type=float, default=20.0)
    a = ap.parse_args()

    ws = DerivWS(token="")

    # ---- A. RAW DUMP -----------------------------------------------------
    print("=" * 70)
    print("A. RAW PROPOSAL DUMP")
    print("=" * 70)
    details = {}
    for sym in (a.symbol, a.control):
        r = proposal(ws, sym, a.rate)
        if "proposal" not in r:
            print(f"\n{sym}: NO PROPOSAL — {r.get('error', {}).get('message')}")
            print("  ^ if this errors, ACCU is not actually tradeable here and the")
            print("    accu_boom.py numbers for this symbol are meaningless.")
            continue
        p = r["proposal"]
        cd = p.get("contract_details", {})
        details[sym] = (p, cd)
        print(f"\n--- {sym} @ g={a.rate} ---")
        print(f"  spot            {p.get('spot')}")
        print(f"  payout          {p.get('payout')}   ask {p.get('ask_price')}")
        for k, v in cd.items():
            if k == "ticks_stayed_in":
                print(f"  {k:22} [{len(v)} samples] first10={v[:10]}")
            else:
                print(f"  {k:22} {v}")
        time.sleep(0.2)

    # ---- B. UNITS TEST ---------------------------------------------------
    print()
    print("=" * 70)
    print("B. UNITS TEST — is tick_size_barrier relative or absolute?")
    print("=" * 70)
    for sym, (p, cd) in details.items():
        spot = float(p.get("spot") or 0)
        tsb = cd.get("tick_size_barrier")
        hi, lo = cd.get("high_barrier"), cd.get("low_barrier")
        print(f"\n  {sym}: spot={spot} tsb={tsb}")
        if hi and lo and spot:
            hi, lo = float(hi), float(lo)
            rel_hi = (hi - spot) / spot
            rel_lo = (spot - lo) / spot
            print(f"    high_barrier {hi}  -> relative {rel_hi:.6e}")
            print(f"    low_barrier  {lo}  -> relative {rel_lo:.6e}")
            if tsb:
                ratio = rel_hi / float(tsb)
                print(f"    rel_hi / tsb = {ratio:.4f}")
                if abs(ratio - 1) < 0.05:
                    print("    => tsb IS the relative barrier. accu_boom comparison was CORRECT.")
                else:
                    print(f"    => MISMATCH by {ratio:.2f}x. accu_boom used the wrong quantity.")
                    print(f"       True relative barrier is {rel_hi:.6e}, not {tsb}.")
        else:
            print("    no high/low_barrier in response — cannot resolve units this way.")
            print("    Rely on section C instead.")

    # ---- C. DERIV'S OWN IMPLIED p ---------------------------------------
    print()
    print("=" * 70)
    print("C. DERIV'S OWN ticks_stayed_in  (the decisive cross-check)")
    print("=" * 70)
    print(f"\n{'symbol':10}{'g':>6}{'mean_ticks':>12}{'p_deriv':>10}{'G_deriv':>10}  verdict")
    print("-" * 70)
    for sym in (a.symbol, a.control):
        for g in (0.01, 0.02, 0.03, 0.04, 0.05):
            r = proposal(ws, sym, g)
            if "proposal" not in r:
                continue
            tsi = r["proposal"].get("contract_details", {}).get("ticks_stayed_in", [])
            if not tsi:
                print(f"{sym:10}{g:>6.2f}{'--':>12}{'--':>10}{'--':>10}  no stats")
                continue
            mt = float(np.mean(tsi))
            p_d = mt / (1 + mt)
            G_d = (1 + g) * p_d
            v = ("G>1 — Deriv confirms" if G_d > 1
                 else "G<1 — correctly priced")
            print(f"{sym:10}{g:>6.2f}{mt:>12.2f}{p_d:>10.5f}{G_d:>10.5f}  {v}")
            time.sleep(0.15)

    print("\n  If p_deriv is ~0.94-0.98 while accu_boom measured 0.999, the barrier")
    print("  interpretation in accu_boom.py is wrong and G>1 is an artifact.")
    print("  If p_deriv agrees with 0.999, the mispricing is real and Deriv publishes it.")

    # ---- D. STABILITY ----------------------------------------------------
    print()
    print("=" * 70)
    print(f"D. BARRIER STABILITY — {a.symbol} @ g={a.rate}, {a.rounds}x{a.interval:.0f}s")
    print("=" * 70)
    seen = []
    for i in range(a.rounds):
        r = proposal(ws, a.symbol, a.rate)
        if "proposal" in r:
            cd = r["proposal"]["contract_details"]
            tsb = float(cd.get("tick_size_barrier", "nan"))
            seen.append(tsb)
            print(f"  [{i+1}/{a.rounds}] spot={r['proposal'].get('spot')} tsb={tsb:.6e}")
        if i < a.rounds - 1:
            time.sleep(a.interval)
    if len(seen) > 1:
        arr = np.array(seen)
        rng = float(arr.max() - arr.min())
        print(f"\n  range {rng:.3e}  ({rng/arr.mean()*100:.4f}% of mean)")
        if rng == 0:
            print("  STATIC barrier — a single snapshot is valid.")
        else:
            print("  Barrier MOVES. It is vol-responsive; snapshot scans are unreliable.")

    ws.close()


if __name__ == "__main__":
    main()
