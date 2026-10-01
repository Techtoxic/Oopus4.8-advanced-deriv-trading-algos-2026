"""mult_stopout.py — H10: does the multiplier stop-out truncate BOOM/CRASH spike risk?

THE IDEA
BOOM drifts down slowly and spikes up ~1 tick in N; CRASH is the mirror. A multiplier
position carries a STOP-OUT: your loss is capped at the stake. Take a short on BOOM —
drift is favourable, so in principle only a spike stops you out. If the spike moves s
against you and s > 1/M, you lose exactly the stake K, not M*K*s. The tail is truncated
while the drift gain is not.

Naive per-cycle algebra gives K*(M*s - 1) > 0 whenever M*s > 1, and a breakeven commission
of 0.25-1.75% of notional against a typical Deriv commission of 0.01-0.06%. A 10-50x margin
is not a discovery, it is a missing term.

THE MISSING TERM
That algebra assumes smooth drift. Real BOOM between spikes is a noisy random walk with
negative drift, so a short can be stopped out by ordinary upward noise long before any spike
arrives. Every such stop-out costs a full K and collects almost no drift. Whether the strategy
is +EV is entirely an empirical question about noise-driven stop-out frequency.

So: no formula. Backtest the position on real ticks with the real proposal parameters.

WHAT THIS DOES (read-only, places no trades)
  1. Pulls a MULTUP/MULTDOWN proposal per symbol -> available multipliers, commission,
     stop_out level, limit orders. Dumps the schema on the first call so we learn the
     real field names rather than guessing.
  2. Pulls ticks.
  3. Simulates the favourable-direction position (short BOOM / long CRASH) with correct
     mechanics: PnL = K*M*(dir)*(p_t - p_entry)/p_entry, stop out when PnL <= -K, pay
     commission at entry, re-enter on the next tick.
  4. Reports realised EV per trade and per tick, stop-out rate, and a spike/noise split of
     the stop-out causes -- which is the number that decides the hypothesis.

Also tests the WRONG direction as a control. If both directions look profitable, the
simulation is broken, not the market.

Run: python3 mult_stopout.py
     python3 mult_stopout.py --symbols BOOM1000 CRASH1000 --ticks 50000
"""
import argparse, json, time
import numpy as np
from deriv_api import DerivWS

BOOM = ["BOOM300N", "BOOM500", "BOOM600", "BOOM900", "BOOM1000"]
CRASH = ["CRASH300N", "CRASH500", "CRASH600", "CRASH900", "CRASH1000"]

_schema_dumped = False


def get_mult_params(ws, sym, ctype, mult, stake):
    """Proposal for a multiplier contract. Returns (ok, dict)."""
    global _schema_dumped
    r = ws.call({"proposal": 1, "amount": stake, "basis": "stake",
                 "contract_type": ctype, "currency": "USD",
                 "underlying_symbol": sym, "multiplier": mult})
    if "proposal" not in r:
        return False, {"error": r.get("error", {}).get("message")}
    p = r["proposal"]
    if not _schema_dumped:
        print("\n  --- first multiplier proposal schema ---")
        for k, v in sorted(p.items()):
            s = json.dumps(v)[:110] if isinstance(v, (dict, list)) else v
            print(f"    {k:26} {s}")
        print("  --- end schema ---\n")
        _schema_dumped = True
    lo = p.get("limit_order", {}) or {}
    so = lo.get("stop_out", {}) or {}
    return True, {
        "commission": float(p.get("commission") or 0),
        "ask": float(p.get("ask_price") or stake),
        "spot": float(p.get("spot") or 0),
        "stop_out_value": so.get("value"),
        "stop_out_amount": so.get("order_amount"),
    }


def find_multipliers(ws, sym, ctype, stake):
    """Probe which multipliers are accepted."""
    ok_mults = []
    for m in (10, 20, 30, 40, 50, 100, 200, 300, 400, 500, 1000):
        ok, d = get_mult_params(ws, sym, ctype, m, stake)
        if ok:
            ok_mults.append((m, d))
        time.sleep(0.08)
    return ok_mults


def simulate(prices, spike_mask, direction, M, K, comm_frac, max_hold):
    """
    direction: -1 short (favourable on BOOM), +1 long (favourable on CRASH)
    Enter every tick a position is free. Stop out when PnL <= -K. Exit at max_hold.
    Returns per-trade records.
    """
    n = len(prices)
    trades = []
    i = 0
    while i < n - 1:
        entry = prices[i]
        comm = comm_frac * K * M
        pnl = -comm
        stopped = False
        by_spike = False
        j = i + 1
        while j < n and (j - i) <= max_hold:
            move = direction * (prices[j] - entry) / entry
            pnl = K * M * move - comm
            if pnl <= -K:
                stopped = True
                by_spike = bool(spike_mask[j - 1])
                pnl = -K
                break
            j += 1
        trades.append(dict(entry_i=i, exit_i=j, ticks=j - i, pnl=pnl,
                           stopped=int(stopped), by_spike=int(by_spike)))
        i = j + 1
    return trades


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+", default=BOOM + CRASH)
    ap.add_argument("--ticks", type=int, default=30000)
    ap.add_argument("--stake", type=float, default=10.0)
    ap.add_argument("--max-hold", type=int, default=2000)
    ap.add_argument("--out", default="../results/mult_stopout.md")
    a = ap.parse_args()

    ws = DerivWS(token="")
    md = ["# H10 — multiplier stop-out truncation on BOOM/CRASH\n\n",
          "Favourable direction = short BOOM / long CRASH. Control = opposite direction.\n",
          "If BOTH directions profit, the simulation is wrong.\n\n",
          "| symbol | M | comm | trades | stop% | spike-caused | EV/trade | EV/tick | control EV/trade |\n",
          "|---|---:|---:|---:|---:|---:|---:|---:|---:|\n"]

    print(f"{'symbol':11}{'M':>6}{'comm%':>8}{'trades':>8}{'stop%':>7}"
          f"{'spike%':>8}{'EV/trade':>10}{'EV/tick':>10}{'ctrl':>9}")
    print("-" * 78)

    best = []
    for sym in a.symbols:
        is_boom = sym.startswith("BOOM")
        fav_ct = "MULTDOWN" if is_boom else "MULTUP"
        ctl_ct = "MULTUP" if is_boom else "MULTDOWN"
        fav_dir = -1 if is_boom else +1

        try:
            times, prices, pip = ws.history_paged(sym, a.ticks, sleep=0.2)
        except Exception as e:
            print(f"{sym:11} tick fetch error: {e}")
            continue
        prices = np.asarray(prices, dtype=float)
        if len(prices) < 5000:
            print(f"{sym:11} only {len(prices)} ticks")
            continue

        rel = np.abs(np.diff(prices) / prices[:-1])
        med = float(np.median(rel))
        mad = float(np.median(np.abs(rel - med))) or 1e-12
        spike_mask = rel > (med + 5 * 1.4826 * mad)
        n_sp = int(spike_mask.sum())
        spike_s = float(np.median(rel[spike_mask])) if n_sp else 0.0

        mults = find_multipliers(ws, sym, fav_ct, a.stake)
        if not mults:
            print(f"{sym:11} no multipliers accepted")
            continue

        for M, d in mults:
            comm_frac = d["commission"] / (a.stake * M) if d["commission"] else 0.0
            tr = simulate(prices, spike_mask, fav_dir, M, a.stake, comm_frac, a.max_hold)
            ct = simulate(prices, spike_mask, -fav_dir, M, a.stake, comm_frac, a.max_hold)
            if not tr:
                continue
            pnls = np.array([t["pnl"] for t in tr])
            tks = np.array([t["ticks"] for t in tr])
            stops = np.array([t["stopped"] for t in tr])
            spk = np.array([t["by_spike"] for t in tr])
            ev = float(pnls.mean())
            ev_tick = float(pnls.sum() / tks.sum())
            stop_rate = float(stops.mean())
            spike_share = float(spk.sum() / max(stops.sum(), 1))
            ctl_ev = float(np.mean([t["pnl"] for t in ct]))

            best.append((ev / a.stake, sym, M, ev, ctl_ev, stop_rate, spike_share))
            print(f"{sym:11}{M:>6}{comm_frac*100:>8.4f}{len(tr):>8}"
                  f"{stop_rate*100:>7.1f}{spike_share*100:>8.1f}"
                  f"{ev:>10.3f}{ev_tick:>10.5f}{ctl_ev:>9.3f}")
            md.append(f"| {sym} | {M} | {comm_frac*100:.4f}% | {len(tr)} | {stop_rate*100:.1f}% "
                      f"| {spike_share*100:.1f}% | {ev:+.3f} | {ev_tick:+.5f} | {ctl_ev:+.3f} |\n")

        md.append(f"\n<!-- {sym}: spike period 1/{len(rel)/max(n_sp,1):.0f}, "
                  f"median spike {spike_s:.5f} -->\n")
        time.sleep(0.2)

    if not best:
        print("\nno cells"); ws.close(); return

    best.sort(reverse=True)
    r = best[0]
    print(f"\n{'='*78}")
    print(f"BEST: {r[1]} M={r[2]}  EV/trade {r[3]:+.3f} on ${a.stake} "
          f"= {r[0]*100:+.2f}%  (control {r[4]:+.3f})")
    print(f"  stop-out rate {r[5]*100:.1f}%, of which {r[6]*100:.1f}% spike-caused")

    md.append(f"\n## Best\n\n`{r[1]}` M={r[2]}: EV/trade {r[3]:+.3f} on ${a.stake} "
              f"({r[0]*100:+.2f}%), control {r[4]:+.3f}. Stop-out {r[5]*100:.1f}%, "
              f"{r[6]*100:.1f}% spike-caused.\n\n")

    if r[3] > 0 and r[4] > 0:
        print("\n  BOTH directions positive -> SIMULATION IS WRONG. Do not trade.")
        md.append("**Both directions positive — simulation is wrong.** Likely commission or "
                  "stop-out mechanics misread. Fix before drawing any conclusion.\n")
    elif r[3] > 0:
        print(f"\n  Favourable direction +EV, control {r[4]:+.3f} negative. Structure holds")
        print("  on this sample. NEXT: verify commission field against a real demo fill,")
        print("  then paper-trade. Backtest EV is not execution EV.")
        md.append("Favourable direction +EV with control negative — structure holds on this "
                  "sample. Verify the commission field against a real demo fill before "
                  "believing it; a misread commission is the most likely error.\n")
    else:
        print("\n  Favourable direction is -EV. Noise-driven stop-outs dominate the")
        print("  truncation benefit. Hypothesis dead.")
        md.append("Favourable direction -EV: noise-driven stop-outs dominate the truncation "
                  "benefit. Hypothesis dead.\n")

    open(a.out, "w").write("".join(md))
    print(f"\nwrote {a.out}")
    ws.close()


if __name__ == "__main__":
    main()
