"""barrier_session.py — do barrier payouts on real markets ignore the clock?

THE FLAW IN THE FIRST ATTEMPT
session_vol.py established the premise cleanly: real-market volatility varies 2-4x across
the UTC day (silver 4.04x, GBP/USD 3.52x, NDX 3.83x), while the synthetic controls came back
flat at 1.03 and 1.04 exactly as they must.

But it then quoted CALL, and **CALL/PUT payoffs do not depend on volatility at all**. They
depend on direction, and P(up) is ~0.5 in a quiet hour and a busy hour alike. The NOTOUCH
quotes — the ones where volatility actually enters the payoff — all failed with "Trading is
not offered for this duration".

So the premise was measured and the conclusion was tested on the wrong instrument.

WHERE VOLATILITY ACTUALLY ENTERS
  ONETOUCH  pays if price EVER touches a barrier -> rises with volatility
  NOTOUCH   pays if it never does                -> falls with volatility
  RANGE     pays if price stays inside a band    -> falls with volatility
  UPORDOWN  pays if it leaves the band           -> rises with volatility

For these, a payout that is constant across a 4x volatility range cannot be correct at both
ends. That is the whole test.

THE HURDLE, WHICH IS BRUTAL
Real-market binaries are far more expensive than synthetics. Measured CALL payouts:

    frxEURUSD 1.7140 -> breakeven 58.34%   (16.6% house edge)
    frxGBPUSD 1.7360 -> breakeven 57.60%
    frxUSDJPY 1.7960 -> breakeven 55.68%

against 1.953 -> 51.20% on synthetic digits. Seven times the margin. So a session
mispricing has to be worth more than ~15% before it is tradable at all, which means the
payout would have to be wrong by a lot, not a little.

WHAT THIS DOES
  1. discovers which durations barrier contracts are actually offered at, per symbol,
     by reading the rejection messages rather than guessing
  2. quotes ONETOUCH / NOTOUCH / RANGE / UPORDOWN at a valid duration
  3. computes fair value from realised volatility AT THE CURRENT HOUR, by simulating the
     barrier crossing on that hour's own tick distribution
  4. reports the same fair value recomputed at every other hour's volatility — so a single
     run shows which hours would be mispriced IF the payout is clock-independent
  5. APPENDS the quote with a timestamp to a log, so repeated runs across the day directly
     measure whether the payout moves

Point 5 is the real test and it needs several runs at different hours. Points 3-4 tell you
in one run whether it is worth setting that up.

Read-only.

Run: python3 barrier_session.py
     python3 barrier_session.py --symbols frxXAGUSD frxGBPUSD
"""
import argparse, json, math, os, re, time, datetime as dt
import numpy as np
from deriv_api import DerivWS
from derivfetch import fetch_ticks, native_interval

LOG = "../results/barrier_session_log.jsonl"
SYMBOLS = ["frxXAGUSD", "frxXAUUSD", "frxGBPUSD", "frxUSDJPY", "frxEURUSD", "OTC_NDX"]
BARRIER_TYPES = ["NOTOUCH", "ONETOUCH", "RANGE", "UPORDOWN"]


def hourly_sigma(t, p):
    """Per-tick log-return sd by UTC hour, excluding closure gaps."""
    lp = np.log(np.asarray(p, dtype=float))
    r = np.diff(lp)
    hours = np.array([dt.datetime.fromtimestamp(int(x), dt.UTC).hour for x in t[:-1]])
    gaps = np.diff(t)
    med = np.median(gaps)
    out = {}
    for h in range(24):
        m = (hours == h) & (gaps <= med * 3)
        if m.sum() >= 60:
            out[h] = (float(np.std(r[m])), int(m.sum()))
    return out


def p_touch(sigma_tick, n_ticks, barrier_frac, trials=20000, seed=0):
    """
    P(|log path| ever reaches barrier_frac within n_ticks), by simulation on a driftless
    Gaussian walk with the given per-tick sd. Simulated rather than closed-form because the
    reflection principle assumes continuous monitoring while these settle on discrete ticks.
    """
    rng = np.random.default_rng(seed)
    n_ticks = int(min(n_ticks, 5000))
    hit = 0
    chunk = 2000
    for s in range(0, trials, chunk):
        m = min(chunk, trials - s)
        w = np.cumsum(rng.normal(0, sigma_tick, size=(m, n_ticks)), axis=1)
        hit += int((np.abs(w).max(axis=1) >= barrier_frac).sum())
    return hit / trials


# Barrier arity, confirmed from the server's own rejections on frxEURUSD:
#   NOTOUCH / ONETOUCH  -> "Invalid barrier (Single barrier input is expected)."
#   RANGE / UPORDOWN    -> two barriers, but "Trading is not offered for this duration"
#                          at 15m, so they need a longer one.
SINGLE_BARRIER = {"NOTOUCH", "ONETOUCH"}
DOUBLE_BARRIER = {"RANGE", "UPORDOWN"}

# Durations to try, shortest first. 15m works for single-barrier types; the
# double-barrier ones were rejected there and need hours or a day.
DUR_LADDER = [(15, "m"), (30, "m"), (1, "h"), (2, "h"), (4, "h"), (8, "h"), (1, "d")]


def quote_barrier(ws, sym, ct, stake, bar_abs, spot):
    """
    Try the duration ladder and return the first accepted quote.
    Sends ONE barrier for NOTOUCH/ONETOUCH and TWO for RANGE/UPORDOWN — the previous
    version sent barrier2 on all four, which is why the single-barrier types were
    rejected outright rather than for any reason to do with pricing.
    """
    # Collect EVERY rejection, not just the last. Returning only the final error meant
    # forex reported "Contracts more than 24 hours in duration..." — the complaint from
    # the 1d rung at the end of the ladder — which said nothing about why 15m or 1h
    # failed. The first error is the diagnostic one.
    errs = []
    dec_places = [4]          # mutable so a rejection can teach it the right value
    for dur, unit in DUR_LADDER:
        req = {"proposal": 1, "amount": stake, "basis": "stake",
               "contract_type": ct, "currency": "USD",
               "underlying_symbol": sym, "duration": dur, "duration_unit": unit}
        # Barrier precision is PER SYMBOL, not a fixed rule:
        #   frxXAGUSD -> "can not have more than 4 decimal places"
        #   frxXAUUSD -> "can not have more than 2 decimal places"
        # So the number is parsed from the rejection and the quote retried at that
        # precision. `dp` carries the learned value between attempts.
        dp = dec_places[0]
        off = max(round(bar_abs, dp), 10 ** (-dp))
        fmt = f"+{{:.{dp}f}}"
        if ct in DOUBLE_BARRIER:
            req["barrier"] = fmt.format(off)
            req["barrier2"] = fmt.format(off).replace("+", "-")
        else:
            req["barrier"] = fmt.format(off)
        r = ws.call(req)
        time.sleep(0.12)
        if "proposal" in r:
            return r["proposal"], dur, unit, None
        last = r.get("error", {}).get("message", "")
        m = re.search(r"more than (\d+) decimal", last)
        if m:
            # learn the symbol's precision and retry this same duration once
            dec_places[0] = int(m.group(1))
            off = max(round(bar_abs, dec_places[0]), 10 ** (-dec_places[0]))
            fmt = f"+{{:.{dec_places[0]}f}}"
            req["barrier"] = fmt.format(off)
            if ct in DOUBLE_BARRIER:
                req["barrier2"] = fmt.format(off).replace("+", "-")
            r = ws.call(req)
            time.sleep(0.12)
            if "proposal" in r:
                return r["proposal"], dur, unit, None
            last = r.get("error", {}).get("message", "")
        errs.append(f"{dur}{unit}: {last[:60]}")
        # a barrier complaint means the duration is fine, the level is not
        if "barrier" in last.lower() and "duration" not in last.lower():
            return None, dur, unit, " | ".join(errs[:2])
    return None, None, None, " | ".join(errs[:3])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+", default=SYMBOLS)
    ap.add_argument("--ticks", type=int, default=150000)
    ap.add_argument("--stake", type=float, default=10.0)
    ap.add_argument("--pause", type=float, default=1.5)
    a = ap.parse_args()

    now = dt.datetime.now(dt.UTC)
    hour = now.hour
    ws = DerivWS(token="")
    print(f"UTC hour {hour}\n")
    print("=" * 92)
    print("1. BARRIER CONTRACTS: QUOTED vs FAIR AT THE CURRENT HOUR")
    print("=" * 92)
    print(f"{'symbol':11}{'contract':10}{'dur':>7}{'barrier':>10}{'quoted':>9}"
          f"{'P(model)':>10}{'fair':>9}{'ratio':>8}  note")
    print("-" * 92)

    entries = []
    for sym in a.symbols:
        try:
            t, p, pip = fetch_ticks(ws, sym, a.ticks, verbose=False, strict=False)
        except Exception as e:
            print(f"{sym:11}  fetch failed: {str(e)[:46]}")
            time.sleep(a.pause); continue
        if len(t) < 4000:
            print(f"{sym:11}  only {len(t)} ticks"); time.sleep(a.pause); continue
        hs = hourly_sigma(t, p)
        if hour not in hs:
            print(f"{sym:11}  no data for hour {hour}"); time.sleep(a.pause); continue
        sig_now = hs[hour][0]
        iv = native_interval(t)
        spot = float(p[-1])

        for ct in BARRIER_TYPES:
            # barrier at 1 sigma of a 1h window, scaled per duration below
            got = False
            for dur, unit in DUR_LADDER:
                secs = dur * {"m": 60, "h": 3600, "d": 86400}[unit]
                n_ticks = max(1, int(secs / iv))
                bfrac = sig_now * math.sqrt(n_ticks)
                bar_abs = spot * bfrac
                pr_obj, d2, u2, err = quote_barrier(ws, sym, ct, a.stake, bar_abs, spot)
                # the accepted offset is what got rounded to 4dp, not what we asked for
                bar_abs = max(round(bar_abs, 4), 0.0001)
                if pr_obj is None:
                    continue
                dur, unit = d2, u2
                secs = dur * {"m": 60, "h": 3600, "d": 86400}[unit]
                n_ticks = max(1, int(secs / iv))
                bfrac = bar_abs / spot
                M = float(pr_obj["payout"]) / a.stake
                pt = p_touch(sig_now, n_ticks, bfrac)
                pwin = {"ONETOUCH": pt, "NOTOUCH": 1 - pt,
                        "UPORDOWN": pt, "RANGE": 1 - pt}[ct]
                fair = 1.0 / pwin if pwin > 0 else float("inf")
                ratio = M / fair if fair > 0 else float("nan")
                note = "  <<< QUOTED ABOVE FAIR" if ratio > 1.0 else ""
                print(f"{sym:11}{ct:10}{str(dur)+unit:>7}{bfrac:>10.5f}{M:>9.4f}"
                      f"{pwin:>10.4f}{fair:>9.4f}{ratio:>8.4f}{note}")
                entries.append(dict(ts=now.isoformat(timespec="seconds"), hour=hour,
                                    sym=sym, ct=ct, dur=f"{dur}{unit}", bfrac=bfrac,
                                    payout=M, p_model=pwin, sigma_hour=sig_now))
                got = True
                break
            if not got:
                print(f"{sym:11}{ct:10}  {(err or 'no duration accepted')[:150]}")
        time.sleep(a.pause)

    # ---- 2. what the same payout implies at other hours ------------------
    if entries:
        print(f"\n{'='*92}")
        print("2. IF THE PAYOUT IS CLOCK-INDEPENDENT, WHICH HOURS ARE MISPRICED?")
        print("=" * 92)
        print("  Recomputing fair value at every hour's own volatility, holding the")
        print("  payout fixed at what is quoted now.")
        e = entries[0]
        sym = e["sym"]
        t, p, pip = fetch_ticks(ws, sym, a.ticks, verbose=False, strict=False)
        hs = hourly_sigma(t, p)
        iv = native_interval(t)
        secs = int(e["dur"][:-1]) * {"m": 60, "h": 3600, "d": 86400}[e["dur"][-1]]
        n_ticks = max(1, int(secs / iv))
        print(f"\n  {sym} {e['ct']} {e['dur']}, payout {e['payout']:.4f}, "
              f"barrier {e['bfrac']:.5f}")
        print(f"  {'UTC':>5}{'sigma':>12}{'P(win)':>10}{'fair':>9}{'EV':>9}")
        for h in sorted(hs):
            s = hs[h][0]
            pt = p_touch(s, n_ticks, e["bfrac"], trials=8000, seed=h)
            pw = {"ONETOUCH": pt, "NOTOUCH": 1 - pt,
                  "UPORDOWN": pt, "RANGE": 1 - pt}[e["ct"]]
            ev = pw * e["payout"] - 1
            flag = "  <<<" if ev > 0 else ""
            print(f"  {h:>4}h{s:>12.6f}{pw:>10.4f}{1/pw if pw>0 else 0:>9.4f}"
                  f"{ev*100:>+8.2f}%{flag}")

    ws.close()

    # ---- 3. log for cross-hour comparison --------------------------------
    if entries:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")
        seen = []
        if os.path.exists(LOG):
            for line in open(LOG):
                try:
                    seen.append(json.loads(line))
                except Exception:
                    pass
        hours_logged = sorted({x["hour"] for x in seen})
        print(f"\n{'='*92}")
        print("3. PAYOUT ACROSS HOURS (the decisive test — needs runs at several hours)")
        print("=" * 92)
        print(f"  logged hours so far: {hours_logged}")
        by = {}
        for x in seen:
            by.setdefault((x["sym"], x["ct"], x["dur"]), []).append((x["hour"], x["payout"]))
        moved = False
        for k, v in sorted(by.items()):
            if len({h for h, _ in v}) < 2:
                continue
            ps = [pp for _, pp in v]
            spread = max(ps) - min(ps)
            moved = True
            print(f"  {k[0]:11}{k[1]:10}{k[2]:>6}  payouts {sorted(set(ps))}  "
                  f"spread {spread:.4f}"
                  + ("  -> payout MOVES with the clock" if spread > 0.005
                     else "  -> payout is CLOCK-INDEPENDENT"))
        if not moved:
            print("  Only one hour logged. Re-run at a quiet hour and a busy hour —")
            print("  from section 1, the extremes are around 09h (quiet) and 01h or 14h")
            print("  (busy) depending on the symbol.")

    print(f"\n{'='*92}")
    print("THE HURDLE")
    print("=" * 92)
    print("  Real-market binaries are far dearer than synthetics: measured CALL payouts")
    print("  1.714-1.796 imply breakeven 55.7-58.3%, a 16.6% house edge on EUR/USD")
    print("  against 2.35% on synthetic digits.")
    print()
    print("  So a session mispricing must be worth more than ~15% to be tradable. The")
    print("  volatility ratio is 2-4x, which is large enough in principle — but only if")
    print("  the payout really is clock-independent. Section 3 decides that, and it")
    print("  needs a second run at a different hour.")


if __name__ == "__main__":
    main()
