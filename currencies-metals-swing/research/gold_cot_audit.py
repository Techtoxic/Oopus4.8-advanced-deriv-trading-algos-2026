"""gold_cot_audit.py: is the gold + COT-index swing edge worth a forward demo?

REPORT.md says gold with the COT-index filter made +16.9% out-of-sample (PF 1.51, Sharpe 0.52).
It never asked three questions, and this file asks them before any demo time is spent:

  1. SWAP. Trades are held up to 40 bars, and the backtester charges spread, slippage and
     commission but no overnight financing. Gold swaps on retail MT5 are large. Read yours in
     MT5 (Symbols > XAUUSD > Specification: swap long / swap short) and pass them in.
  2. BENCHMARK. Gold went from ~$400 to ~$2,600+ over the sample. A long-biased trend system
     on gold can look good simply by being long. Compare with buy-and-hold at the same risk.
  3. TIMING. Keep every trade's direction, holding time and position size, but move its entry
     to a random date (2,000 times). If the real entries do not beat that, the "signal" is
     just exposure to gold's trend, which buy-and-hold gives for free.

A forward demo cannot settle this: at ~1-2%/year, four weeks of trades is noise. The history
has to show the edge first; the demo then checks only that the EA reproduces the backtest.

PRE-REGISTERED (written 2026-10-01, before this file was run)
  WORTH A DEMO only if, on the out-of-sample 40% (the same split as REPORT.md):
    (a) net return stays > 0 after swap at the rates given, AND
    (b) the real entries beat random timing at >= the 95th percentile, AND
    (c) the COT filter beats the same system without it (the filter is the claimed edge).
  Otherwise: NOT WORTH A DEMO. Report Sharpe vs buy-and-hold either way.

Run:
  python gold_cot_audit.py                                   # default swap -6%/yr long, -2%/yr short
  python gold_cot_audit.py --swap-long -0.08 --swap-short -0.01
"""
import argparse

import numpy as np
import pandas as pd

import data
from run_research import run_one

SIMS = 2000


def ann_stats(eq):
    r = eq.pct_change().fillna(0.0)
    yrs = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1
    sh = r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else 0.0
    dd = (eq / eq.cummax() - 1).min()
    return cagr * 100, sh, dd * 100, r.std() * np.sqrt(252) * 100


def swap_cost(tdf, long_pa, short_pa):
    """Financing in account currency: notional x annual rate x calendar days held / 365."""
    days = (pd.to_datetime(tdf.exit_date) - pd.to_datetime(tdf.entry_date)).dt.days.clip(lower=0)
    notional = tdf.units * tdf.entry
    rate = np.where(tdf.direction > 0, long_pa, short_pa)
    return notional * rate * days / 365.0          # negative = you pay


def random_timing(df, tdf, equity0, rng):
    """Gross return of the real trades vs the same trades placed at random dates."""
    c = df['close'].to_numpy()
    idx = df.index
    pos = {d: i for i, d in enumerate(idx)}
    ent = np.array([pos.get(pd.Timestamp(d), -1) for d in tdf.entry_date])
    ext = np.array([pos.get(pd.Timestamp(d), -1) for d in tdf.exit_date])
    ok = (ent >= 0) & (ext > ent)
    t = tdf[ok]
    dur = ext[ok] - ent[ok]
    d = t.direction.to_numpy()
    eq_before = (t.equity_after - t.pnl).to_numpy()
    f = (t.units * t.entry).to_numpy() / eq_before          # notional as a fraction of equity
    real = float(np.sum(f * d * (t.exit.to_numpy() / t.entry.to_numpy() - 1)))
    n = len(c)
    sims = np.empty(SIMS)
    for k in range(SIMS):
        s = rng.integers(0, n - dur - 1)
        sims[k] = np.sum(f * d * (c[s + dur] / c[s] - 1))
    return real, sims, int(ok.sum())


def section(name, df, r_cot, r_base, a, rng):
    eq = r_cot['equity']
    tdf = r_cot['trades']
    cagr, sh, dd, vol = ann_stats(eq)
    bh = df['close'] / df['close'].iloc[0] * eq.iloc[0]
    bcagr, bsh, bdd, bvol = ann_stats(bh)
    # buy-and-hold scaled to the strategy's volatility
    scale = vol / bvol if bvol > 0 else 0.0
    br = df['close'].pct_change().fillna(0.0) * scale
    beq = (1 + br).cumprod() * eq.iloc[0]
    mcagr, msh, mdd, _ = ann_stats(beq)
    sw = swap_cost(tdf, a.swap_long, a.swap_short)
    net = eq.iloc[-1] - eq.iloc[0]
    net_sw = net + sw.sum()
    days_in = (pd.to_datetime(tdf.exit_date) - pd.to_datetime(tdf.entry_date)).dt.days.sum()
    span = (df.index[-1] - df.index[0]).days
    real, sims, nt = random_timing(df, tdf, eq.iloc[0], rng)
    pct = float((sims < real).mean() * 100)
    base_net = r_base['equity'].iloc[-1] - r_base['equity'].iloc[0]
    print(f"\n=== {name}: {df.index[0].date()} -> {df.index[-1].date()} ===")
    print(f"  strategy (COT on)     trades {len(tdf)}  long {int((tdf.direction>0).sum())} short {int((tdf.direction<0).sum())}  "
          f"in market {days_in/span*100:.0f}% of days")
    print(f"                        CAGR {cagr:5.2f}%  Sharpe {sh:4.2f}  maxDD {dd:6.1f}%  vol {vol:4.1f}%")
    print(f"  buy-and-hold gold     CAGR {bcagr:5.2f}%  Sharpe {bsh:4.2f}  maxDD {bdd:6.1f}%  vol {bvol:4.1f}%")
    print(f"  buy-and-hold at the strategy's volatility: CAGR {mcagr:5.2f}%  maxDD {mdd:6.1f}%")
    print(f"  net P&L {net:+,.0f}  swap {sw.sum():+,.0f} (long {a.swap_long*100:+.1f}%/yr, short {a.swap_short*100:+.1f}%/yr)  "
          f"net after swap {net_sw:+,.0f}")
    print(f"  same system WITHOUT COT: net {base_net:+,.0f}  -> COT filter adds {net - base_net:+,.0f}")
    print(f"  timing: real entries {real*100:+.1f}% gross vs random dates median {np.median(sims)*100:+.1f}% "
          f"[5th {np.percentile(sims,5)*100:+.1f}, 95th {np.percentile(sims,95)*100:+.1f}]  -> real is at the "
          f"{pct:.1f}th percentile ({nt} trades)")
    return dict(net_sw=net_sw, pct=pct, cot_adds=net - base_net, sharpe=sh, bh_sharpe=bsh)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--swap-long', type=float, default=-0.06, help='annual financing on longs (negative = you pay)')
    ap.add_argument('--swap-short', type=float, default=-0.02, help='annual financing on shorts')
    ap.add_argument('--seed', type=int, default=7)
    a = ap.parse_args()
    rng = np.random.default_rng(a.seed)
    df = data.load_ohlc('XAUUSD')
    section('FULL PERIOD', df, run_one('XAUUSD', True), run_one('XAUUSD', False), a, rng)
    cut = df.index[int(len(df) * 0.6)]
    oos = df[df.index >= cut]
    o = section('OUT-OF-SAMPLE 40% (REPORT.md split)', oos, run_one('XAUUSD', True, (cut, df.index[-1])),
                run_one('XAUUSD', False, (cut, df.index[-1])), a, rng)
    ok = o['net_sw'] > 0 and o['pct'] >= 95 and o['cot_adds'] > 0
    print("\nVERDICT (pre-registered, out-of-sample):")
    print(f"  (a) net after swap > 0          {'yes' if o['net_sw'] > 0 else 'NO'}")
    print(f"  (b) timing >= 95th percentile   {'yes' if o['pct'] >= 95 else 'NO'} ({o['pct']:.1f})")
    print(f"  (c) COT filter adds value       {'yes' if o['cot_adds'] > 0 else 'NO'}")
    print(f"  Sharpe {o['sharpe']:.2f} vs buy-and-hold {o['bh_sharpe']:.2f}")
    print(f"  => {'WORTH A FORWARD DEMO' if ok else 'NOT WORTH A DEMO'}")


if __name__ == '__main__':
    main()
