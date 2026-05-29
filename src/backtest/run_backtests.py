"""
Run every popular strategy x stake-sizing combo on real tick data with real
payouts, and print an honest results table. Output -> results/backtests.txt
"""
import json, os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import common
from engine import backtest
import strategies as S

DATA = os.path.join(os.path.dirname(__file__), "..", "..", "data")
RESULTS = os.path.join(os.path.dirname(__file__), "..", "..", "results")

def load_payouts():
    path = os.path.join(DATA, "payouts.json")
    if not os.path.exists(path):
        return {}
    rows = json.load(open(path))
    out = {}
    for r in rows:
        if "payout" not in r:
            continue
        sym = r["symbol"]
        bar = r.get("params", {}).get("barrier")
        bar = int(bar) if bar is not None else None
        out.setdefault(sym, {})[(r["contract"], bar)] = r["payout"]
    return out

def run_symbol(sym, payouts_all, out, bankroll=10.0, base=0.35, has_digits=True):
    e, p = common.load(sym)
    dec = common.infer_decimals(p)
    d = common.last_digit(p, dec)
    pay = payouts_all.get(sym, {})
    out.append(f"\n================ {sym}  (n={len(p)} ticks, start ${bankroll:.2f}, base ${base:.2f}) ================")
    digit_combos = [
        ("Differs / flat",            S.always_differs,                S.flat(base)),
        ("Differs / MARTINGALE x2",   S.always_differs,                S.martingale(base)),
        ("Even / flat",               S.always_even,                   S.flat(base)),
        ("Even / MARTINGALE x2",      S.always_even,                   S.martingale(base)),
        ("Even / anti-martingale",    S.always_even,                   S.anti_martingale(base)),
        ("Over0 (90%) / flat",        S.always_over0,                  S.flat(base)),
        ("Over0 (90%) / MARTINGALE",  S.always_over0,                  S.martingale(base)),
        ("Gambler low->Over3 / flat", S.gambler_low_then_over,         S.flat(base)),
        ("Gambler match->Diff /flat", S.gambler_after_match_differs,   S.flat(base)),
    ]
    price_combos = [
        ("Momentum rise(3) / flat",   S.momentum_rise(3),              S.flat(base)),
        ("MeanRev rise(3) / flat",    S.meanrev_rise(3),               S.flat(base)),
        ("Momentum rise(3) / MART",   S.momentum_rise(3),              S.martingale(base)),
    ]
    combos = (digit_combos if has_digits else []) + price_combos
    if not has_digits:
        out.append("  [digit contracts NOT offered by Deriv on this symbol -> only Rise/Fall tested]")
    for name, strat, stake in combos:
        res = backtest(name, p, d, strat, stake, bankroll=bankroll,
                       min_stake=base, payouts=pay, stop_at_ruin=True)
        out.append("  " + res.line())

def main():
    payouts_all = load_payouts()
    out = []
    out.append("=" * 120)
    out.append("HONEST BACKTESTS — popular Deriv strategies on REAL ticks with REAL payouts")
    out.append("Engine is generous: no spread/slippage/commission beyond the quoted payout.")
    out.append("=" * 120)
    for sym in ["1HZ100V", "R_100", "1HZ10V", "JD100"]:
        run_symbol(sym, payouts_all, out, has_digits=True)
    run_symbol("stpRNG", payouts_all, out, has_digits=False)
    text = "\n".join(out)
    print(text)
    os.makedirs(RESULTS, exist_ok=True)
    with open(os.path.join(RESULTS, "backtests.txt"), "w") as f:
        f.write(text + "\n")

if __name__ == "__main__":
    main()
