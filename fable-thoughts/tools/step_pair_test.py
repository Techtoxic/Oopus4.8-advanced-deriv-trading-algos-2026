"""step_pair_test.py — buy the actual Rise+Fall pair on demo and see what happens.

THE CLAIM
step_tie.py measured stpRNG on 300,000 unique ticks: |step| = 1 pip 100% of the time,
zero flat ticks, and P(tie) EXACTLY 0.00000 on every odd tick duration. That is parity,
not statistics — a fixed-step process cannot return to its start in an odd number of steps.

If the payout really is 2.46 per leg at 15s (screenshot, Step 100 Index, Allow equals OFF),
then one leg always wins and:

    $2 staked -> $2.46 back -> +23% per round, risk free.

That would be the largest mispricing in retail derivatives history, so the prior is
overwhelmingly that something in the setup differs from what was measured:

  - WRONG SYMBOL. step_tie ran on stpRNG. "Step 100 Index" may be a different stpRNG*
    with a different tick rate. At 2s per tick, 15 seconds is 7.5 ticks and parity breaks.
  - DURATION SEMANTICS. The contract may not settle exactly 15 wall-clock seconds after
    entry, or may snap to tick boundaries that do not map to 15 ticks.
  - THE PAYOUT. The displayed/proposal payout has already been caught lying by up to 25%
    on JD100 (payout_audit.py). 2.46 is unverified.

Rather than argue, buy the pair and read reality back.

WHAT THIS DOES
  1. Resolves which stpRNG* symbol is which display name, and each one's tick interval.
  2. Quotes CALL and PUT at the target duration, then BUYS BOTH on demo.
  3. Waits for settlement and records: executed payout per leg, entry/exit spots, exit
     minus entry in pips, how many ticks actually elapsed, and the realised pair P&L.
  4. Repeats N times and reports the realised tie rate and mean pair return, which is the
     only number that matters.

This costs 2 x stake per round on a DEMO account. Refuses to run on real.

Run: python3 step_pair_test.py --rounds 20
     python3 step_pair_test.py --symbol stpRNG2 --duration 15 --rounds 20
"""
import argparse, json, math, time
import numpy as np
from deriv_api import DerivWS


def settle(tr, cid, timeout=90):
    t0 = time.time()
    last = {}
    while time.time() - t0 < timeout:
        c = tr.open_contract(cid).get("proposal_open_contract", {})
        if c:
            last = c
            if c.get("is_sold") or c.get("is_expired") or c.get("status") in ("won", "lost"):
                return c
        time.sleep(0.8)
    return last


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default=None,
                    help="default: probe all stpRNG* and pick the 1s one")
    ap.add_argument("--duration", type=int, default=15)
    ap.add_argument("--unit", default="s", choices=["s", "t"])
    ap.add_argument("--stake", type=float, default=1.0)
    ap.add_argument("--rounds", type=int, default=20)
    a = ap.parse_args()

    pub = DerivWS(token="")

    # ---- 1. which symbol is which, and how fast does it tick? -------------
    print("=" * 78)
    print("1. STEP SYMBOLS — display name, tick interval, step size")
    print("=" * 78)
    r = pub.call({"active_symbols": "brief"})
    syms = [s for s in r.get("active_symbols", [])
            if str(s.get("underlying_symbol", "")).startswith("stpRNG")]
    info = {}
    for s in syms:
        sym = s["underlying_symbol"]
        name = s.get("underlying_symbol_name", "")
        h = pub.call({"ticks_history": sym, "count": 400, "end": "latest",
                      "style": "ticks"})
        hh = h.get("history", {})
        ts = [int(x) for x in hh.get("times", [])]
        ps = [float(x) for x in hh.get("prices", [])]
        pip = int(h.get("pip_size", 1))
        iv = int(np.median(np.diff(ts))) if len(ts) > 2 else 0
        v = np.round(np.array(ps) * 10 ** pip).astype(np.int64)
        st = np.abs(np.diff(v))
        info[sym] = dict(name=name, iv=iv, pip=pip,
                         step=int(np.median(st)) if len(st) else 0,
                         flat=float((st == 0).mean()) if len(st) else 1.0)
        print(f"  {sym:10} {name:24} interval {iv}s  pip 1e-{pip}  "
              f"|step| {info[sym]['step']}  flat {info[sym]['flat']*100:.2f}%")
        time.sleep(0.15)

    sym = a.symbol
    if sym is None:
        one_s = [k for k, d in info.items() if d["iv"] == 1]
        sym = one_s[0] if one_s else (syms[0]["underlying_symbol"] if syms else "stpRNG")
    d = info.get(sym, {})
    print(f"\n  using {sym} ({d.get('name','?')})")
    if a.unit == "s" and d.get("iv"):
        nt = a.duration / d["iv"]
        par = "odd" if abs(nt - round(nt)) < 1e-9 and int(round(nt)) % 2 else \
              ("even" if abs(nt - round(nt)) < 1e-9 else "FRACTIONAL")
        print(f"  {a.duration}s / {d['iv']}s per tick = {nt:.2f} ticks -> {par}")
        if par == "FRACTIONAL":
            print("  *** duration does not map to a whole tick count. Parity does not apply.")

    # ---- 2. quotes --------------------------------------------------------
    print(f"\n{'='*78}")
    print("2. QUOTED PAYOUTS (proposal — has lied before, verified in step 3)")
    print("=" * 78)
    quoted = {}
    for ct in ("CALL", "PUT"):
        pr = pub.call({"proposal": 1, "amount": a.stake, "basis": "stake",
                       "contract_type": ct, "currency": "USD",
                       "underlying_symbol": sym, "duration": a.duration,
                       "duration_unit": a.unit})
        if "proposal" in pr:
            quoted[ct] = float(pr["proposal"]["payout"]) / a.stake
            print(f"  {ct:5} payout {quoted[ct]:.4f}  "
                  f"(implied P(win) {1/quoted[ct]*100:.2f}%)")
        else:
            print(f"  {ct:5} {pr.get('error', {}).get('message')}")
        time.sleep(0.15)
    if len(quoted) == 2:
        pair_q = sum(quoted.values()) / 2
        print(f"\n  if exactly one leg always wins, pair return = "
              f"{min(quoted.values()):.4f} on 2.0 staked")
        print(f"  breakeven needs a payout of 2.0000; "
              f"quoted min is {min(quoted.values()):.4f} -> "
              f"{'POSITIVE' if min(quoted.values()) > 2 else 'negative'}")

    # ---- 3. actually buy both --------------------------------------------
    tr = DerivWS()
    acct = tr.account or {}
    if acct.get("account_type") != "demo":
        print(f"\nNOT a demo account ({acct.get('account_type')}) — refusing to trade.")
        return
    print(f"\n{'='*78}")
    print(f"3. EXECUTING {a.rounds} PAIRS  [demo {acct.get('account_id')}]")
    print("=" * 78)

    rows = []
    for i in range(a.rounds):
        cids = {}
        for ct in ("CALL", "PUT"):
            b = tr.call({"buy": 1, "price": round(a.stake * 5, 2), "parameters": dict(
                amount=a.stake, basis="stake", contract_type=ct, currency="USD",
                underlying_symbol=sym, duration=a.duration, duration_unit=a.unit)})
            if "buy" in b:
                cids[ct] = (b["buy"]["contract_id"],
                            float(b["buy"]["payout"]) / float(b["buy"].get("buy_price") or a.stake))
            else:
                print(f"  [{i+1}] {ct} buy error: "
                      f"{b.get('error', {}).get('message','')[:60]}")
        if len(cids) < 2:
            time.sleep(2); continue

        res = {ct: settle(tr, cid) for ct, (cid, _) in cids.items()}
        if any(not c for c in res.values()):
            print(f"  [{i+1}] settle timeout"); continue

        pnl = sum(float(c.get("profit", 0)) for c in res.values())
        e = res["CALL"]
        ent, ex = e.get("entry_spot"), e.get("exit_spot")
        et, xt = e.get("entry_spot_time"), e.get("exit_spot_time")
        diff = (float(ex) - float(ent)) if (ent and ex) else float("nan")
        elapsed = (int(xt) - int(et)) if (et and xt) else None
        both_lost = all(c.get("status") == "lost" for c in res.values())
        rows.append(dict(i=i + 1, pnl=pnl, ent=ent, ex=ex, diff=diff,
                         elapsed=elapsed, tie=int(both_lost),
                         M_call=cids["CALL"][1], M_put=cids["PUT"][1],
                         s_call=res["CALL"].get("status"), s_put=res["PUT"].get("status")))
        print(f"  [{i+1}/{a.rounds}] CALL={res['CALL'].get('status'):5} "
              f"PUT={res['PUT'].get('status'):5} "
              f"{ent}->{ex} (d={diff:+.1f}, {elapsed}s) "
              f"payout {cids['CALL'][1]:.3f}/{cids['PUT'][1]:.3f} "
              f"pair={pnl:+.2f}" + ("   <== BOTH LOST" if both_lost else ""))
        time.sleep(1.0)

    if not rows:
        print("\nno completed rounds"); return

    # ---- 4. reality ------------------------------------------------------
    print(f"\n{'='*78}")
    print("4. WHAT ACTUALLY HAPPENED")
    print("=" * 78)
    pnls = np.array([r["pnl"] for r in rows])
    ties = sum(r["tie"] for r in rows)
    Ms = np.array([r["M_call"] for r in rows] + [r["M_put"] for r in rows])
    el = [r["elapsed"] for r in rows if r["elapsed"] is not None]
    print(f"  rounds {len(rows)}   executed payout mean {Ms.mean():.4f} "
          f"(quoted {min(quoted.values()) if quoted else float('nan'):.4f})")
    if el:
        print(f"  elapsed entry->exit: median {int(np.median(el))}s, "
              f"range {min(el)}-{max(el)}s")
    print(f"  both-lost (tie) rounds: {ties}/{len(rows)} = {ties/len(rows)*100:.1f}%")
    print(f"  mean pair P&L {pnls.mean():+.4f} on {2*a.stake:.2f} staked "
          f"= {pnls.mean()/(2*a.stake)*100:+.2f}% per round")
    print(f"  total {pnls.sum():+.2f}")
    if len(pnls) > 2:
        se = pnls.std(ddof=1) / math.sqrt(len(pnls))
        print(f"  SE {se:.4f} -> t = {pnls.mean()/se if se>0 else float('nan'):+.2f}")
    print()
    if Ms.mean() < 2.0:
        print("  Executed payout is BELOW 2.00 -> the hedge cannot profit, whatever the")
        print("  tie rate. The 2.46 in the screenshot was not this contract.")
    elif ties == 0 and pnls.mean() > 0:
        print("  No ties and positive mean. Before believing this: run 200+ rounds,")
        print("  check Deriv does not cap opposing positions, and re-verify on a")
        print("  fresh session. A risk-free +20% would not survive undiscovered.")
    else:
        print("  Ties occurred despite the parity argument -- the duration does not map")
        print("  to a whole odd tick count in practice. Check the elapsed times above.")


if __name__ == "__main__":
    main()
