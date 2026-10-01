"""accu_audit.py — is the accumulator growth rate still calibrated to the barrier?

WHY REDO THIS
Accumulators were audited weeks ago (G = 0.9935-0.9979, correctly priced). But the watchdog
just found that three symbols we believed untouched — 1HZ100V, 1HZ10V, R_100 — now execute
digit contracts at 1.9230 against a proposal of 1.9530. Every prior tool missed it because
they read proposals. Old audits go stale, and growth-based products have not been re-checked
since two repricings.

THE STRUCTURE, AND WHY IT IS EXACTLY COMPUTABLE
An accumulator pays (1+g)^n if the price stays inside a barrier for n ticks, and zero the
moment it steps outside. So the per-tick growth factor is

    G = P(stay inside barrier) * (1 + g)

G < 1 means negative EV, compounding every tick. Both terms are measurable: g is quoted, and
P(stay) is the empirical fraction of ticks whose relative move falls inside the barrier. No
model is required.

THE SPECIFIC RISK WORTH CHECKING
If the barrier is defined RELATIVE to spot (a fraction of price), it rescales as spot moves
and stays matched to volatility. If it is ABSOLUTE, it does not — and a drifting sigma walks
it out of calibration exactly as the fixed pip grid did on JD100. That is the one shape of
edge this project has ever found, so it is worth confirming which it is rather than assuming.

The earlier accumulator work already found one trap here: an apparent +8.7% anomaly on
BOOM1000 traced to longcode band ROUNDING. The true knockout rule reads tick_size_barrier
from the shortcode, not the rounded percentage shown in the longcode. This tool reads the
shortcode value.

WHAT IT MEASURES
For every symbol offering ACCU, at every growth rate offered:
  1. the quoted growth rate and the barrier, read from the contract itself
  2. whether the barrier is absolute or scales with spot (compared across time/spot levels)
  3. P(stay) measured from clean ticks
  4. G = P(stay) * (1+g), with a Wilson bound on P(stay)
  5. the implied breakeven barrier — how much wider it would need to be for G = 1

Read-only unless --execute, which buys one minimum contract per cell to read the true
contracted barrier rather than the proposal's.

Run: python3 accu_audit.py
     python3 accu_audit.py --symbols 1HZ100V R_100 BOOM1000 --execute
"""
import argparse, math, re, time
import numpy as np
from deriv_api import DerivWS
from derivfetch import fetch_ticks, contiguous_pairs, native_interval, wilson

ACCU_SYMBOLS = ["1HZ10V", "1HZ15V", "1HZ25V", "1HZ30V", "1HZ50V", "1HZ75V",
                "1HZ90V", "1HZ100V", "R_10", "R_25", "R_50", "R_75", "R_100",
                "BOOM300N", "BOOM500", "BOOM1000", "CRASH300N", "CRASH500",
                "CRASH1000"]
GROWTH_RATES = [0.01, 0.02, 0.03, 0.04, 0.05]


def barrier_from(resp):
    """
    Return (value, kind, source) where kind is "rel" (fraction of spot) or "abs" (price
    distance). UNITS ARE THE WHOLE PROBLEM HERE.

    The first version of this function conflated the two and compared an ABSOLUTE
    barrier_spot_distance against RELATIVE tick moves. Every P(stay) came out as exactly
    1.000000 and all 95 cells reported G = 1+g — a fake edge produced entirely by a units
    mismatch.

    It also fell back to a longcode regex for "([0-9.]+)%", which matches the GROWTH RATE
    text, not the barrier. That is why those rows returned barrier == g exactly.

    tick_size_barrier  -> RELATIVE (fraction of spot)
    barrier_spot_distance -> ABSOLUTE (price units)
    """
    v = resp.get("tick_size_barrier")
    if v is not None:
        try:
            return float(v), "rel", "tick_size_barrier"
        except (TypeError, ValueError):
            pass
    v = resp.get("barrier_spot_distance")
    if v is not None:
        try:
            return float(v), "abs", "barrier_spot_distance"
        except (TypeError, ValueError):
            pass
    return None, None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+", default=ACCU_SYMBOLS)
    ap.add_argument("--ticks", type=int, default=200000)
    ap.add_argument("--stake", type=float, default=10.0)
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--pause", type=float, default=1.5)
    ap.add_argument("--out", default="../results/accu_audit.md")
    a = ap.parse_args()

    tr = None
    if a.execute:
        tr = DerivWS()
        if (tr.account or {}).get("account_type") != "demo":
            print("NOT demo — refusing to execute"); tr = None

    print(f"{'symbol':10}{'g':>6}{'barrier':>10}{'unit':>5}{'rel thr':>11}"
          f"{'P(stay)':>10}{'G':>9}{'99% low G':>11}{'flip@':>9}  note")
    print("  (rel thr = barrier as a fraction of spot, which is scale-invariant.")
    print("   flip@ = how much wider the barrier would need to be for G = 1; a small")
    print("   number means spot drift over the sample could account for the result.)")
    print("-" * 92)

    rows = []
    for sym in a.symbols:
        ws = DerivWS(token="")
        try:
            t, p, pip = fetch_ticks(ws, sym, a.ticks, verbose=False, strict=False)
        except Exception as e:
            print(f"{sym:10}  fetch failed: {str(e)[:50]}")
            ws.close(); time.sleep(a.pause); continue
        if len(t) < 20000:
            print(f"{sym:10}  only {len(t)} ticks"); ws.close(); continue

        v = np.round(p * (10 ** pip)).astype(np.int64)
        iv = native_interval(t)
        cg = contiguous_pairs(t, v, iv)
        # relative move per tick — the quantity a relative barrier acts on
        absmove = np.abs(np.diff(p)[cg])              # price units
        relmove = absmove / p[:-1][cg]                 # fraction of spot
        spot = float(p[-1])

        for g in GROWTH_RATES:
            r = ws.call({"proposal": 1, "amount": a.stake, "basis": "stake",
                         "contract_type": "ACCU", "currency": "USD",
                         "underlying_symbol": sym, "growth_rate": g})
            if "proposal" not in r:
                if g == GROWTH_RATES[0]:
                    print(f"{sym:10}{g:>6.2f}  not offered: "
                          f"{r.get('error', {}).get('message', '')[:44]}")
                continue
            pr = r["proposal"]
            bar, kind, src = barrier_from(pr)
            time.sleep(0.08)

            if a.execute and tr is not None:
                b = tr.call({"buy": 1, "price": round(a.stake * 3, 2), "parameters":
                             dict(amount=a.stake, basis="stake", contract_type="ACCU",
                                  currency="USD", underlying_symbol=sym,
                                  growth_rate=g)})
                if "buy" in b:
                    cid = b["buy"]["contract_id"]
                    time.sleep(1.0)
                    c = tr.open_contract(cid).get("proposal_open_contract", {})
                    eb, ekind, esrc = barrier_from(c)
                    if eb is not None:
                        bar, kind, src = eb, ekind, "exec:" + (esrc or "")
                    tr.call({"sell": cid, "price": 0})
                    time.sleep(0.3)

            if bar is None:
                print(f"{sym:10}{g:>6.2f}  no barrier field in response")
                continue

            # SPOT-DRIFT BIAS. barrier_spot_distance is absolute and quoted at the
            # CURRENT spot. P(stay) is measured over 200k historical ticks during which
            # spot moved, so a fixed absolute barrier is too WIDE when spot was lower and
            # overstates survival. Convert to a relative barrier at the quote spot and
            # apply it to relative moves — scale-invariant, and removes the bias.
            if kind == "abs":
                bar_rel = bar / spot
                moves, thr_used, unit_used = relmove, bar_rel, "rel(from abs)"
            else:
                moves, thr_used, unit_used = relmove, bar, "rel"
            k = int((moves < thr_used).sum())
            n = len(moves)
            pstay = k / n
            lo, _ = wilson(k, n)
            G = pstay * (1 + g)
            Glo = lo * (1 + g)
            # how wide would the barrier need to be for G = 1?
            need_p = 1.0 / (1 + g)
            need_bar = float(np.quantile(moves, min(need_p, 0.999999)))
            # how much spot drift would flip the sign of this cell?
            drift_to_flip = (need_bar / thr_used - 1) * 100 if thr_used > 0 else float("nan")
            note = ""
            # SANITY GUARD: P(stay) of exactly 1 across 200k ticks means the barrier is
            # far outside the move distribution, which in practice means wrong units.
            if pstay >= 0.99999:
                note = "  UNITS SUSPECT — P(stay)=1, barrier not comparable to moves"
            elif Glo > 1:
                note = "  <<< POSITIVE (verify executed)"
            elif G > 1:
                note = "  positive point est, CI straddles 1"
            if "longcode" in (src or ""):
                note += "  BARRIER FROM ROUNDED LONGCODE"
            print(f"{sym:10}{g:>6.2f}{bar:>10.6f}{(kind or '?'):>5}{thr_used:>11.7f}"
                  f"{pstay:>10.6f}{G:>9.5f}{Glo:>11.5f}"
                  f"{drift_to_flip:>+8.2f}%{note}")
            rows.append(dict(sym=sym, g=g, bar=bar, kind=kind, src=src, pstay=pstay,
                             thr=thr_used, flip=drift_to_flip,
                             G=G, Glo=Glo, need_bar=need_bar, spot=spot, n=n))
        ws.close()
        time.sleep(a.pause)

    if not rows:
        print("\nnothing measured"); return

    # ---- is the barrier relative or absolute? ----------------------------
    print(f"\n{'='*92}")
    print("IS THE BARRIER RELATIVE TO SPOT?")
    print("=" * 92)
    print("  A RELATIVE barrier rescales with spot and stays matched to volatility.")
    print("  An ABSOLUTE one does not — and a drifting sigma walks it out of calibration,")
    print("  which is exactly the shape that made JD100 tradable.")
    print()
    print(f"  {'symbol':10}{'spot':>12}{'barrier':>11}{'barrier*spot':>14}"
          f"{'implied abs move':>18}")
    for r in [x for x in rows if x["g"] == 0.01][:20]:
        print(f"  {r['sym']:10}{r['spot']:>12.4f}{r['bar']:>11.6f}"
              f"{r['bar']*r['spot']:>14.6f}{r['bar']*r['spot']:>18.6f}")
    print("\n  If 'barrier' is constant across symbols with very different spots, it is")
    print("  RELATIVE. If 'barrier*spot' is constant instead, it is ABSOLUTE.")

    # ---- verdict ---------------------------------------------------------
    print(f"\n{'='*92}")
    print("VERDICT")
    print("=" * 92)
    live = [r for r in rows if r["Glo"] > 1 and r["pstay"] < 0.99999]
    suspect = [r for r in rows if r["pstay"] >= 0.99999]
    if suspect:
        print(f"  {len(suspect)} cell(s) had P(stay) = 1.000000 — barrier units do not")
        print("  match the move distribution. Those cells are excluded, not findings.")
    if live:
        print(f"  {len(live)} cell(s) with a 99% lower bound on G above 1:")
        for r in live:
            print(f"    {r['sym']:10} g={r['g']:.2f} barrier {r['bar']:.6f} "
                  f"P(stay) {r['pstay']:.6f} G {r['G']:.5f} [99% low {r['Glo']:.5f}]")
        print()
        print("  BEFORE BELIEVING IT:")
        print("   1. re-run with --execute — the barrier MUST come from the contracted")
        print("      tick_size_barrier, not a longcode percentage. Longcode rounding")
        print("      produced a fake +8.7% edge on BOOM1000 previously.")
        print("   2. hold contracts live and confirm they knock out where predicted")
        print("   3. G compounds per tick, so a 0.1% error becomes enormous over a run")
    else:
        best = max(rows, key=lambda r: r["G"])
        print(f"  No cell has G above 1 at the 99% lower bound.")
        print(f"  Closest: {best['sym']} g={best['g']:.2f}, G={best['G']:.5f} "
              f"(needs 1.00000)")
        print(f"  P(stay) is {best['pstay']:.5f}; it would need "
              f"{1/(1+best['g']):.5f} to break even.")
        print()
        print("  Accumulators remain correctly priced. The growth rate is set below the")
        print("  barrier's survival probability on every symbol tested.")

    with open(a.out, "w") as f:
        f.write("# Accumulator audit\n\n| symbol | g | barrier | source | P(stay) | G | 99% low |\n"
                "|---|---|---|---|---|---|---|\n")
        for r in rows:
            f.write(f"| {r['sym']} | {r['g']:.2f} | {r['bar']:.6f} | {r['src']} "
                    f"| {r['pstay']:.6f} | {r['G']:.5f} | {r['Glo']:.5f} |\n")
    print(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
