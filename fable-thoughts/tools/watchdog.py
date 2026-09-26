"""watchdog.py — monitor the three conditions that could create a NEW edge.

WHY A MONITOR RATHER THAN ANOTHER SCAN
The search is closed by argument, not exhaustion. I(past ; next) = 0.0023 bits bounds every
predictor; linearity closes every combination; convexity closes every Dutch book; and digit
contracts are offered only on symbols whose digits are uniform.

The one edge that ever existed had a specific shape: **a payout grid that was correct at
launch and became stale as the instrument's physics drifted.** JD100's sigma_pips fell with
spot while pip_size stayed frozen at 0.01, and the fixed grid stopped matching the
distribution. That is not something you find by analysis — it is something you notice when
it happens.

So the correct posture now is a watchdog, not a hunt. Three conditions, any of which could
recreate that shape:

  1. A NEW SYMBOL appears. Every launch is a fresh chance for a grid to be set on
     assumptions that do not match behaviour. Check sigma, check whether digits are offered,
     compare the grid to the empirical distribution.

  2. AN EXISTING SYMBOL DRIFTS into a concentrated regime while digits remain offered.
     Only JD100 is close, but sigma moves and the threshold is known.

  3. PROPOSAL AND EXECUTED PAYOUTS DIVERGE anywhere. On JD100 the proposal serves the
     pre-cut grid and OVERSTATES — that direction is a trap and has produced five false
     positives. But nobody has ever scanned for the REVERSE: executed > proposal would mean
     the execution grid is stale in your favour. It has never been checked, on any symbol,
     at any time. It is also the only one of the three that could appear and vanish within
     hours, which is exactly why it needs a daemon rather than an occasional look.

Also tracks pip_size. A change from 0.01 to 0.001 on JD100 takes sigma_pips from 3.8 to 38
and closes that instrument permanently; the TRANSITION itself is untested and is the kind of
infrastructure event that produces transient mispricing.

STATE is persisted to a JSON registry so successive runs can diff against history rather
than re-deriving everything.

Read-only by default. --execute performs one minimum-stake demo buy per digit symbol to read
the true executed payout, which is the only trustworthy source.

Run once:        python3 watchdog.py
Run continuous:  python3 watchdog.py --loop 21600     (every 6 hours)
With execution:  python3 watchdog.py --execute
"""
import argparse, json, math, os, time, datetime as dt
import numpy as np
from deriv_api import DerivWS
from derivfetch import fetch_ticks, contiguous_pairs, native_interval, wilson

REGISTRY = "../results/watchdog_registry.json"
SIGMA_ALERT = 5.0          # digits become non-uniform below roughly this
DIGIT_TYPES = ("DIGITOVER", "DIGITUNDER", "DIGITMATCH",
               "DIGITDIFF", "DIGITEVEN", "DIGITODD")

# JD100 executed grid — proposals there serve the pre-cut book and overstate by up to 25%.
JD100_EXECUTED = {("DIGITOVER", "4"): 1.794, ("DIGITUNDER", "5"): 1.794,
                  ("DIGITOVER", "5"): 2.186, ("DIGITUNDER", "4"): 2.186}


def h2(p):
    return 0.0 if p <= 0 or p >= 1 else -p*math.log2(p) - (1-p)*math.log2(1-p)


def load_registry():
    if os.path.exists(REGISTRY):
        try:
            return json.load(open(REGISTRY))
        except Exception:
            pass
    return {"symbols": {}, "history": []}


def save_registry(reg):
    os.makedirs(os.path.dirname(REGISTRY), exist_ok=True)
    json.dump(reg, open(REGISTRY, "w"), indent=1, sort_keys=True)


def measure(ws, sym, n_ticks):
    """sigma_pips and the +/-2 window win rate, from clean ticks."""
    try:
        t, p, pip = fetch_ticks(ws, sym, n_ticks, verbose=False, strict=False)
    except Exception as e:
        return None, str(e)[:60]
    if len(t) < 15000:
        return None, f"only {len(t)} ticks"
    v = np.round(p * (10 ** pip)).astype(np.int64)
    iv = native_interval(t)
    cg = contiguous_pairs(t, v, iv)
    st = np.diff(v)[cg].astype(float)
    nz = np.abs(st[st != 0])
    if len(nz) < 200:
        return None, "degenerate steps"
    thr = np.percentile(nz, 99.5)
    f = st[np.abs(st) <= thr]
    sg = math.sqrt((f ** 2).mean()) if len(f) else float("nan")
    dig = (v % 10).astype(int)
    ent, nxt = dig[:-1][cg], dig[1:][cg]
    off = (nxt - ent) % 10
    win = np.isin(off, [0, 1, 2, 8, 9])
    return dict(sigma=sg, pip=pip, iv=iv, spot=float(p[-1]),
                n=len(t), p_win=float(win.mean()),
                k=int(win.sum())), None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticks", type=int, default=100000)
    ap.add_argument("--stake", type=float, default=10.0,
                    help="MUST be >= 10. Payouts quote to the cent, so at $0.35 the "
                         "resolution is 0.029 and 1.953 reads back as 1.8857 — a 3.4pp "
                         "error in breakeven that would mask a real signal.")
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--loop", type=float, default=0,
                    help="seconds between runs; 0 = run once")
    ap.add_argument("--pause", type=float, default=1.5)
    a = ap.parse_args()

    while True:
        if a.stake < 5:
            print(f"WARNING: stake ${a.stake} is too small to resolve payouts. "
                  f"Resolution is {0.01/a.stake:.4f}; use --stake 10 or more.")
        reg = load_registry()
        known = reg["symbols"]
        stamp = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
        alerts = []
        print(f"\n{'='*80}\nWATCHDOG {stamp}\n{'='*80}")

        ws = DerivWS(token="")
        r = ws.call({"active_symbols": "brief"})
        live = {}
        for s in r.get("active_symbols", []):
            u = s.get("underlying_symbol") or s.get("symbol")
            if u:
                live[u] = s.get("underlying_symbol_name") or ""

        # ---- 1. new symbols ---------------------------------------------
        new = sorted(set(live) - set(known))
        gone = sorted(set(known) - set(live))
        print(f"\n  {len(live)} symbols live, {len(known)} known")
        if gone:
            print(f"  DELISTED: {gone}")
            alerts.append(f"delisted: {gone}")
        if new:
            print(f"  *** NEW SYMBOLS: {new} ***")
            alerts.append(f"NEW SYMBOLS: {new}")
        else:
            print("  no new symbols")

        # ---- 2. which offer digits, and their sigma ---------------------
        digit_syms = []
        for sym in sorted(live):
            cf = ws.call({"contracts_for": sym}).get("contracts_for", {})
            types = {x.get("contract_type") for x in cf.get("available", [])}
            has_digits = bool(types & set(DIGIT_TYPES))
            prev = known.get(sym, {})
            if has_digits:
                digit_syms.append(sym)
            if prev and prev.get("digits") is not None and prev["digits"] != has_digits:
                msg = f"{sym}: digit availability changed {prev['digits']} -> {has_digits}"
                print(f"  *** {msg} ***")
                alerts.append(msg)
            known.setdefault(sym, {})["digits"] = has_digits
            known[sym]["name"] = live[sym]
            time.sleep(0.15)

        print(f"  {len(digit_syms)} symbols offer digit contracts")

        # ---- 3. sigma, pip_size, and payout checks ----------------------
        print(f"\n  {'symbol':10}{'sigma':>10}{'pip':>5}{'prop':>8}{'exec':>8}"
              f"{'p(win)':>9}{'BE':>8}{'EV':>9}  note")
        print("  " + "-" * 74)
        tr = None
        if a.execute:
            tr = DerivWS()
            if (tr.account or {}).get("account_type") != "demo":
                print("  NOT demo — skipping execution"); tr = None

        for sym in digit_syms:
            m, err = measure(ws, sym, a.ticks)
            if m is None:
                print(f"  {sym:10}  {err}")
                time.sleep(a.pause); continue
            prev = known.get(sym, {})

            # pip_size change is the instrument-killer
            if prev.get("pip") is not None and prev["pip"] != m["pip"]:
                msg = (f"{sym}: PIP_SIZE CHANGED {prev['pip']} -> {m['pip']} "
                       f"(sigma_pips scales by 10^{m['pip']-prev['pip']})")
                print(f"  *** {msg} ***")
                alerts.append(msg)

            pr = ws.call({"proposal": 1, "amount": a.stake, "basis": "stake",
                          "contract_type": "DIGITOVER", "currency": "USD",
                          "underlying_symbol": sym, "duration": 1,
                          "duration_unit": "t", "barrier": "4"})
            Mp = (float(pr["proposal"]["payout"]) / a.stake
                  if "proposal" in pr else float("nan"))
            Me = float("nan")
            if tr is not None:
                b = tr.call({"buy": 1, "price": round(a.stake * 30, 2), "parameters":
                             dict(amount=a.stake, basis="stake",
                                  contract_type="DIGITOVER", currency="USD",
                                  underlying_symbol=sym, duration=1,
                                  duration_unit="t", barrier="4")})
                if "buy" in b:
                    bp = float(b["buy"].get("buy_price") or a.stake)
                    Me = float(b["buy"]["payout"]) / bp
                time.sleep(0.35)
            elif sym == "JD100":
                Me = JD100_EXECUTED[("DIGITOVER", "4")]

            M = Me if np.isfinite(Me) else Mp
            be = 1.0 / M if np.isfinite(M) and M > 0 else float("nan")
            ev = m["p_win"] * M - 1 if np.isfinite(M) else float("nan")
            lo, _ = wilson(m["k"], m["n"])
            evlo = lo * M - 1 if np.isfinite(M) else float("nan")

            note = ""
            # THE UNTESTED DIRECTION: executed ABOVE proposal
            if np.isfinite(Me) and np.isfinite(Mp) and Me > Mp * 1.002:
                msg = (f"{sym}: EXECUTED ABOVE PROPOSAL — {Me:.4f} vs {Mp:.4f}. "
                       f"Execution grid may be stale in your favour.")
                note = "  <<< EXEC > PROP"
                alerts.append(msg)
            elif np.isfinite(Me) and np.isfinite(Mp) and Mp > Me * 1.01:
                note = f"  proposal overstates {(Mp/Me-1)*100:.1f}%"

            if m["sigma"] < SIGMA_ALERT:
                note += f"  SIGMA LOW ({m['sigma']:.2f})"
                alerts.append(f"{sym}: sigma {m['sigma']:.2f} below {SIGMA_ALERT}")
            if np.isfinite(evlo) and evlo > 0:
                note += "  <<< POSITIVE 99% LOW"
                alerts.append(f"{sym}: EV {ev*100:+.2f}%, 99% low {evlo*100:+.2f}%")

            print(f"  {sym:10}{m['sigma']:>10.2f}{m['pip']:>5}{Mp:>8.4f}{Me:>8.4f}"
                  f"{m['p_win']:>9.5f}{be*100:>7.2f}%{ev*100:>+8.2f}%{note}")

            known[sym].update(pip=m["pip"], sigma=round(m["sigma"], 4),
                              spot=round(m["spot"], 4), proposal=round(Mp, 4)
                              if np.isfinite(Mp) else None,
                              executed=round(Me, 4) if np.isfinite(Me) else None,
                              checked=stamp)
            time.sleep(a.pause)

        ws.close()
        if tr:
            tr.close()

        reg["history"].append(dict(t=stamp, n_symbols=len(live),
                                   n_digit=len(digit_syms), alerts=alerts))
        reg["history"] = reg["history"][-500:]
        save_registry(reg)

        print(f"\n{'='*80}")
        if alerts:
            print(f"{len(alerts)} ALERT(S)")
            for x in alerts:
                print(f"  - {x}")
            print("\n  Any positive-EV alert must be re-checked against an EXECUTED buy")
            print("  before acting. Proposals have produced five false positives here.")
        else:
            print("No alerts. Nothing has drifted.")
        print(f"registry: {REGISTRY}")

        if a.loop <= 0:
            break
        print(f"\nsleeping {a.loop:.0f}s")
        time.sleep(a.loop)


if __name__ == "__main__":
    main()
