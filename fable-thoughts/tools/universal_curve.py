"""universal_curve.py — is the digit edge one universal law of sigma_pips?

THE QUESTION
redo_all.py on 1.2M clean JD100 ticks gave 12 monotone sigma bins fitting
EV% = 25.18 - 7.07*sigma, residual sd 0.30pp over a 7.3pp range. That is the one surviving
result in the project. But it is one symbol.

The naive follow-up is "is the -7.07 slope the same elsewhere". That is the wrong question:
-7.07 is the LOCAL slope over sigma 3.5-4.7. The wrapped-normal model predicts p(sigma)
flattens as sigma rises, because p -> 0.50 and cannot move further. Other symbols sit at
sigma 10-12, so a near-zero slope there would CONFIRM the model, not contradict it.

The right question is whether every symbol lies on ONE p(sigma) curve. If so:
  - the mechanism is physics, not a JD100 quirk
  - any symbol's edge is predictable from its sigma alone
  - each symbol's entry date follows from its own decay rate and its own payout

WHAT THIS DOES
  1. Pulls clean deduplicated ticks per symbol via derivfetch (the looping bug is fixed;
     see the correction in SESSION_2026-08-04.md).
  2. Computes P(next digit within +/-2 of entry) per sigma bin per symbol. This quantity is
     PAYOUT-INDEPENDENT, so symbols with different grids are directly comparable.
  3. Overlays every symbol against the wrapped-normal prediction and reports the residual.
     One curve = universal law. Symbol-dependent offsets = something else is going on.
  4. Applies each symbol's OWN payout to get its breakeven sigma, then its own measured
     decay rate to get a date.

PAYOUTS: JD100's proposal endpoint serves the pre-cut grid (proposal 1.886 vs executed
1.8286). Other symbols are unverified. --execute buys one contract per symbol on demo to
read the contracted payout from the buy response, which is authoritative. Without it the
breakeven column is provisional.

Read-only unless --execute (which risks ~$0.35 per symbol on demo).

Run: python3 universal_curve.py
     python3 universal_curve.py --symbols JD10 JD25 JD50 JD75 JD100 --execute
"""
import argparse, math, time, datetime as dt
import numpy as np
from deriv_api import DerivWS
from derivfetch import (fetch_ticks, contiguous_pairs, sigma_series, wilson,
                        native_interval, jump_threshold)

WIN5 = [0, 1, 2, 8, 9]
# JD family shares construction and differs in volatility -> the cleanest test.
# R_100 / 1HZ100V included as different-construction contrasts.
DEFAULT = ["JD100", "JD75", "JD50", "JD25", "JD10", "R_100", "1HZ100V"]


def wrapped_normal_win5(sigma):
    """P(offset in {-2..2} mod 10) under a wrapped normal of the given sigma_pips."""
    tot = 0.0
    for d in (-2, -1, 0, 1, 2):
        s = 0.0
        for k in range(-6, 7):
            x = d + 10 * k
            s += math.exp(-0.5 * (x / sigma) ** 2)
        tot += s
    norm = 0.0
    for d in range(10):
        s = 0.0
        for k in range(-6, 7):
            x = d + 10 * k
            s += math.exp(-0.5 * (x / sigma) ** 2)
        norm += s
    return tot / norm


def decay_rate(ws, sym, days=365):
    """Fitted log-linear drag from daily candles, per day."""
    r = ws.call({"ticks_history": sym, "style": "candles", "granularity": 86400,
                 "count": days, "end": "latest"})
    c = r.get("candles")
    if not c or len(c) < 30:
        return None
    cl = np.array([float(x["close"]) for x in c], dtype=float)
    cl = cl[cl > 0]
    if len(cl) < 30:
        return None
    b, _ = np.polyfit(np.arange(len(cl), dtype=float), np.log(cl), 1)
    return float(b)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+", default=DEFAULT)
    ap.add_argument("--ticks", type=int, default=300000)
    ap.add_argument("--binw", type=float, default=0.1)
    ap.add_argument("--min-n", type=int, default=4000)
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--stake", type=float, default=0.35)
    ap.add_argument("--out", default="../results/universal_curve.md")
    a = ap.parse_args()

    ws = DerivWS(token="")
    data = {}
    for sym in a.symbols:
        print(f"\n--- {sym} ---")
        try:
            t, p, pip = fetch_ticks(ws, sym, a.ticks, verbose=True)
        except Exception as e:
            print(f"  fetch failed: {e}")
            continue
        v = np.round(p * (10 ** pip)).astype(np.int64)
        iv = native_interval(t)
        cg = contiguous_pairs(t, v, iv)
        jt = jump_threshold(v, cg)
        print(f"  native interval {iv}s, jump cutoff {jt:.0f} pips")
        if cg.sum() < 10000:
            print(f"  only {int(cg.sum())} contiguous pairs — skipping")
            continue
        dig = (v % 10).astype(int)
        off = ((dig[1:] - dig[:-1]) % 10)[cg]
        win = np.isin(off, WIN5)
        sig = sigma_series(v, mask=cg, jump=jt)[cg]
        ok = ~np.isnan(sig)
        data[sym] = dict(win=win, sig=sig, ok=ok, pip=pip,
                         spot=float(p[-1]), t=t)
        print(f"  usable {int(ok.sum())}, sigma "
              f"{np.nanmin(sig):.2f}-{np.nanmax(sig):.2f}")

    # ---- payouts ---------------------------------------------------------
    print(f"\n{'='*82}")
    print("PAYOUTS")
    print("=" * 82)
    tr = None
    if a.execute:
        try:
            tr = DerivWS()
        except Exception as e:
            print(f"  auth failed ({type(e).__name__}: {e}) — proposals only")
            tr = None
        acct = (tr.account or {}) if tr else {}
        if tr and acct.get("account_type") != "demo":
            print(f"  NOT demo ({acct.get('account_type')}) — proposals only")
            tr = None
        else:
            print(f"  [demo {acct.get('account_id')}] buying one OVER4 per symbol\n")
    print(f"  {'symbol':10}{'proposal':>10}{'executed':>10}{'breakeven':>11}  note")
    pay = {}
    for sym in data:
        pr = ws.call({"proposal": 1, "amount": a.stake, "basis": "stake",
                      "contract_type": "DIGITOVER", "currency": "USD",
                      "underlying_symbol": sym, "duration": 1,
                      "duration_unit": "t", "barrier": "4"})
        prop = float(pr["proposal"]["payout"]) / a.stake if "proposal" in pr else None
        ex = None
        if tr is not None:
            b = tr.call({"buy": 1, "price": round(a.stake * 20, 2), "parameters":
                         dict(amount=a.stake, basis="stake",
                              contract_type="DIGITOVER", currency="USD",
                              underlying_symbol=sym, duration=1,
                              duration_unit="t", barrier="4")})
            if "buy" in b:
                ex = float(b["buy"]["payout"]) / float(b["buy"].get("buy_price") or a.stake)
            time.sleep(0.35)
        M = ex or prop
        pay[sym] = M
        note = ""
        if ex and prop and abs(ex - prop) / prop > 0.005:
            note = f"proposal lies by {(prop/ex-1)*100:.1f}%"
        elif not ex:
            note = "proposal only — unverified"
        print(f"  {sym:10}{prop or 0:>10.4f}{ex or 0:>10.4f}"
              f"{(1/M*100 if M else 0):>10.2f}%  {note}")

    # ---- the universal curve --------------------------------------------
    print(f"\n{'='*82}")
    print("P(+/-2 WINDOW) vs SIGMA — all symbols, payout-independent")
    print("=" * 82)
    print(f"  {'symbol':10}{'sigma':>7}{'n':>9}{'measured p':>12}"
          f"{'wrapped-normal':>15}{'resid':>9}")
    pts = []
    for sym, d in data.items():
        bins = np.round(d["sig"] / a.binw)
        for b in sorted(set(bins[d["ok"]].astype(int))):
            m = d["ok"] & (bins == b)
            nn = int(m.sum())
            if nn < a.min_n:
                continue
            s = b * a.binw
            k = int(d["win"][m].sum())
            pp = k / nn
            wn = wrapped_normal_win5(s)
            pts.append((sym, s, nn, pp, wn, pp - wn))
            print(f"  {sym:10}{s:>7.2f}{nn:>9}{pp:>12.5f}{wn:>15.5f}{pp-wn:>+9.5f}")

    if not pts:
        print("  no bins met the sample threshold"); ws.close(); return

    res = np.array([q[5] for q in pts])
    wts = np.array([q[2] for q in pts], dtype=float)
    mean_res = float(np.average(res, weights=wts))
    sd_res = float(np.sqrt(np.average((res - mean_res) ** 2, weights=wts)))
    print(f"\n  weighted mean residual {mean_res:+.5f}, sd {sd_res:.5f}")

    print(f"\n  per-symbol mean residual:")
    per = {}
    for sym in data:
        q = [x for x in pts if x[0] == sym]
        if not q:
            continue
        w = np.array([x[2] for x in q], dtype=float)
        r = float(np.average([x[5] for x in q], weights=w))
        per[sym] = r
        print(f"    {sym:10}{r:+.5f}  ({len(q)} bins)")
    spread = max(per.values()) - min(per.values()) if per else 0
    print(f"\n  spread of per-symbol offsets: {spread:.5f}")
    universal = spread < 0.01
    print(f"  -> {'ONE UNIVERSAL CURVE — the mechanism is physics'
                 if universal else 'symbol-dependent offsets — not a single law'}")

    # ---- per-symbol entry dates -----------------------------------------
    print(f"\n{'='*82}")
    print("WHEN DOES EACH SYMBOL ENTER ITS OWN ZONE?")
    print("=" * 82)
    print(f"  {'symbol':10}{'sigma now':>11}{'payout':>9}{'BE p':>9}"
          f"{'sigma needed':>14}{'drag/day':>11}{'days':>8}")
    for sym, d in data.items():
        M = pay.get(sym)
        if not M:
            continue
        be = 1 / M
        cur = float(np.nanmedian(d["sig"][d["ok"]][-30000:]))
        need = None
        for s10 in range(int(cur * 100), 100, -2):
            if wrapped_normal_win5(s10 / 100) >= be:
                need = s10 / 100
                break
        b = decay_rate(ws, sym)
        days = None
        if need and b and b < -1e-9:
            days = math.log(need / cur) / b
        ds = (f"{days:.0f}" if days is not None and 0 < days < 3650
              else ("NOW" if need and need >= cur else "--"))
        print(f"  {sym:10}{cur:>11.2f}{M:>9.4f}{be*100:>8.2f}%"
              f"{(need if need else 0):>14.2f}{(b or 0):>11.2e}{ds:>8}")
        time.sleep(0.2)

    ws.close()
    if tr:
        tr.close()

    md = ["# Universal p(sigma) curve\n\n",
          f"weighted mean residual vs wrapped normal {mean_res:+.5f}, sd {sd_res:.5f}, "
          f"per-symbol offset spread {spread:.5f}.\n\n",
          "| symbol | sigma | n | measured p | wrapped-normal | resid |\n",
          "|---|---:|---:|---:|---:|---:|\n"]
    for sym, s, nn, pp, wn, r in pts:
        md.append(f"| {sym} | {s:.2f} | {nn} | {pp:.5f} | {wn:.5f} | {r:+.5f} |\n")
    open(a.out, "w").write("".join(md))
    print(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
