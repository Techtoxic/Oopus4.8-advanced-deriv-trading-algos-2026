"""gold_cot_tilt.py: does the COT signal improve a plain long-gold position?

WHY THIS FORM
gold_cot_audit.py showed the swing system loses to holding gold at the same risk, while its COT
filter still added value over the bare system. So test the COT signal where it would actually be
used by someone who wants gold: as a TILT on a position that is always long.

PRIMARY TEST (fixed 2026-10-01, before the first run)
  signal   the same weekly COT bias the swing system used (cot.pair_bias_history('XAUUSD',
           'index'): gold commercials' 26-week index vs dollar speculators' index, +1 / 0 / -1),
           made public with the Friday release lag (cot.align_daily), applied from the NEXT close
  tilt     weight = 1 + 0.5 x bias   ->  0.5x, 1.0x or 1.5x long gold, never short, never flat
  cost     5 bp per unit of weight changed (rebalancing); financing ignored because the
           benchmark holds the same average weight and would pay the same
  benchmark buy-and-hold at the tilt's AVERAGE weight over the same window (exposure-matched)
  null     the same bias series circularly shifted by a random 1-to-(N-1)-year offset, 2,000 times:
           same signal statistics, no alignment with the prices it is meant to time
  PASS     on the out-of-sample 40% (the REPORT.md split): annual excess return over the matched
           benchmark > 0 AND the real excess at >= the 95th percentile of the shifted nulls
  KILL     otherwise

SECONDARY (reported, not judged): gold speculators' own 156-week (3-year) COT index, following
the speculators (the fable-session study found extremes confirm momentum): weight = 0.5 + index.

Also prints the passive-gold sizing note: what a vol-targeted plain long would have returned, and
what CFD swap would cost compared with an ETF-style fee.

Run:  python gold_cot_tilt.py
"""
import numpy as np
import pandas as pd

import cot
import data

SIMS = 2000
COST_BP = 5.0


def perf(r):
    r = r.fillna(0.0)
    ann = r.mean() * 252
    vol = r.std() * np.sqrt(252)
    eq = (1 + r).cumprod()
    dd = (eq / eq.cummax() - 1).min()
    return ann * 100, (ann / vol if vol > 0 else 0.0), dd * 100


def tilt_returns(ret, w):
    """Daily returns of a position holding weight w (decided at the previous close)."""
    w = w.shift(1).fillna(1.0)
    cost = w.diff().abs().fillna(0.0) * COST_BP / 1e4
    return w * ret - cost, w


def excess(ret, w):
    tr, wl = tilt_returns(ret, w)
    bench = ret * wl.mean()
    return (tr.mean() - bench.mean()) * 252, tr, bench


def shifted_nulls(ret, w, rng):
    n = len(w)
    vals = w.to_numpy()
    out = np.empty(SIMS)
    for k in range(SIMS):
        s = int(rng.integers(252, max(253, n - 252)))
        out[k] = excess(ret, pd.Series(np.roll(vals, s), index=w.index))[0]
    return out


def report(name, ret, w, rng, judge):
    ex, tr, bench = excess(ret, w)
    nulls = shifted_nulls(ret, w, rng)
    pct = float((nulls < ex).mean() * 100)
    a, s, d = perf(tr)
    ba, bs, bd = perf(bench)
    tilt_w = w.shift(1).fillna(1.0)
    print(f"\n=== {name}: {ret.index[0].date()} -> {ret.index[-1].date()} ===")
    print(f"  weights: 0.5x {float((tilt_w < 0.75).mean())*100:.0f}%  1.0x {float(((tilt_w >= 0.75) & (tilt_w <= 1.25)).mean())*100:.0f}%  "
          f"1.5x {float((tilt_w > 1.25).mean())*100:.0f}% of days  (mean {tilt_w.mean():.2f}x)")
    print(f"  COT tilt          {a:6.2f}%/yr  Sharpe {s:4.2f}  maxDD {d:6.1f}%")
    print(f"  buy-and-hold at the same average weight  {ba:6.2f}%/yr  Sharpe {bs:4.2f}  maxDD {bd:6.1f}%")
    print(f"  excess {ex*100:+.2f}%/yr; shifted-signal nulls median {np.median(nulls)*100:+.2f}% "
          f"[5th {np.percentile(nulls,5)*100:+.2f}, 95th {np.percentile(nulls,95)*100:+.2f}] -> real at the {pct:.1f}th percentile")
    return ex, pct


def passive_note(ret):
    vol = ret.std() * np.sqrt(252)
    print("\n=== PASSIVE GOLD: sizing and carrying cost (not an edge; only if you want the exposure) ===")
    print(f"  gold daily-return volatility over the sample: {vol*100:.1f}%/yr")
    for target in (0.05, 0.10):
        w = target / vol
        a, s, d = perf(ret * w)
        etf = a - w * 0.40          # ETF-style expense ratio ~0.40%/yr on the notional
        cfd = a + w * -6.0          # CFD long swap, assumed -6%/yr on the notional (read yours in MT5)
        print(f"  target vol {target*100:.0f}%: hold {w:.2f}x equity in gold -> {a:5.2f}%/yr gross, maxDD {d:6.1f}%;"
              f" after ETF fee {etf:5.2f}%/yr, after CFD swap {cfd:5.2f}%/yr")


def main():
    rng = np.random.default_rng(11)
    df = data.load_ohlc('XAUUSD')
    ret = df['close'].pct_change().fillna(0.0)
    bias = cot.align_daily(cot.pair_bias_history('XAUUSD', 'index'), df.index)
    w = 1.0 + 0.5 * bias
    cut = df.index[int(len(df) * 0.6)]

    report('PRIMARY, full period', ret, w, rng, False)
    ex, pct = report('PRIMARY, out-of-sample 40%', ret[ret.index >= cut], w[w.index >= cut], rng, True)

    spec = cot._cot_index_series('GOLD', 'noncomm_net', 156)
    w2 = 0.5 + cot.align_daily(spec, df.index).clip(0, 1)
    report('SECONDARY (reported only): gold speculators 3-year index, follow', ret[ret.index >= cut],
           w2[w2.index >= cut], rng, False)

    passive_note(ret)
    ok = ex > 0 and pct >= 95
    print(f"\nVERDICT (pre-registered, primary, out-of-sample): excess {ex*100:+.2f}%/yr, "
          f"{pct:.1f}th percentile -> {'PASS' if ok else 'KILL'}")


if __name__ == '__main__':
    main()
