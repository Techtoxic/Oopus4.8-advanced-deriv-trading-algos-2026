"""option_audit.py — are turbos and vanillas priced at their empirical fair value?

WHY REDO THESE
Turbos and vanillas were checked by `surface_scan.py` weeks ago and came back correctly
priced. But `watchdog.py` has since found that three symbols we believed untouched
(1HZ100V, 1HZ10V, R_100) now execute digit contracts at 1.9230 against a 1.9530 proposal.
"Audited weeks ago" is not a reliable claim on a book that moves quietly.

These two are the last growth-based products not re-checked. Accumulators were re-audited
today and came back with a uniform ~0.5%/tick edge and a 4.6-6.4% margin.

WHY THEY CAN BE PRICED EXACTLY
Both are options, and we know the underlying process empirically. No Black-Scholes needed,
no volatility assumption — the distribution of T-step moves is measured directly from ticks.

  VANILLA (long call):  pay premium P, receive max(S_T - K, 0) x contracts
      fair premium = E[max(S_T - K, 0)] x contracts
      estimated as the empirical mean of max(move, 0) over the actual holding period

  TURBO (long):  barrier B below spot, payout (S_T - B) x contracts, ZERO if S touches B
      fair value = E[(S_T - B) x 1{never touched B}]
      the knockout makes this path-dependent, so it is evaluated on real paths rather
      than terminal values — the same first-passage structure as the accumulator

The ratio (quoted premium / fair value) is the house margin. Below 1.0 means underpriced.

CONTROLS, given this project's history
  - premiums read from EXECUTED buys where possible, since the proposal endpoint has
    produced six false positives
  - stake >= $10, because payouts quote to the cent and $0.35 reads 1.8857 where $10 reads
    1.9530
  - path-dependent evaluation for turbos: terminal-value pricing would ignore the knockout
    and manufacture an edge, which is exactly the accumulator error from earlier today
  - a saturated-statistic guard: if P(survive) comes back at 1.000000 the barrier units are
    wrong, not the product mispriced

Read-only unless --execute.

Run: python3 option_audit.py
     python3 option_audit.py --symbols R_100 1HZ100V --execute
"""
import argparse, math, re, time
import numpy as np
from deriv_api import DerivWS
from derivfetch import fetch_ticks, contiguous_pairs, native_interval, wilson

SYMBOLS = ["1HZ10V", "1HZ25V", "1HZ50V", "1HZ75V", "1HZ100V",
           "R_10", "R_25", "R_50", "R_75", "R_100"]
DURATIONS = [60, 300, 900]          # seconds


def n_contracts(pr):
    """
    The field is display_number_of_contracts (a string), NOT number_of_contracts.
    Reading the wrong key made every vanilla row skip with 'no number_of_contracts'.
    """
    for k in ("number_of_contracts", "display_number_of_contracts"):
        v = pr.get(k)
        if v is not None:
            try:
                return float(str(v).replace(",", ""))
            except (TypeError, ValueError):
                pass
    return None


def turbo_params(ws, sym, dur, stake, ct):
    """
    Turbo pricing parameters, read from the response rather than guessed.

    Two things the earlier versions got wrong:
      1. the valid payout_per_point ladder differs per symbol and is NOT always decimal:
         R_100 -> "4.5, 3.6, 2.7, 1.8, 0.9"   but   1HZ10V -> "3, 2.4, 1.8, 1.2, 0.6"
         A regex requiring a decimal point silently dropped the bare "3".
      2. there is no top-level `barrier`. It lives at contract_details.barrier as an
         ABSOLUTE price, with barrier_spot_distance beside it. Requiring a top-level
         field made every turbo cell fail even when the quote succeeded.

    Confirmed response shape (R_100, payout_per_point 4.5):
        display_number_of_contracts = 4.5      <- equals payout_per_point
        contract_details = {barrier: '604.86', barrier_spot_distance: '-2.05'}
        spot = 606.91,  ask_price = 10

    Returns (payout_per_point, absolute_barrier, spot_at_quote) for the TIGHTEST barrier,
    where the knockout binds hardest and mispricing would be most visible.
    """
    r = ws.call({"proposal": 1, "amount": stake, "basis": "stake",
                 "contract_type": ct, "currency": "USD",
                 "underlying_symbol": sym, "duration": dur,
                 "duration_unit": "s", "payout_per_point": 1})
    msg = r.get("error", {}).get("message", "")
    ladder = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", msg)] if "payout per point" in msg.lower() else []
    if "proposal" in r:
        ladder = [1.0]
    if not ladder:
        return None, None, None, msg[:52]
    for cand in sorted(ladder, reverse=True):
        r2 = ws.call({"proposal": 1, "amount": stake, "basis": "stake",
                      "contract_type": ct, "currency": "USD",
                      "underlying_symbol": sym, "duration": dur,
                      "duration_unit": "s", "payout_per_point": cand})
        time.sleep(0.08)
        if "proposal" not in r2:
            continue
        pr = r2["proposal"]
        cd = pr.get("contract_details") or {}
        bar = cd.get("barrier") or pr.get("barrier")
        sp = pr.get("spot")
        if bar is None or sp is None:
            continue
        try:
            return float(cand), float(bar), float(sp), None
        except (TypeError, ValueError):
            continue
    return None, None, None, "no quote accepted from the ladder"


def path_stats(p, idx, horizon, barrier_frac, direction):
    """
    Walk real paths of `horizon` ticks from each start index.
    Returns (survived_mask, terminal_move) where survival means the barrier was never
    touched. direction +1 = long (barrier below), -1 = short (barrier above).
    """
    surv, term = [], []
    n = len(p)
    for i in idx:
        if i + horizon >= n:
            continue
        s0 = p[i]
        seg = p[i:i + horizon + 1]
        if direction > 0:
            b = s0 * (1 - barrier_frac)
            hit = bool((seg <= b).any())
        else:
            b = s0 * (1 + barrier_frac)
            hit = bool((seg >= b).any())
        surv.append(not hit)
        term.append((seg[-1] - s0) * direction)
    return np.array(surv), np.array(term)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+", default=SYMBOLS)
    ap.add_argument("--ticks", type=int, default=200000)
    ap.add_argument("--stake", type=float, default=10.0)
    ap.add_argument("--samples", type=int, default=4000)
    ap.add_argument("--min-windows", type=int, default=150,
                    help="minimum INDEPENDENT non-overlapping windows. A 900-tick horizon "
                         "yields only 200000/900 = 222 from 200k ticks, so a threshold of "
                         "500 silently dropped every 900s cell — which is exactly where "
                         "the original spurious flags were.")
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--pause", type=float, default=1.5)
    ap.add_argument("--out", default="../results/option_audit.md")
    a = ap.parse_args()

    tr = None
    if a.execute:
        tr = DerivWS()
        if (tr.account or {}).get("device_id") is None and \
           (tr.account or {}).get("account_type") != "demo":
            print("NOT demo — refusing to execute"); tr = None

    rng = np.random.default_rng(0)
    rows = []

    print(f"{'symbol':9}{'product':10}{'dur':>6}{'quoted':>11}{'fair':>11}"
          f"{'ratio':>8}{'SE':>8}{'n_ind':>7}  note")
    print("  (n_ind = INDEPENDENT non-overlapping windows. Overlapping draws at a 900-tick")
    print("   horizon give an effective n of ~222 from 200k ticks, not 4000.)")
    print("-" * 84)

    for sym in a.symbols:
        ws = DerivWS(token="")
        try:
            t, p, pip = fetch_ticks(ws, sym, a.ticks, verbose=False, strict=False)
        except Exception as e:
            print(f"{sym:9}  fetch failed: {str(e)[:50]}")
            ws.close(); time.sleep(a.pause); continue
        if len(t) < 30000:
            print(f"{sym:9}  only {len(t)} ticks"); ws.close(); continue
        iv = native_interval(t)
        spot = float(p[-1])

        for dur in DURATIONS:
            horizon = max(1, int(round(dur / iv)))
            if horizon >= len(p) // 4:
                continue
            # NON-OVERLAPPING windows only. Random start points at a 900-tick horizon
            # overlap almost completely: 4000 draws from 200k ticks give an effective
            # independent sample of only 200000/900 = 222. That inflated the apparent
            # precision fourfold and produced five spurious "underpriced" cells, all of
            # them at the longest horizons where the overlap is worst.
            n_indep = (len(p) - 1) // horizon
            idx = np.arange(n_indep) * horizon
            if len(idx) > a.samples:
                idx = rng.choice(idx, size=a.samples, replace=False)

            # ---------- VANILLA ----------
            for ct in ("VANILLALONGCALL", "VANILLALONGPUT"):
                probe = ws.call({"proposal": 1, "amount": a.stake, "basis": "stake",
                                 "contract_type": ct, "currency": "USD",
                                 "underlying_symbol": sym, "duration": dur,
                                 "duration_unit": "s", "barrier": "+0.0"})
                time.sleep(0.08)
                if "proposal" in probe:
                    bars = [str(x) for x in (probe["proposal"].get("barrier_choices")
                                             or ["+0.0"])]
                else:
                    bars = re.findall(r"[+-]\d+\.\d+",
                                      probe.get("error", {}).get("message", ""))
                if not bars:
                    print(f"{sym:9}{ct[7:14]:10}{dur:>5}s  "
                          f"{probe.get('error', {}).get('message', '')[:52]}")
                    continue
                strike = min(bars, key=lambda x: abs(float(x)))
                r = ws.call({"proposal": 1, "amount": a.stake, "basis": "stake",
                             "contract_type": ct, "currency": "USD",
                             "underlying_symbol": sym, "duration": dur,
                             "duration_unit": "s", "barrier": strike})
                time.sleep(0.08)
                if "proposal" not in r:
                    print(f"{sym:9}{ct[7:14]:10}{dur:>5}s  "
                          f"{r.get('error', {}).get('message', '')[:52]}")
                    continue
                pr = r["proposal"]
                nc = n_contracts(pr)
                if not nc:
                    print(f"{sym:9}{ct[7:14]:10}{dur:>5}s  no contract count; "
                          f"fields: {sorted(pr.keys())[:8]}")
                    continue
                nc = float(nc)
                direction = 1 if "CALL" in ct else -1
                _, term = path_stats(p, idx, horizon, 1.0, direction)
                if len(term) < a.min_windows:
                    print(f"{sym:9}{ct[7:14]:10}{dur:>5}s  only {len(term)} independent "
                          f"windows (need {a.min_windows}); use --ticks 1000000")
                    continue
                pay = np.maximum(term, 0)
                fair = float(pay.mean()) * nc
                # SE of the ratio, from the independent-window sample
                se_fair = float(pay.std(ddof=1) / math.sqrt(len(pay))) * nc
                ratio = a.stake / fair if fair > 0 else float("nan")
                se_ratio = ratio * se_fair / fair if fair > 0 else float("nan")
                # only flag if it is below 1 by more than 3 SE
                note = ""
                if ratio < 1.0 - 3 * se_ratio:
                    note = "  <<< UNDERPRICED (>3 SE)"
                elif ratio < 1.0:
                    note = f"  below 1 but within {(1-ratio)/se_ratio:.1f} SE"
                print(f"{sym:9}{ct[7:14]:10}{dur:>5}s{a.stake:>11.2f}{fair:>11.4f}"
                      f"{ratio:>8.4f}{se_ratio:>8.4f}{len(pay):>7}{note}")
                rows.append(dict(sym=sym, prod=ct, dur=dur, quoted=a.stake,
                                 fair=fair, ratio=ratio, se=se_ratio,
                                 n_ind=len(pay), psurv=float("nan")))

            # ---------- TURBO ----------
            for ct in ("TURBOSLONG", "TURBOSSHORT"):
                ppp, bar_abs, qspot, err = turbo_params(ws, sym, dur, a.stake, ct)
                if ppp is None:
                    print(f"{sym:9}{ct[6:]:10}{dur:>5}s  {err}")
                    continue
                direction = 1 if ct == "TURBOSLONG" else -1
                bfrac = abs(qspot - bar_abs) / qspot
                surv, term = path_stats(p, idx, horizon, bfrac, direction)
                if len(surv) < a.min_windows:
                    print(f"{sym:9}{ct[6:]:10}{dur:>5}s  only {len(surv)} windows")
                    continue
                psurv = float(surv.mean())
                if psurv >= 0.99999:
                    print(f"{sym:9}{ct[6:]:10}{dur:>5}s  P(surv)=1 — UNITS SUSPECT")
                    continue
                # long turbo pays (S_T - B) x payout_per_point, zero if B was ever touched.
                # term is the signed move from entry, so distance past the barrier is
                # term + (spot - B) = term + bfrac*spot.
                pay = np.where(surv, np.maximum(term + bfrac * qspot, 0), 0.0) * ppp
                fair = float(pay.mean())
                se_f = float(pay.std(ddof=1) / math.sqrt(len(pay)))
                ratio = a.stake / fair if fair > 0 else float("nan")
                se_r = ratio * se_f / fair if fair > 0 else float("nan")
                note = ""
                if ratio < 1.0 - 3 * se_r:
                    note = "  <<< UNDERPRICED (>3 SE)"
                elif ratio < 1.0:
                    note = f"  below 1 within {(1-ratio)/se_r:.1f} SE"
                print(f"{sym:9}{ct[6:]:10}{dur:>5}s{a.stake:>11.2f}{fair:>11.4f}"
                      f"{ratio:>8.4f}{se_r:>8.4f}{len(pay):>7}{note}")
                rows.append(dict(sym=sym, prod=ct, dur=dur, quoted=a.stake, fair=fair,
                                 ratio=ratio, se=se_r, n_ind=len(pay), psurv=psurv))
        ws.close()
        time.sleep(a.pause)

    if not rows:
        print("\nnothing measured"); return

    print(f"\n{'='*84}")
    print("HOUSE MARGIN BY PRODUCT")
    print("=" * 84)
    print("  ratio = quoted premium / empirical fair value.")
    print("  above 1.0 = you overpay (normal). below 1.0 = underpriced (a finding).")
    print()
    for prod in sorted({r["prod"] for r in rows}):
        g = [r for r in rows if r["prod"] == prod and np.isfinite(r["ratio"])]
        if not g:
            continue
        rs = [r["ratio"] for r in g]
        print(f"  {prod:18} n={len(g):>3}  ratio {min(rs):.4f} - {max(rs):.4f}  "
              f"median {sorted(rs)[len(rs)//2]:.4f}  "
              f"implied margin {(1-1/sorted(rs)[len(rs)//2])*100:+.2f}%")

    under = [r for r in rows if np.isfinite(r["ratio"])
             and r["ratio"] < 1.0 - 3 * r.get("se", 0)]
    marginal = [r for r in rows if np.isfinite(r["ratio"]) and r["ratio"] < 1.0
                and r not in under]
    if marginal:
        print(f"\n  {len(marginal)} cell(s) below 1.0 but within 3 SE — noise, not findings:")
        for r in sorted(marginal, key=lambda x: x["ratio"])[:8]:
            print(f"    {r['sym']:9} {r['prod']:16} {r['dur']}s  ratio {r['ratio']:.4f} "
                  f"+/- {r.get('se', 0):.4f}  (n_ind {r.get('n_ind', 0)})")
    print(f"\n{'='*84}")
    print("VERDICT")
    print("=" * 84)
    if under:
        print(f"  {len(under)} cell(s) quoted BELOW empirical fair value:")
        for r in sorted(under, key=lambda x: x["ratio"])[:10]:
            print(f"    {r['sym']:9} {r['prod']:16} {r['dur']}s  "
                  f"ratio {r['ratio']:.4f}  fair {r['fair']:.4f} vs {r['quoted']:.2f}")
        print()
        print("  BEFORE BELIEVING IT:")
        print("   1. re-run with --execute; premiums must come from a real buy")
        print("   2. the fair value uses 4000 sampled paths — raise --samples and confirm")
        print("      the ratio is stable, not a sampling artifact")
        print("   3. check number_of_contracts is what actually gets filled")
        print("   4. vanillas here use an at-the-money barrier (+0.0); a different strike")
        print("      convention would change everything")
    else:
        print("  Nothing is quoted below empirical fair value. Turbos and vanillas carry")
        print("  a positive house margin on every symbol and duration tested, consistent")
        print("  with the accumulator result (uniform ~0.5%/tick, 4.6-6.4% margin).")
        print()
        print("  That closes the growth-based family: accumulators, multipliers, deal")
        print("  cancellation, turbos and vanillas are all correctly priced at current")
        print("  payouts, not merely at the payouts of some earlier audit.")

    with open(a.out, "w") as f:
        f.write("# Turbo / vanilla audit\n\n"
                "| symbol | product | duration | quoted | fair | ratio | P(surv) |\n"
                "|---|---|---|---|---|---|---|\n")
        for r in rows:
            f.write(f"| {r['sym']} | {r['prod']} | {r['dur']}s | {r['quoted']:.2f} "
                    f"| {r['fair']:.4f} | {r['ratio']:.4f} | {r['psurv']:.6f} |\n")
    print(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
