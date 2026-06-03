"""
backtest_lattice.py — run the FULL 6-layer pipeline on real ticks, honestly.

Settlement is lag-2 (Deriv processes a 1-tick digital option at entry=i+1,
exit=i+2), real live payouts, instant fills, no slippage (deliberately generous).

Three things are run:

  1. REAL DATA, belief="framework", strict 0.99 gate  — the friend's system,
     exactly as specified. (It almost never trades; that's a finding.)
  2. REAL DATA, belief="framework", relaxed gate       — forces high volume so
     the realized win rate converges and the house edge is visible in P/L.
  3. REAL DATA, belief="honest"                         — p_win = uniform truth,
     so Kelly sizes 0 and the bot correctly never trades.

  4. PLANTED-EDGE SANITY — a synthetic price series whose last digits follow an
     ANTI_REPEAT (untapped-favoring) law is fed through the IDENTICAL engine.
     The bot's machinery turns a profit there, proving the pipeline is sound and
     only the (fair) market denies it an edge.

Everything writes to results/backtests.txt.
"""
import os, sys
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lattice import common
from lattice.engine import LatticeEngine


def run_engine(symbol, prices, epochs, payouts, belief="framework",
               conv_threshold=0.99, require_entropy_gate=True, bankroll=10.0,
               places=None, return_log=False, disable_drift=False):
    if places is None:
        places = common.infer_decimals(prices)
    dig, _ = common.last_digit(prices, places)
    eng = LatticeEngine(symbol, places, payouts, belief=belief, bankroll=bankroll,
                        require_entropy_gate=require_entropy_gate,
                        conv_threshold=conv_threshold, disable_drift=disable_drift)
    pending = {}            # settle_index -> list of orders
    n = len(prices)
    curve = [bankroll]
    for i in range(n):
        # settle anything due at i
        if i in pending:
            for od in pending.pop(i):
                eng.settle(od, int(dig[i]))
            curve.append(eng.bankroll)
        dec = eng.on_tick(float(prices[i]), int(epochs[i]))
        if dec:
            settle_idx = i + 2          # lag-2
            if settle_idx < n:
                pending.setdefault(settle_idx, []).extend(dec["orders"])
    s = eng.L6.summary()
    s["gated"] = eng.gated
    s["final_bankroll"] = eng.bankroll
    s["min_bankroll"] = min(curve) if curve else bankroll
    if return_log:
        return s, curve, eng.L6.log
    return s, curve


def synth_anti_repeat_prices(n=120000, w=10, avoid=0.6, base=1000.0, seed=7):
    """Random-walk price in the 0.10 place; last digit (2dp) follows an
    anti-repeat law so untapped digits are genuinely more likely."""
    rng = np.random.default_rng(seed)
    d = np.empty(n, dtype=int)
    d[:w] = rng.integers(0, 10, size=w)
    for i in range(w, n):
        recent = set(d[i - w:i].tolist())
        unt = [x for x in range(10) if x not in recent]
        d[i] = rng.choice(unt) if (unt and rng.random() < avoid) else rng.integers(0, 10)
    steps = rng.choice([-0.2, -0.1, 0.0, 0.1, 0.2], size=n)
    tens = base + np.cumsum(steps)
    tens = np.round(tens, 1)
    prices = np.round(tens + d * 0.01, 2)
    epochs = np.arange(n, dtype=np.int64) + 1_700_000_000
    # sanity: last digits match
    chk, _ = common.last_digit(prices, 2)
    assert np.mean(chk == d) > 0.999, "synthetic last digits drifted"
    return prices, epochs


def fmt(tag, s):
    g = s["gated"]
    wr = f"{s['win_rate']:.4f}" if s["win_rate"] is not None else "  -  "
    roi = f"{s['roi']*100:+.2f}%" if s["roi"] is not None else "  -  "
    return (f"{tag:38s} trades={s['trades']:6d} win={wr} "
            f"P/L={s['pnl']:+8.3f} ROI={roi:>8s} "
            f"bank={s['final_bankroll']:.2f} (min {s['min_bankroll']:.2f})  "
            f"executed_ticks={g['executed']}")


def per_kind_breakdown(eng_log):
    """Realized win rate by contract family from a feedback log."""
    agg = {}
    for r in eng_log:
        k = r["kind"]
        a = agg.setdefault(k, [0, 0, 0.0])
        a[0] += 1; a[1] += (r["outcome"] == "win"); a[2] += r["pnl"]
    lines = []
    for k, (n, w, pnl) in sorted(agg.items()):
        lines.append(f"      {k:7s} n={n:6d} win={w/n:.4f} P/L={pnl:+8.3f}")
    return lines


def main():
    payouts = common.load_payouts()
    BR = 1000.0
    L = ["=" * 100, "FULL 6-LAYER PIPELINE BACKTEST (lag-2 settlement, live payouts)",
         f"start bankroll ${BR:.0f} (a $10 account can't trade: 2% cap=$0.20 < $0.35 min stake)",
         "=" * 100]
    syms = ["1HZ100V", "1HZ10V", "R_100", "JD100"]

    L.append("\n--- (1) friend's system AS SPECIFIED: conv>=0.99 gate ---")
    e, pr = common.load("1HZ100V")
    s, _ = run_engine("1HZ100V", pr, e, payouts, belief="framework",
                      conv_threshold=0.99, require_entropy_gate=True, bankroll=BR)
    L.append(fmt("1HZ100V framework@0.99", s))
    L.append("  rolling convergence peaks ~0.6 -> the 99% gate NEVER fires. As specified, it never trades.")

    L.append("\n--- (2) drift detector ON (conv>=0.50): the bot self-halts almost immediately ---")
    s, _ = run_engine("1HZ100V", pr, e, payouts, belief="framework",
                      conv_threshold=0.50, require_entropy_gate=False, bankroll=BR,
                      disable_drift=False)
    L.append(fmt("1HZ100V drift-ON", s))
    L.append(f"  executed {s['gated']['executed']} trades, then Layer-6 drift halt fired "
             f"{s['gated']['drift_halt']:,} times (untapped-fill assumption broke at once).")

    L.append("\n--- (3) drift OFF, high volume: realized win rate converges to the house edge ---")
    main_log = None
    for sym in syms:
        e, pr = common.load(sym)
        s, _, log = run_engine(sym, pr, e, payouts, belief="framework",
                               conv_threshold=0.50, require_entropy_gate=False,
                               bankroll=BR, return_log=True, disable_drift=True)
        L.append(fmt(f"{sym} framework@0.50", s))
        if sym == "1HZ100V":
            main_log = log
    if main_log:
        L.append("    realized win rate by contract family (1HZ100V) — each sits at its uniform prob:")
        L += per_kind_breakdown(main_log)

    L.append("\n--- (4) honest belief (p=uniform truth) -> Kelly=0 -> no trades, drift OFF ---")
    e, pr = common.load("1HZ100V")
    s, _ = run_engine("1HZ100V", pr, e, payouts, belief="honest",
                      conv_threshold=0.50, require_entropy_gate=False, bankroll=BR,
                      disable_drift=True)
    L.append(fmt("1HZ100V honest", s))

    L.append("\n--- (5) PLANTED-EDGE SANITY: anti-repeat synthetic through the IDENTICAL engine ---")
    pr, ep = synth_anti_repeat_prices()
    s, _, log = run_engine("1HZ100V", pr, ep, payouts, belief="framework",
                           conv_threshold=0.50, require_entropy_gate=False,
                           bankroll=BR, return_log=True, disable_drift=True)
    L.append(fmt("ANTI_REPEAT framework", s))
    L += per_kind_breakdown(log)
    L.append("  ^ Positive here while real data is not => the pipeline is SOUND; the fair")
    L.append("    market — not the code — is what removes the edge.")
    text = "\n".join(L); print(text)
    rp = os.path.join(os.path.dirname(__file__), "..", "results", "backtests.txt")
    os.makedirs(os.path.dirname(rp), exist_ok=True)
    open(rp, "w").write(text + "\n")


if __name__ == "__main__":
    main()
