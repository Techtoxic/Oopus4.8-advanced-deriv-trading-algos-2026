"""session_vol.py — does Deriv price REAL-market symbols by clock or by realised volatility?

WHY THIS IS OUTSIDE EVERYTHING ALREADY CLOSED
Every closing argument in this project was measured on SYNTHETIC indices. The information
bound (I(past ; next) = 0.0023 bits) was computed on synthetic ticks, where Deriv generates
the price and can price to the generator exactly.

Real-market symbols are a different game. On frxEURUSD, OTC_SPC, frxXAUUSD, Deriv does not
generate the price — it tracks one. And real markets have something synthetics do not:
**sessions**. Volatility on EUR/USD during the London-New York overlap is several times what
it is at 03:00 UTC. The same is true of gold, and of every OTC index while its home exchange
is shut.

So the question is simple and has never been asked here:

    Is Deriv's payout for a fixed-duration contract CONSTANT across the clock,
    while realised volatility is NOT?

If payouts are flat and volatility is not, then a barrier or range contract is systematically
mispriced at predictable hours — and the mispricing is on a schedule rather than a drift.
That is a different shape from JD100: not a parameter that fell out of calibration slowly,
but one that is wrong at 03:00 and right at 14:00, every single day.

WHY IT NEEDS NO EXTERNAL FEED
Both sides come from Deriv's own API. Realised volatility per hour is measured from their
ticks; the payout is read from their proposals. The comparison is internal and complete.

WHAT IS MEASURED
  1. realised volatility by UTC hour, per symbol, from clean ticks
  2. the ratio peak-hour vol / trough-hour vol — how much the underlying actually varies
  3. payouts for a fixed contract quoted at several hours, to see whether they move at all
  4. for TOUCH / NO-TOUCH specifically: barrier distance is the thing volatility acts on, so
     a fixed barrier at a fixed payout across a 3x volatility range cannot be right at both
     ends

DIRECTION OF THE EDGE, IF IT EXISTS
  - quiet hours: volatility LOW  -> NOTOUCH is underpriced, ONETOUCH overpriced
  - busy hours:  volatility HIGH -> ONETOUCH is underpriced, NOTOUCH overpriced

The tool reports which side, at which hour, on which symbol.

CONTROLS
  - synthetic symbols are included as a NEGATIVE CONTROL. They have no sessions, so their
    hourly volatility must be flat. If a synthetic shows a session pattern, the measurement
    is wrong, not the market.
  - market-closed hours are identified from tick gaps rather than assumed.

Read-only. No trades.

Run: python3 session_vol.py
     python3 session_vol.py --symbols frxEURUSD frxXAUUSD OTC_SPC --ticks 300000
"""
import argparse, math, time, datetime as dt
import numpy as np
from deriv_api import DerivWS
from derivfetch import fetch_ticks, native_interval

REAL = ["frxEURUSD", "frxGBPUSD", "frxUSDJPY", "frxAUDUSD",
        "frxXAUUSD", "frxXAGUSD", "OTC_SPC", "OTC_NDX", "OTC_FTSE", "OTC_N225"]
CONTROL = ["R_100", "1HZ100V"]        # synthetics: must show NO session pattern


def hourly_vol(t, p):
    """Realised volatility of log returns, bucketed by UTC hour."""
    lp = np.log(np.asarray(p, dtype=float))
    r = np.diff(lp)
    hours = np.array([dt.datetime.fromtimestamp(int(x), dt.UTC).hour for x in t[:-1]])
    gaps = np.diff(t)
    out = {}
    for h in range(24):
        m = (hours == h) & (gaps <= np.median(gaps) * 3)   # exclude closure gaps
        if m.sum() < 200:
            continue
        out[h] = (float(np.std(r[m])), int(m.sum()))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+", default=REAL + CONTROL)
    ap.add_argument("--ticks", type=int, default=200000)
    ap.add_argument("--stake", type=float, default=10.0)
    ap.add_argument("--pause", type=float, default=1.5)
    ap.add_argument("--out", default="../results/session_vol.md")
    a = ap.parse_args()

    ws = DerivWS(token="")
    rows = []

    print("=" * 90)
    print("1. REALISED VOLATILITY BY UTC HOUR")
    print("=" * 90)
    print(f"{'symbol':11}{'iv':>4}{'span_d':>8}{'quiet h':>9}{'busy h':>8}"
          f"{'vol quiet':>12}{'vol busy':>11}{'ratio':>8}  verdict")
    print("-" * 90)

    for sym in a.symbols:
        try:
            t, p, pip = fetch_ticks(ws, sym, a.ticks, verbose=False, strict=False)
        except Exception as e:
            print(f"{sym:11}  fetch failed: {str(e)[:48]}")
            time.sleep(a.pause); continue
        if len(t) < 20000:
            print(f"{sym:11}  only {len(t)} ticks")
            time.sleep(a.pause); continue

        hv = hourly_vol(t, p)
        if len(hv) < 6:
            print(f"{sym:11}  only {len(hv)} usable hours")
            time.sleep(a.pause); continue
        quiet_h = min(hv, key=lambda h: hv[h][0])
        busy_h = max(hv, key=lambda h: hv[h][0])
        vq, vb = hv[quiet_h][0], hv[busy_h][0]
        ratio = vb / vq if vq > 0 else float("nan")
        span = (t[-1] - t[0]) / 86400
        is_ctrl = sym in CONTROL
        verdict = ""
        if is_ctrl:
            verdict = ("CONTROL OK (flat)" if ratio < 1.5
                       else "*** CONTROL FAILED — measurement is wrong ***")
        elif ratio > 2.0:
            verdict = "STRONG session pattern"
        elif ratio > 1.4:
            verdict = "session pattern"
        else:
            verdict = "flat"
        print(f"{sym:11}{native_interval(t):>4}{span:>8.1f}{quiet_h:>8}h{busy_h:>7}h"
              f"{vq:>12.6f}{vb:>11.6f}{ratio:>8.2f}  {verdict}")
        rows.append(dict(sym=sym, hv=hv, quiet=quiet_h, busy=busy_h,
                         vq=vq, vb=vb, ratio=ratio, ctrl=is_ctrl))
        time.sleep(a.pause)

    ctrl = [r for r in rows if r["ctrl"]]
    if ctrl and any(r["ratio"] >= 1.5 for r in ctrl):
        print("\n  *** A SYNTHETIC SHOWED A SESSION PATTERN. Synthetics have no sessions,")
        print("      so this is a measurement artifact — stop and fix it before reading")
        print("      anything below. ***")

    real = [r for r in rows if not r["ctrl"] and np.isfinite(r["ratio"])]
    if not real:
        print("\nno real-market symbols measured"); ws.close(); return

    # ---- 2. do payouts move with the clock? ------------------------------
    print(f"\n{'='*90}")
    print("2. DOES THE PAYOUT MOVE WITH THE CLOCK?")
    print("=" * 90)
    print("  If volatility varies 2-3x across the day and the payout does not, a")
    print("  fixed-barrier contract cannot be fairly priced at both ends.")
    print()
    print(f"  {'symbol':11}{'contract':12}{'payout now':>12}{'UTC hour':>10}"
          f"{'vol now':>11}{'vol/mean':>10}")
    now_h = dt.datetime.now(dt.UTC).hour
    for r in sorted(real, key=lambda x: -x["ratio"])[:6]:
        sym = r["sym"]
        vols = [v for v, _ in r["hv"].values()]
        vnow = r["hv"].get(now_h, (float("nan"), 0))[0]
        vmean = float(np.mean(vols))
        for ct, extra in (("CALL", {}), ("NOTOUCH", {"barrier": "+0.0010"})):
            req = {"proposal": 1, "amount": a.stake, "basis": "stake",
                   "contract_type": ct, "currency": "USD",
                   "underlying_symbol": sym, "duration": 15,
                   "duration_unit": "m", **extra}
            rr = ws.call(req)
            time.sleep(0.1)
            if "proposal" in rr:
                M = float(rr["proposal"]["payout"]) / a.stake
                print(f"  {sym:11}{ct:12}{M:>12.4f}{now_h:>9}h{vnow:>11.6f}"
                      f"{vnow/vmean if vmean else float('nan'):>10.2f}")
            else:
                msg = rr.get("error", {}).get("message", "")[:40]
                print(f"  {sym:11}{ct:12}  {msg}")
    ws.close()

    # ---- 3. the hour profile for the strongest symbol --------------------
    print(f"\n{'='*90}")
    print("3. HOUR PROFILE — the symbol with the strongest session pattern")
    print("=" * 90)
    top = max(real, key=lambda r: r["ratio"])
    vols = [v for v, _ in top["hv"].values()]
    vmean = float(np.mean(vols))
    print(f"  {top['sym']}  (peak/trough = {top['ratio']:.2f}x)")
    print(f"  {'UTC':>5}{'vol':>12}{'vs mean':>10}{'n':>9}  bar")
    for h in sorted(top["hv"]):
        v, n = top["hv"][h]
        rel = v / vmean if vmean else 0
        print(f"  {h:>4}h{v:>12.6f}{rel:>10.2f}{n:>9}  {'#' * int(rel * 20)}")

    print(f"\n{'='*90}")
    print("VERDICT")
    print("=" * 90)
    strong = [r for r in real if r["ratio"] > 2.0]
    if strong:
        print(f"  {len(strong)} symbol(s) with volatility varying more than 2x across the day:")
        for r in strong:
            print(f"    {r['sym']:11} {r['ratio']:.2f}x  quiet {r['quiet']}h  busy {r['busy']}h")
        print()
        print("  NEXT, and this is the actual test:")
        print("   1. quote the SAME barrier contract at the quiet hour and the busy hour")
        print("   2. if the payout is identical, price it against realised vol at each")
        print("   3. the mispriced side is NOTOUCH in quiet hours, ONETOUCH in busy hours")
        print("   4. verify with executed buys — proposals have lied six times here")
        print()
        print("  This is a SCHEDULED mispricing rather than a drifting one, so unlike")
        print("  JD100 it does not require waiting for a parameter to wander.")
    else:
        print("  No real-market symbol shows volatility varying more than 2x by hour.")
        print("  Either Deriv's real-market feeds are smoothed, or the sample does not")
        print("  span enough sessions. Check the span_d column before concluding.")

    with open(a.out, "w") as f:
        f.write("# Session volatility on real-market symbols\n\n"
                "| symbol | quiet h | busy h | vol quiet | vol busy | ratio |\n"
                "|---|---|---|---|---|---|\n")
        for r in rows:
            f.write(f"| {r['sym']} | {r['quiet']} | {r['busy']} | {r['vq']:.6f} "
                    f"| {r['vb']:.6f} | {r['ratio']:.2f} |\n")
    print(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
