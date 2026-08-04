"""surface_scan.py — price EVERY contract family off real ticks. Model-free.

THE BLIND SPOT THIS FIXES
This repo found one edge (digit pmf on JD100) and then spent months generating variants of
it. But contracts_for on JD100 lists fifteen families:

    accumulator, asian, callput, callputequal, digits, endsinout, higherlower,
    highlowticks, multiplier, reset, runs, staysinout, touchnotouch, turbos, vanilla

SEVEN have never been tested: asian, endsinout, highlowticks, runs, staysinout, turbos,
vanilla.

And the mechanism generalises. sigma_pips drift is not a digit phenomenon — it is a TICK
DISCRETENESS phenomenon. P(tie) ~ 1/(sigma*sqrt(2pi)) grows as spot decays, which changes
the three-outcome structure of every tick:

    measured JD100 @ sigma 3.88:  P(up)=0.4533  P(tie)=0.1011  P(down)=0.4456

A binary model assumes 0.5/0/0.5. For RUNS the gap compounds with N:

    N=2  binary 0.2500  actual 0.2055   binary overstates by 21.7%
    N=5  binary 0.0313  actual 0.0191   binary overstates by 63.3%
    N=7  binary 0.0078  actual 0.0039   binary overstates by 98.7%

Any contract paying on the COMPLEMENT of a run is systematically cheap if priced binary.
And P(5 ups) has fallen ~30% since sigma 6.0, so a static payout drifts out of calibration
exactly the way the digit grid did.

METHOD — no model at all
For each contract, implement its payoff rule as a function of the tick path, backtest on a
large block of real ticks to get the empirical win rate, and compare against the payout.

    EV = P_empirical * payout - 1

CONTROLS: digits and callput/callputequal have known answers from earlier work. If the
harness does not reproduce them, nothing else in the output is trustworthy. The control
check runs first and the script says so loudly if it fails.

PAYOUTS: payout_audit.py established the proposal endpoint serves the PRE-CUT grid
(proposal 1.886 vs executed 1.8286) with a graduated lie that vanishes on low payouts and
reaches 25% on the tails. So proposals are used for the broad scan, then --execute
re-measures only the flagged candidates against real demo fills.

Run: python3 surface_scan.py                     # scan, proposals, no trades
     python3 surface_scan.py --execute           # re-measure flagged cells on demo
     python3 surface_scan.py --symbol 1HZ100V
"""
import argparse, json, math, time
import numpy as np
from deriv_api import DerivWS


# ---------------------------------------------------------------- payoff rules
# Each rule takes (v, i, N, **kw) where v is the int-pip price array and i the entry
# index, and returns True (win), False (lose), or None (undefined at this point).

def r_runs_up(v, i, N, **kw):
    return all(v[i + k + 1] > v[i + k] for k in range(N))


def r_runs_down(v, i, N, **kw):
    return all(v[i + k + 1] < v[i + k] for k in range(N))


def r_call(v, i, N, **kw):
    return v[i + N] > v[i]


def r_put(v, i, N, **kw):
    return v[i + N] < v[i]


def r_calle(v, i, N, **kw):
    return v[i + N] >= v[i]


def r_pute(v, i, N, **kw):
    return v[i + N] <= v[i]


def r_asian_up(v, i, N, **kw):
    return v[i + N] > np.mean(v[i + 1: i + N + 1])


def r_asian_down(v, i, N, **kw):
    return v[i + N] < np.mean(v[i + 1: i + N + 1])


def r_hi_tick(v, i, N, pos=1, **kw):
    """highlowticks: is tick `pos` (1-indexed) the highest of the N?"""
    seg = v[i + 1: i + N + 1]
    return int(np.argmax(seg)) == (pos - 1)


def r_lo_tick(v, i, N, pos=1, **kw):
    seg = v[i + 1: i + N + 1]
    return int(np.argmin(seg)) == (pos - 1)


def r_stays_in(v, i, N, w=None, **kw):
    """staysinout: path stays within +/- w pips of entry for all N ticks."""
    seg = v[i + 1: i + N + 1]
    return bool(np.all(np.abs(seg - v[i]) <= w))


def r_goes_out(v, i, N, w=None, **kw):
    seg = v[i + 1: i + N + 1]
    return bool(np.any(np.abs(seg - v[i]) > w))


def r_ends_in(v, i, N, w=None, **kw):
    return abs(v[i + N] - v[i]) <= w


def r_ends_out(v, i, N, w=None, **kw):
    return abs(v[i + N] - v[i]) > w


def r_touch(v, i, N, w=None, **kw):
    seg = v[i + 1: i + N + 1]
    return bool(np.any(np.abs(seg - v[i]) >= w))


def r_notouch(v, i, N, w=None, **kw):
    seg = v[i + 1: i + N + 1]
    return bool(np.all(np.abs(seg - v[i]) < w))


def r_digit_over(v, i, N, bar=None, **kw):
    return (v[i + N] % 10) > bar


def r_digit_under(v, i, N, bar=None, **kw):
    return (v[i + N] % 10) < bar


def r_digit_match(v, i, N, bar=None, **kw):
    return (v[i + N] % 10) == bar


def r_digit_diff(v, i, N, bar=None, **kw):
    return (v[i + N] % 10) != bar


def r_even(v, i, N, **kw):
    return (v[i + N] % 10) % 2 == 0


def r_odd(v, i, N, **kw):
    return (v[i + N] % 10) % 2 == 1


# name -> (rule, N, kwargs, deriv_contract_type, deriv_params, is_control)
def build_catalog():
    """
    Durations and availability taken from family_matrix.py output (contracts_for),
    NOT guessed. On 1HZ100V / R_* the real tick-expiry constraints are:

        RUNHIGH/RUNLOW      runs           2t - 5t
        TICKHIGH/TICKLOW    highlowticks   5t only   (selected_tick param)
        ASIANU/ASIAND       asian          5t - 10t
        HIGHER/LOWER        higherlower    5t - 10t  (barrier)
        RESETCALL/RESETPUT  reset          5t - 10t
        ONETOUCH/NOTOUCH    touchnotouch   5t - 10t  (barrier)
        TURBOSLONG/SHORT    turbos         5t - 10t  (barrier)
        CALL/PUT/CALLE/PUTE callput        1t - 10t
        DIGIT*              digits         1t - 10t

    JD100 offers ONLY digits, callput, callputequal, multiplier — so runs/asian/
    highlowticks cannot be traded there and were wasted backtests in the first pass.
    """
    C = []
    # runs: 2t-5t
    for N in (2, 3, 4, 5):
        C.append((f"RUNS_UP_{N}", r_runs_up, N, {}, "RUNHIGH",
                  dict(duration=N, duration_unit="t"), False))
        C.append((f"RUNS_DOWN_{N}", r_runs_down, N, {}, "RUNLOW",
                  dict(duration=N, duration_unit="t"), False))
    # asian: 5t-10t
    for N in (5, 6, 8, 10):
        C.append((f"ASIAN_UP_{N}", r_asian_up, N, {}, "ASIANU",
                  dict(duration=N, duration_unit="t"), False))
        C.append((f"ASIAN_DOWN_{N}", r_asian_down, N, {}, "ASIAND",
                  dict(duration=N, duration_unit="t"), False))
    # highlowticks: 5t ONLY, selected_tick 1..5
    for pos in range(1, 6):
        C.append((f"HITICK_5_{pos}", r_hi_tick, 5, dict(pos=pos), "TICKHIGH",
                  dict(duration=5, duration_unit="t", selected_tick=pos), False))
        C.append((f"LOTICK_5_{pos}", r_lo_tick, 5, dict(pos=pos), "TICKLOW",
                  dict(duration=5, duration_unit="t", selected_tick=pos), False))
    # touchnotouch: 5t-10t, barrier in price offset
    for N in (5, 10):
        for off in ("+0.50", "+1.00", "+2.00"):
            C.append((f"TOUCH_{N}_{off}", None, N, dict(off=off), "ONETOUCH",
                      dict(duration=N, duration_unit="t", barrier=off), False))
            C.append((f"NOTOUCH_{N}_{off}", None, N, dict(off=off), "NOTOUCH",
                      dict(duration=N, duration_unit="t", barrier=off), False))
    # controls — known answers, 1t
    C.append(("CTRL_CALL_1", r_call, 1, {}, "CALL", dict(duration=1, duration_unit="t"), True))
    C.append(("CTRL_CALLE_1", r_calle, 1, {}, "CALLE", dict(duration=1, duration_unit="t"), True))
    C.append(("CTRL_PUTE_1", r_pute, 1, {}, "PUTE", dict(duration=1, duration_unit="t"), True))
    for b in (3, 4):
        C.append((f"CTRL_OVER{b}", r_digit_over, 1, dict(bar=b), "DIGITOVER",
                  dict(duration=1, duration_unit="t", barrier=str(b)), True))
    C.append(("CTRL_MATCH0", r_digit_match, 1, dict(bar=0), "DIGITMATCH",
              dict(duration=1, duration_unit="t", barrier="0"), True))
    C.append(("CTRL_EVEN", r_even, 1, {}, "DIGITEVEN",
              dict(duration=1, duration_unit="t"), True))
    return C


def backtest(v, rule, N, kw, stride=1, max_pts=400000):
    n = len(v)
    idx = range(0, n - N - 1, stride)
    wins = tot = 0
    for i in idx:
        if tot >= max_pts:
            break
        r = rule(v, i, N, **kw)
        if r is None:
            continue
        wins += bool(r)
        tot += 1
    return wins, tot


def wilson(k, n, z=2.576):
    if n == 0:
        return 0.0, 1.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return c - h, c + h


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="1HZ100V")
    ap.add_argument("--ticks", type=int, default=600000)
    ap.add_argument("--stride", type=int, default=1)
    ap.add_argument("--stake", type=float, default=1.0)
    ap.add_argument("--execute", action="store_true",
                    help="re-measure flagged cells against real demo fills")
    ap.add_argument("--min-ev", type=float, default=0.01)
    ap.add_argument("--out", default="../results/surface_scan.md")
    a = ap.parse_args()

    ws = DerivWS(token="")
    print(f"fetching {a.ticks} ticks of {a.symbol}...")
    _, prices, pip = ws.history_paged(a.symbol, a.ticks, sleep=0.2)
    pip = int(pip)
    v = np.round(np.asarray(prices, dtype=float) * (10 ** pip)).astype(np.int64)
    print(f"got {len(v)} ticks, spot {prices[-1]}")

    d = np.diff(v)
    pu, pt, pd = (d > 0).mean(), (d == 0).mean(), (d < 0).mean()
    st = d[np.abs(d) <= 20]
    sg = float(np.sqrt((st.astype(float) ** 2).mean()))
    print(f"sigma_pips {sg:.3f}   P(up) {pu:.4f}  P(tie) {pt:.4f}  P(down) {pd:.4f}")
    print(f"binary model would say 0.5 / 0 / 0.5 — tie mass is {pt*100:.2f}%\n")

    cat = build_catalog()
    rows = []
    print(f"{'contract':18}{'n':>9}{'P_emp':>9}{'payout':>9}{'breakeven':>11}"
          f"{'EV':>9}{'Wilson99 EV':>22}")
    print("-" * 92)

    for name, rule, N, kw, ct, params, is_ctrl in cat:
        if rule is None:
            # barrier contracts: convert the price offset to pips, then backtest
            off = kw.get("off")
            w = int(round(abs(float(off)) * (10 ** pip)))
            rule = r_touch if name.startswith("TOUCH") else r_notouch
            kw = dict(w=w)
        wins, tot = backtest(v, rule, N, kw, a.stride)
        if tot < 1000:
            continue
        p = wins / tot
        lo, hi = wilson(wins, tot)

        pay = None
        if ct:
            req = {"proposal": 1, "amount": a.stake, "basis": "stake",
                   "contract_type": ct, "currency": "USD",
                   "underlying_symbol": a.symbol, "duration_unit": "t"}
            req.update(params)
            r = ws.call(req)
            if "proposal" in r:
                pay = float(r["proposal"]["payout"]) / a.stake
            else:
                e = r.get("error", {})
                print(f"  [proposal failed] {name:18} {ct:14} "
                      f"[{e.get('code')}] {str(e.get('message'))[:60]}")
            time.sleep(0.1)

        ev = (p * pay - 1) if pay else None
        ev_lo = (lo * pay - 1) if pay else None
        ev_hi = (hi * pay - 1) if pay else None
        rows.append(dict(name=name, n=tot, p=p, lo=lo, hi=hi, pay=pay,
                         ev=ev, ev_lo=ev_lo, ev_hi=ev_hi, ctrl=is_ctrl, ct=ct,
                         params=params))
        ps = f"{pay:.4f}" if pay else "--"
        bs = f"{1/pay*100:.2f}%" if pay else "--"
        es = f"{ev*100:+.2f}%" if ev is not None else "--"
        cis = f"[{ev_lo*100:+.2f}%,{ev_hi*100:+.2f}%]" if ev is not None else ""
        print(f"{name:18}{tot:>9}{p:>9.5f}{ps:>9}{bs:>11}{es:>9}{cis:>22}")

    # ---- controls ------------------------------------------------------
    print(f"\n{'='*92}")
    print("CONTROL CHECK — harness must reproduce known results")
    print(f"{'='*92}")
    ctrls = [r for r in rows if r["ctrl"]]
    ok = True
    for r in ctrls:
        note = ""
        if r["name"] == "CTRL_CALLE_1":
            note = f"expect P ~ P(up)+P(tie) = {pu+pt:.4f}"
            if abs(r["p"] - (pu + pt)) > 0.01:
                note += "  MISMATCH"; ok = False
        if r["name"] == "CTRL_MATCH0":
            note = "expect P ~ 0.10"
            if abs(r["p"] - 0.10) > 0.02:
                note += "  MISMATCH"; ok = False
        if r["name"] == "CTRL_EVEN":
            note = "expect P ~ 0.50"
            if abs(r["p"] - 0.50) > 0.03:
                note += "  MISMATCH"; ok = False
        print(f"  {r['name']:16} P={r['p']:.5f}  {note}")
    print(f"\n  harness: {'OK' if ok else 'FAILED — do not trust anything above'}")

    # ---- candidates ----------------------------------------------------
    cands = [r for r in rows if not r["ctrl"] and r["ev_lo"] is not None
             and r["ev_lo"] > a.min_ev]
    cands.sort(key=lambda r: -r["ev_lo"])
    print(f"\n{'='*92}")
    print(f"CANDIDATES — Wilson-99 LOWER bound on EV above +{a.min_ev*100:.0f}%")
    print(f"{'='*92}")
    if not cands:
        print("  none. every priced contract is at or below breakeven on real ticks.")
    for r in cands:
        print(f"  {r['name']:18} P={r['p']:.5f} payout={r['pay']:.4f} "
              f"EV={r['ev']*100:+.2f}%  99% low {r['ev_lo']*100:+.2f}%")
    print("\n  NOTE: payouts above are PROPOSALS, which serve the pre-cut grid")
    print("  (payout_audit: proposal 1.886 vs executed 1.8286). Re-run with --execute.")

    # ---- execute stage -------------------------------------------------
    if a.execute and cands:
        tr = DerivWS()
        acct = tr.account or {}
        if acct.get("account_type") != "demo":
            print(f"\nNOT demo ({acct.get('account_type')}) — skipping execution.")
        else:
            print(f"\n{'='*92}")
            print(f"EXECUTED PAYOUTS [demo {acct.get('account_id')}]")
            print(f"{'='*92}")
            for r in cands:
                pr = {"amount": a.stake, "basis": "stake", "contract_type": r["ct"],
                      "currency": "USD", "underlying_symbol": a.symbol,
                      "duration_unit": "t"}
                pr.update(r["params"])
                b = tr.call({"buy": 1, "price": round(a.stake * 30, 2), "parameters": pr})
                if "buy" in b:
                    pay = float(b["buy"]["payout"]) / float(b["buy"].get("buy_price") or a.stake)
                    ev = r["p"] * pay - 1
                    evlo = r["lo"] * pay - 1
                    flag = "STILL +EV" if evlo > 0 else "dies on real fill"
                    print(f"  {r['name']:18} proposal {r['pay']:.4f} -> executed {pay:.4f}"
                          f"   EV {ev*100:+.2f}%  99% low {evlo*100:+.2f}%   {flag}")
                else:
                    print(f"  {r['name']:18} buy error: "
                          f"{b.get('error', {}).get('message', '')[:40]}")
                time.sleep(0.35)

    md = [f"# Surface scan — {a.symbol}\n\n",
          f"{len(v)} ticks, spot {prices[-1]}, sigma_pips {sg:.3f}. ",
          f"P(up) {pu:.4f}, P(tie) {pt:.4f}, P(down) {pd:.4f}.\n\n",
          "Model-free: every contract's payoff rule backtested on real ticks.\n\n",
          "| contract | n | P_emp | payout | breakeven | EV | Wilson99 EV |\n",
          "|---|---:|---:|---:|---:|---:|---|\n"]
    for r in rows:
        pay_s = f"{r['pay']:.4f}" if r["pay"] else ""
        be_s = f"{1 / r['pay'] * 100:.2f}%" if r["pay"] else ""
        ev_s = f"{r['ev'] * 100:+.2f}%" if r["ev"] is not None else ""
        ci_s = (f"[{r['ev_lo'] * 100:+.2f}%, {r['ev_hi'] * 100:+.2f}%]"
                if r["ev"] is not None else "")
        md.append(f"| {r['name']} | {r['n']} | {r['p']:.5f} | {pay_s} | "
                  f"{be_s} | {ev_s} | {ci_s} |\n")
    open(a.out, "w").write("".join(md))
    print(f"\nwrote {a.out}")
    ws.close()


if __name__ == "__main__":
    main()
