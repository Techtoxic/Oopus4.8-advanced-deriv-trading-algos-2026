"""tie_probe.py — decide whether the CALLE/PUTE tie edge is live RIGHT NOW.

Why this exists: tie_hypothesis.py computes EV from *proposal* payouts, but FINDINGS.md §3
established the proposal endpoint LIES for CALLE/PUTE (quotes the no-tie 1.953 grid; executions
fill at ~1.835). That 6pp error is larger than the entire edge. This script measures the fill
from SETTLED DEMO CONTRACTS and nothing else.

Physics: on a pip lattice, P(next tick == this tick exactly) ~ 1/(sigma_pips * sqrt(2*pi)).
CALLE ("higher or equal") and PUTE ("lower or equal") collect that tie mass. sigma_pips falls
as JD100 spot decays, so P(tie) RISES over time while the payout stays static -> the same
"fixed parameter meets drifting state" engine that produced the digit edge.

Pair trade (CALLE + PUTE together): one leg always pays, BOTH pay on a tie.
    no tie -> M - 2      tie -> 2M - 2      EV = M*(1+P_tie) - 2
PUTE solo: wins on down OR tie, and a drag-decaying index has P(down) > P(up) structurally.
    EV = M * (P_down + P_tie) - 1

Run (measure only, no trading):   python3 tie_probe.py --no-trade
Run (full, demo account):         python3 tie_probe.py --rounds 40
"""
import argparse, csv, math, os, time
import numpy as np
from deriv_api import DerivWS

W = 1800
JUMP_THR = 20


def sigma_pips(vals):
    """Same jump-filtered rolling estimator sentinel_v2 / the offset tables use."""
    st = np.diff(vals[-(W + 1):])
    nj = st[np.abs(st) <= JUMP_THR]
    if len(nj) < 200:
        return None
    return float(np.sqrt((nj.astype(float) ** 2).mean()))


def measure(ws, sym, n):
    times, prices, pip = ws.history_paged(sym, n, sleep=0.2)
    pip = int(pip)
    vals = np.round(np.array(prices) * (10 ** pip)).astype(np.int64)
    d = np.diff(vals)
    p_up = float((d > 0).mean())
    p_tie = float((d == 0).mean())
    p_dn = float((d < 0).mean())
    return dict(spot=float(prices[-1]), pip=pip, n=len(vals),
                sigma=sigma_pips(vals), p_up=p_up, p_tie=p_tie, p_dn=p_dn)


def run_pairs(tr, sym, rounds, stake, logpath):
    """Execute CALLE+PUTE pairs; recover the TRUE payout multiple from settled profit."""
    fills, rows = [], []
    ties = 0
    total = 0.0
    same_tick = 0
    for i in range(rounds):
        cids = []
        for ct in ("CALLE", "PUTE"):
            br = tr.call({"buy": 1, "price": round(stake * 1.05, 2), "parameters":
                          dict(amount=stake, basis="stake", contract_type=ct,
                               currency="USD", duration=1, duration_unit="t",
                               underlying_symbol=sym)})
            if "buy" not in br:
                print("  buy error:", br.get("error", {}).get("message"))
                break
            cids.append((ct, br["buy"]["contract_id"]))
        if len(cids) < 2:
            time.sleep(2); continue

        time.sleep(2.5)
        rec = {}
        for ct, cid in cids:
            for _ in range(12):
                c = tr.open_contract(cid).get("proposal_open_contract", {})
                if c.get("is_sold") or c.get("status") in ("won", "lost"):
                    rec[ct] = c
                    break
                time.sleep(0.8)
        if len(rec) < 2:
            print("  settle timeout"); continue

        rp = sum(float(rec[c].get("profit", 0)) for c in rec)
        total += rp
        tie = all(rec[c].get("status") == "won" for c in rec)
        ties += int(tie)

        # TRUE multiple from a winning leg: profit = stake*(M-1)
        for ct, c in rec.items():
            if c.get("status") == "won":
                fills.append(1.0 + float(c["profit"]) / stake)

        e = rec["CALLE"]
        et, xt = e.get("entry_spot_time"), e.get("exit_spot_time")
        if et is not None and et == xt:
            same_tick += 1

        rows.append(dict(round=i + 1, calle=rec["CALLE"].get("status"),
                         pute=rec["PUTE"].get("status"),
                         entry=e.get("entry_spot"), exit=e.get("exit_spot"),
                         entry_t=et, exit_t=xt, pnl=round(rp, 4), tie=int(tie)))
        print(f"  [{i+1}/{rounds}] CALLE={rec['CALLE'].get('status'):5} "
              f"PUTE={rec['PUTE'].get('status'):5} "
              f"{e.get('entry_spot')}->{e.get('exit_spot')} "
              f"round={rp:+.2f} cum={total:+.2f}" + ("   <== TIE" if tie else ""))

    if rows:
        new = not os.path.exists(logpath)
        with open(logpath, "a", newline="") as f:
            wtr = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            if new: wtr.writeheader()
            wtr.writerows(rows)
    return fills, ties, total, len(rows), same_tick


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="JD100")
    ap.add_argument("--ticks", type=int, default=20000)
    ap.add_argument("--rounds", type=int, default=40)
    ap.add_argument("--stake", type=float, default=1.0)
    ap.add_argument("--no-trade", action="store_true")
    ap.add_argument("--log", default="../results/tie_probe.csv")
    a = ap.parse_args()

    pub = DerivWS(token="")
    print(f"=== 1. PHYSICS — {a.symbol}, {a.ticks} fresh ticks ===")
    m = measure(pub, a.symbol, a.ticks)
    approx = 1 / (m["sigma"] * math.sqrt(2 * math.pi)) if m["sigma"] else float("nan")
    print(f"  spot        {m['spot']:.4f}   pip 1e-{m['pip']}   n={m['n']}")
    print(f"  sigma_pips  {m['sigma']:.3f}")
    print(f"  P(up)       {m['p_up']:.4f}")
    print(f"  P(tie)      {m['p_tie']:.4f}   (lattice approx {approx:.4f})")
    print(f"  P(down)     {m['p_dn']:.4f}")
    print(f"  P(not up)   {m['p_dn'] + m['p_tie']:.4f}   <- what PUTE wins on")

    print(f"\n=== 2. PROPOSAL QUOTES (reference only — these LIE for CALLE/PUTE) ===")
    quotes = {}
    for ct in ("CALL", "CALLE", "PUT", "PUTE"):
        r = pub.proposal(amount=10, basis="stake", contract_type=ct, currency="USD",
                         duration=1, duration_unit="t", underlying_symbol=a.symbol)
        quotes[ct] = float(r["proposal"]["payout"]) / 10 if "proposal" in r else None
        time.sleep(0.10)
        q = quotes[ct]
        print(f"  {ct:6} {'--' if q is None else f'{q:.4f}'}")

    if a.no_trade:
        print("\n--no-trade set. Re-run without it to measure the executed fill.")
        return

    print(f"\n=== 3. EXECUTED FILL — {a.rounds} demo CALLE+PUTE pairs ===")
    tr = DerivWS()
    acct = tr.account or {}
    if acct.get("account_type") != "demo":
        print(f"  NOT a demo account ({acct.get('account_type')}) — refusing."); return
    print(f"  acct {acct.get('account_id')} bal={acct.get('balance')}")

    fills, ties, total, n, same_tick = run_pairs(tr, a.symbol, a.rounds, a.stake, a.log)
    if not fills:
        print("  no settled legs — cannot determine fill."); return

    M = sum(fills) / len(fills)
    print(f"\n  TRUE executed multiple M = {M:.4f}   (proposal claimed "
          f"{quotes.get('CALLE')})   n_legs={len(fills)}")
    if same_tick:
        print(f"  WARNING: {same_tick}/{n} rounds had entry_spot_time == exit_spot_time")

    print(f"\n=== 4. DECISION (measured M, measured P_tie) ===")
    pt, pdn = m["p_tie"], m["p_dn"]
    ev_pair = M * (1 + pt) - 2
    ev_pute = M * (pdn + pt) - 1
    ev_calle = M * (m["p_up"] + pt) - 1
    print(f"  pair  EV = M*(1+P_tie)-2      = {ev_pair:+.4f} on 2 staked = {ev_pair/2*100:+.2f}%/round")
    print(f"  PUTE  EV = M*(P_dn+P_tie)-1   = {ev_pute*100:+.2f}%/trade")
    print(f"  CALLE EV = M*(P_up+P_tie)-1   = {ev_calle*100:+.2f}%/trade")
    print(f"  pair breakeven needs P_tie > {2/M - 1:.4f}  (measured {pt:.4f})")

    var = (1 - pt) * (M - 2 - ev_pair) ** 2 + pt * (2 * M - 2 - ev_pair) ** 2
    sd = math.sqrt(var)
    print(f"\n  pair sd/round {sd:.4f}")
    if ev_pair > 0:
        for t in (2, 3):
            need = (t * sd / ev_pair) ** 2
            print(f"    rounds for t={t}: {need:,.0f}  (~{need*3/3600:.1f}h at 3s/round)")
        print("\n  VERDICT: positive on measured numbers -> run the full validation sample.")
    else:
        print("\n  VERDICT: NOT positive at current M and P_tie. Do not deploy.")
        print("  Re-probe when spot is lower (P_tie rises as sigma falls).")


if __name__ == "__main__":
    main()
