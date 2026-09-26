"""info_vs_payout.py — measured information against each symbol's OWN payout, per contract.

THE ERROR THIS FIXES
info_universe.py hardcoded payout 1.80 for every symbol. That is JD100's CUT grid. The
other 19 digit-enabled symbols still carry the uncut 1.953 (confirmed on JD50: all 22
contracts unchanged at $10 stake). The information requirement is not a constant:

    payout 1.953  ->  breakeven 51.20%  ->  0.000418 bits
    payout 1.800  ->  breakeven 55.56%  ->  0.008924 bits

A 26x difference. Scoring every symbol at 1.80 made the bar 26x too high for 19 of 20
symbols and understated how close they are. R_25 is 4x short of the real requirement, not
90x.

WHAT THIS DOES INSTEAD
For every digit-enabled symbol and every digit contract type:

  1. read the actual payout — proposal, and EXECUTED buy with --execute, because on JD100
     the proposal endpoint serves the pre-cut grid and overstates by up to 25%
  2. convert that payout into the bits required for breakeven
  3. measure the mutual information available on that symbol
  4. measure the ACTUAL win rate per entry digit, which is what you would trade
  5. report both the information ceiling and the realised edge, against the real bar

Two quantities matter and they are different:
  - MI says whether an edge is POSSIBLE at that payout (an upper bound)
  - the measured per-digit win rate says whether one EXISTS (the realised value)

MI clearing the bar is necessary, not sufficient. The win rate clearing it is what counts.

CONTRACT TYPES COVERED
DIGITOVER and DIGITUNDER at every barrier, DIGITMATCH and DIGITDIFF at every barrier,
DIGITEVEN and DIGITODD. Each has its own payout and therefore its own bit requirement:
MATCH at 8.929 needs a far larger edge than OVER4 at 1.953.

Read-only unless --execute. Verify any candidate with executed buys before believing it.

Run: python3 info_vs_payout.py
     python3 info_vs_payout.py --symbols R_25 JD75 --execute
"""
import argparse, math, time
import numpy as np
from deriv_api import DerivWS
from derivfetch import fetch_ticks, contiguous_pairs, native_interval, wilson

# JD100 EXECUTED GRID — measured by payout_audit.py at $10 stake, 2026-08-12, and
# confirmed live 2026-08-29 (OVER5 quoted $4.37 on a $2 stake = 2.185).
#
# JD100 is the ONE symbol where the proposal endpoint serves the PRE-CUT book. Reading
# proposals there produced a fake +4.22% EV on OVER5 d=7; at the real 2.185 that cell is
# -6.17%. Every other digit symbol still carries the uncut 1.953 grid and its proposals
# are honest (verified on JD50: all 22 contracts unchanged).
JD100_EXECUTED = {
    ("DIGITOVER", "0"): 1.044, ("DIGITOVER", "1"): 1.166, ("DIGITOVER", "2"): 1.320,
    ("DIGITOVER", "3"): 1.521, ("DIGITOVER", "4"): 1.794, ("DIGITOVER", "5"): 2.186,
    ("DIGITOVER", "6"): 2.797, ("DIGITOVER", "7"): 3.883, ("DIGITOVER", "8"): 6.349,
    ("DIGITUNDER", "1"): 6.349, ("DIGITUNDER", "2"): 3.883, ("DIGITUNDER", "3"): 2.797,
    ("DIGITUNDER", "4"): 2.186, ("DIGITUNDER", "5"): 1.794, ("DIGITUNDER", "6"): 1.521,
    ("DIGITUNDER", "7"): 1.320, ("DIGITUNDER", "8"): 1.166, ("DIGITUNDER", "9"): 1.044,
    ("DIGITEVEN", None): 1.794, ("DIGITODD", None): 1.794,
}
for _b in range(10):
    JD100_EXECUTED[("DIGITMATCH", str(_b))] = 6.349
    JD100_EXECUTED[("DIGITDIFF", str(_b))] = 1.044

DIGIT_SYMBOLS = ["1HZ10V", "1HZ15V", "1HZ25V", "1HZ30V", "1HZ50V", "1HZ75V",
                 "1HZ90V", "1HZ100V", "JD10", "JD25", "JD50", "JD75", "JD100",
                 "RDBEAR", "RDBULL", "R_10", "R_25", "R_50", "R_75", "R_100"]


def h2(p):
    return 0.0 if p <= 0 or p >= 1 else -p*math.log2(p) - (1-p)*math.log2(1-p)


def bits_needed(M):
    """Bits required for a binary decision to clear breakeven at payout M."""
    be = 1.0 / M
    return 1 - h2(be) if 0 < be < 1 else float("inf")


def mi_mm(ctx, nxt):
    n = len(nxt)
    if n < 500:
        return float("nan")
    j = np.zeros((10, 10))
    np.add.at(j, (ctx, nxt), 1)
    pj = j / n
    px, py = pj.sum(1, keepdims=True), pj.sum(0, keepdims=True)
    with np.errstate(divide="ignore", invalid="ignore"):
        t = pj * np.log2(pj / (px @ py))
    raw = float(np.nansum(t))
    ox, oy = int((j.sum(1) > 0).sum()), int((j.sum(0) > 0).sum())
    return max(raw - max(ox*oy - ox - oy + 1, 0) / (2*n*math.log(2)), 0.0)


def contracts():
    out = [("DIGITOVER", str(b)) for b in range(9)]
    out += [("DIGITUNDER", str(b)) for b in range(1, 10)]
    out += [("DIGITMATCH", str(b)) for b in range(10)]
    out += [("DIGITDIFF", str(b)) for b in range(10)]
    out += [("DIGITEVEN", None), ("DIGITODD", None)]
    return out


def wins(ct, bar, nxt):
    if ct == "DIGITOVER":
        return nxt > int(bar)
    if ct == "DIGITUNDER":
        return nxt < int(bar)
    if ct == "DIGITMATCH":
        return nxt == int(bar)
    if ct == "DIGITDIFF":
        return nxt != int(bar)
    if ct == "DIGITEVEN":
        return nxt % 2 == 0
    return nxt % 2 == 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+", default=DIGIT_SYMBOLS)
    ap.add_argument("--ticks", type=int, default=300000)
    ap.add_argument("--stake", type=float, default=10.0)
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--pause", type=float, default=2.0)
    ap.add_argument("--out", default="../results/info_vs_payout.md")
    a = ap.parse_args()

    tr = None
    if a.execute:
        tr = DerivWS()
        if (tr.account or {}).get("account_type") != "demo":
            print("NOT demo — refusing to execute"); tr = None

    print(f"{'symbol':10}{'sigma':>10}{'MI':>11}{'ref M':>8}{'refNeed':>9}"
          f"{'MI/need':>8}   best cell        its M  itsNeed      EV   99%low")
    print("  (ref M / refNeed are the OVER4 reference used to score the SYMBOL.")
    print("   its M / itsNeed belong to the best cell itself — a different contract")
    print("   with its own payout. DIFF pays 1.096, OVER1 pays 1.232, OVER4 pays 1.953.)")
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
        dig = (v % 10).astype(int)
        ent, nxt = dig[:-1][cg], dig[1:][cg]
        st = np.diff(v)[cg].astype(float)
        nz = np.abs(st[st != 0])
        thr = np.percentile(nz, 99.5) if len(nz) > 100 else 20
        f = st[np.abs(st) <= thr]
        sg = math.sqrt((f**2).mean()) if len(f) else float("nan")
        mi = mi_mm(ent, nxt)
        half = len(ent) // 2

        best = None
        ref_payout = None
        for ct, bar in contracts():
            req = {"proposal": 1, "amount": a.stake, "basis": "stake",
                   "contract_type": ct, "currency": "USD",
                   "underlying_symbol": sym, "duration": 1, "duration_unit": "t"}
            if bar is not None:
                req["barrier"] = bar
            r = ws.call(req)
            if "proposal" not in r:
                continue
            M = float(r["proposal"]["payout"]) / a.stake
            if sym == "JD100":
                # override: proposals here are the pre-cut grid
                M = JD100_EXECUTED.get((ct, bar), M)
            if ct == "DIGITOVER" and bar == "4":
                ref_payout = M
            time.sleep(0.05)

            w = wins(ct, bar, nxt)
            for c in range(10):
                m = ent == c
                nn = int(m.sum())
                if nn < 3000:
                    continue
                k = int(w[m].sum())
                pw = k / nn
                lo, _ = wilson(k, nn)
                ev, evlo = pw * M - 1, lo * M - 1
                if best is None or evlo > best["evlo"]:
                    m1 = m[:half]; m2 = m[half:]
                    p1 = float(w[:half][m1].mean()) if m1.sum() > 800 else float("nan")
                    p2 = float(w[half:][m2].mean()) if m2.sum() > 800 else float("nan")
                    best = dict(ct=ct, bar=bar, c=c, n=nn, p=pw, M=M,
                                ev=ev, evlo=evlo, p1=p1, p2=p2)
        ws.close()

        if best is None or ref_payout is None:
            print(f"{sym:10}  no usable proposals"); time.sleep(a.pause); continue
        need = bits_needed(ref_payout)
        ratio = mi / need if need > 0 else float("nan")
        lbl = f"{best['ct'][5:]}{best['bar'] or ''} d={best['c']}"
        flag = "  <<<" if best["evlo"] > 0 else ""
        own_need = bits_needed(best["M"])
        print(f"{sym:10}{sg:>10.2f}{mi:>11.6f}{ref_payout:>8.3f}{need:>9.6f}"
              f"{ratio:>8.2f}   {lbl:14}{best['M']:>7.3f}{own_need:>9.6f}"
              f"{best['ev']*100:>+8.2f}%{best['evlo']*100:>+8.2f}%{flag}")
        rows.append(dict(sym=sym, sg=sg, mi=mi, M=ref_payout, need=need,
                         ratio=ratio, **{f"b_{k}": v for k, v in best.items()}))
        time.sleep(a.pause)

    if not rows:
        print("\nnothing measured"); return

    print(f"\n{'='*92}")
    print("INFORMATION CEILING vs REALISED EDGE")
    print("=" * 92)
    print("  MI/need >= 1 means an edge is POSSIBLE at that payout (necessary, not")
    print("  sufficient). The realised 99% lower bound is what would actually be traded.")
    print()
    poss = [r for r in rows if r["ratio"] >= 1]
    real = [r for r in rows if r["b_evlo"] > 0]
    print(f"  symbols where MI clears the bar:      {[r['sym'] for r in poss] or 'none'}")
    print(f"  symbols with a positive 99% low cell: {[r['sym'] for r in real] or 'none'}")

    if real and a.execute and tr:
        print(f"\n{'='*92}")
        print("EXECUTED PAYOUT CHECK")
        print("=" * 92)
        for r in real[:8]:
            par = dict(amount=a.stake, basis="stake", contract_type=r["b_ct"],
                       currency="USD", underlying_symbol=r["sym"],
                       duration=1, duration_unit="t")
            if r["b_bar"] is not None:
                par["barrier"] = r["b_bar"]
            b = tr.call({"buy": 1, "price": round(a.stake*30, 2), "parameters": par})
            if "buy" in b:
                bp = float(b["buy"].get("buy_price") or a.stake)
                Me = float(b["buy"]["payout"]) / bp
                lo = r["b_p"] - 2.576*math.sqrt(r["b_p"]*(1-r["b_p"])/r["b_n"])
                print(f"  {r['sym']:10}{r['b_ct'][5:]}{r['b_bar'] or '':3} "
                      f"quoted {r['b_M']:.4f} -> executed {Me:.4f}  "
                      f"EV {(r['b_p']*Me-1)*100:+.2f}% "
                      f"99%low {(lo*Me-1)*100:+.2f}%"
                      + ("  SURVIVES" if lo*Me-1 > 0 else "  dies on fill"))
            time.sleep(0.4)

    print(f"\n{'='*92}")
    print("VERDICT")
    print("=" * 92)
    if real:
        print("  Candidate cells exist. Check in this order:")
        print("   1. executed payout (proposals overstate by up to 25% on JD100)")
        print("   2. half-split p1 vs p2 — divergence means it is not stable")
        print("   3. fresh non-overlapping tick block")
    else:
        print("  No symbol has a digit cell with a positive 99% lower bound at its OWN")
        print("  payout. Scoring at each symbol's real grid rather than a flat 1.80 makes")
        print("  the bar 26x lower for the 19 uncut symbols — and nothing clears it even")
        print("  so. The information is simply not there outside JD100, and JD100's grid")
        print("  was cut past the point where its information suffices.")

    with open(a.out, "w") as f:
        f.write("# Information vs each symbol's own payout\n\n"
                "| symbol | sigma | MI | payout | bits needed | MI/need | best cell | EV | 99% low |\n"
                "|---|---|---|---|---|---|---|---|---|\n")
        for r in rows:
            f.write(f"| {r['sym']} | {r['sg']:.2f} | {r['mi']:.6f} | {r['M']:.4f} "
                    f"| {r['need']:.6f} | {r['ratio']:.2f} "
                    f"| {r['b_ct']}{r['b_bar'] or ''} d={r['b_c']} "
                    f"| {r['b_ev']*100:+.2f}% | {r['b_evlo']*100:+.2f}% |\n")
    print(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
