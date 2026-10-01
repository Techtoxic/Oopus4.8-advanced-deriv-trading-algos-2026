"""drift_regime_test.py: can the drift regimes of a Drift Switching index pay the MT5 spread?

WHAT WE KNOW (generator_probe_v3 on DSI20, 31M ticks, 365 days, 2026-10-01)
  Variance ratios rise with horizon (VR16 1.013, VR64 1.054, VR256 1.197) and replicate in both
  halves of the year. For q well inside a regime, VR(q) - 1 ~ q (mu/sigma)^2, and all three
  give a regime drift of ~0.028 tick-sigma per tick. That is the designed regime drift, and it
  is real. The question is only whether it pays the cost.
  The cost is large: median spread 3.15 = ~23 tick-sigma, so ~800 ticks of PERFECTLY KNOWN drift
  pay one round trip, and detecting the drift at 2 sigma takes ~5,000 ticks. Breakouts on
  1-minute bars continued by 0.57-1.51 price units against that 3.15 spread. The low end of
  the spread (p5 0.41, ~3 tick-sigma) is the only place this can work.

THE TEST (one causal momentum family, fixed before any run on real data)
  signal at tick i: z = (mid[i] - mid[i-L]) / (sigma_tick * sqrt(L)),  sigma from the training part
  long if z > theta, short if z < -theta, optionally only if spread[i] <= training p25 of spread
  fill at tick i+1 (one tick of latency): long buys the ASK, short sells the BID
  exit at tick i+1+H at the other side of the book (long sells the BID, short buys the ASK)
  the signal is checked every 60 ticks; a signal that arrives while a trade is open is skipped,
  so trades never overlap (overlapping trades fake precision, ledger bug 20); any window that
  crosses a feed gap is dropped
  grid: L in {60, 300, 900, 1800}, theta in {1, 1.5, 2, 3}, H in {60, 300, 900, 1800},
        spread filter in {all, <= p25}  -> 128 configurations
  The first 70% of the file (in time) chooses ONE configuration: the highest one-sided 99% lower
  bound on mean net P&L, among those with >= 200 trades. (Choosing by the mean alone picked rare,
  lucky configurations in the self-test, too sparse to judge.) The last 30% judges that configuration only. The other 127 are shown, not judged.

PRE-REGISTERED VERDICT
  PASS     on the last 30%: >= 300 trades, mean net P&L > 0 with the one-sided 99% lower bound
           (mean - 2.326 sd / sqrt(n)) above 0, AND the placebo (the same signal shifted by a
           third of the series, so it no longer lines up with the prices) is not significantly
           positive (its own 99% lower bound <= 0; requiring a placebo mean <= 0 fails half the
           time on noise alone).
  KILL     otherwise.
  INVALID  a built-in control fails (--selftest: a simulated drift-switching series with a small
           spread must PASS; the same series with a median-like spread and a plain random walk
           must KILL).
  A PASS is a candidate, not an edge: it must hold on the NEXT month of ticks, and costs not
  modelled here (swap on positions held over rollover, slippage beyond one tick, Deriv
  changing the spread) must be checked on a demo account first.

Run:
  python3 drift_regime_test.py --npz ../results/DSI_mid.npz
  python3 drift_regime_test.py --selftest
"""
import argparse
import math

import numpy as np

LS = [60, 300, 900, 1800]
THETAS = [1.0, 1.5, 2.0, 3.0]
HS = [60, 300, 900, 1800]
FILTERS = ['all', 'p25']
MIN_TRAIN, MIN_TEST, Z99 = 200, 300, 2.326
STEP = 60


def load(path):
    z = np.load(path)
    t = z['t'].astype(np.int64)
    p = z['p'].astype(float)
    sp = z['spread'].astype(float)
    kind = str(z['price']) if 'price' in z.files else 'bid'
    mid = p + {'bid': 0.5, 'ask': -0.5, 'mid': 0.0}[kind] * sp
    return t, mid, sp


def run(t, mid, sp, L, theta, H, filt, lo, hi, sig, sp_cap, shift=0):
    """Net and gross P&L (in bp of entry mid) of non-overlapping trades decided in [lo, hi)."""
    dt = int(np.median(np.diff(t)))
    start = max(lo, L)
    i = np.arange(start, hi - H - 2, STEP)                   # decision ticks, every STEP
    if len(i) == 0:
        return np.array([]), np.array([])
    j = i - shift if shift else i                             # placebo: signal from elsewhere
    ok = (j - L >= 0) & (j < len(mid))
    i, j = i[ok], j[ok]
    z = (mid[j] - mid[j - L]) / (sig * mid[j - L] * math.sqrt(L))
    side = np.where(z > theta, 1, np.where(z < -theta, -1, 0))
    if filt == 'p25':
        side = np.where(sp[i] <= sp_cap, side, 0)
    e, x = i + 1, i + 1 + H
    span_ok = (t[x] - t[i - L]) == (L + 1 + H) * dt           # no feed gap anywhere in the window
    if shift:
        span_ok &= (t[j] - t[j - L]) == L * dt
    m = (side != 0) & span_ok
    i, e, x, side = i[m], e[m], x[m], side[m]
    keep, free = [], -1                                       # greedy: no overlapping trades
    for k, ii in enumerate(i.tolist()):
        if ii >= free:
            keep.append(k)
            free = ii + 1 + H + 1
    i, e, x, side = i[keep], e[keep], x[keep], side[keep]
    gross = side * (mid[x] - mid[e]) / mid[e] * 1e4
    cost = (sp[e] + sp[x]) / 2 / mid[e] * 1e4                 # half spread in, half spread out
    return gross - cost, gross


def stats(net):
    n = len(net)
    if n < 2:
        return dict(n=n, mean=float('nan'), lb=float('nan'), sd=float('nan'))
    mu, sd = float(net.mean()), float(net.std(ddof=1))
    return dict(n=n, mean=mu, sd=sd, lb=mu - Z99 * sd / math.sqrt(n))


def evaluate(t, mid, sp, verbose=True):
    n = len(mid)
    cut = int(n * 0.7)
    r = np.diff(np.log(mid[:cut]))
    okd = np.diff(t[:cut]) == int(np.median(np.diff(t)))
    r = r[okd]
    mad = np.median(np.abs(r)) * 1.4826
    sig = float(np.sqrt(np.mean(r[np.abs(r) < 8 * mad] ** 2)))
    sp_cap = float(np.percentile(sp[:cut], 25))
    rows = []
    for L in LS:
        for th in THETAS:
            for H in HS:
                for f in FILTERS:
                    net, gross = run(t, mid, sp, L, th, H, f, 0, cut, sig, sp_cap)
                    s = stats(net)
                    s.update(L=L, theta=th, H=H, filt=f, gross=float(gross.mean()) if len(gross) else float('nan'))
                    rows.append(s)
    elig = [r_ for r_ in rows if r_['n'] >= MIN_TRAIN]
    if verbose:
        sp_bp = np.median(sp / mid) * 1e4
        print(f"ticks {n:,}  train {cut:,}  test {n - cut:,}  tick sigma {sig*1e4:.3f} bp  "
              f"median spread {sp_bp:.2f} bp ({sp_bp / (sig*1e4):.1f} tick-sigma)  p25 cap {sp_cap:.4f}")
        print("\nTRAIN (first 70%), top 8 by 99% lower bound, configs with >= 200 trades")
        print(f"  {'L':>5} {'theta':>5} {'H':>5} {'filter':>6} {'trades':>8} {'gross bp':>9} {'net bp':>8} {'lb99 bp':>8}")
        for r_ in sorted(elig, key=lambda r_: -r_['lb'])[:8]:
            print(f"  {r_['L']:>5} {r_['theta']:>5} {r_['H']:>5} {r_['filt']:>6} {r_['n']:>8,} "
                  f"{r_['gross']:>9.3f} {r_['mean']:>8.3f} {r_['lb']:>8.3f}")
    if not elig:
        return dict(verdict='KILL', reason='no configuration reached 200 training trades')
    best = max(elig, key=lambda r_: r_['lb'])
    net, gross = run(t, mid, sp, best['L'], best['theta'], best['H'], best['filt'], cut, n, sig, sp_cap)
    ts = stats(net)
    pl, _ = run(t, mid, sp, best['L'], best['theta'], best['H'], best['filt'], cut, n, sig, sp_cap,
                shift=n // 3)
    ps = stats(pl)
    passed = ts['n'] >= MIN_TEST and ts['lb'] > 0 and not (ps['n'] >= 2 and ps['lb'] > 0)
    out = dict(chosen=best, test=ts, test_gross=float(gross.mean()) if len(gross) else float('nan'),
               placebo=ps, verdict='PASS' if passed else 'KILL')
    if verbose:
        b = best
        print(f"\nCHOSEN ON TRAIN: L {b['L']}  theta {b['theta']}  H {b['H']}  filter {b['filt']}")
        print(f"TEST (last 30%):   trades {ts['n']:,}  gross {out['test_gross']:.3f} bp  net {ts['mean']:.3f} bp  "
              f"99% lower bound {ts['lb']:.3f} bp")
        print(f"PLACEBO (shifted): trades {ps['n']:,}  net {ps['mean']:.3f} bp  lb99 {ps['lb']:.3f}  "
              f"(must not be significantly positive)")
        print(f"\nVERDICT: {out['verdict']}")
        if out['verdict'] == 'KILL':
            print("  The regimes are real, but on these terms the drift that a causal rule can catch does not pay\n"
                  "  the spread. Gross vs net above shows how much of the move the spread takes.")
        else:
            print("  Candidate only: rerun on the NEXT month of ticks, then a small demo with swap and real fills.")
    return out


def simulate(n, mu_sig, regime, spread_sig, seed, s=2e-5, S0=1000.0):
    """Drift-switching walk: regimes of geometric length with drift +mu, -mu or 0, constant spread."""
    rng = np.random.default_rng(seed)
    drift = np.empty(n)
    k = 0
    while k < n:
        ln = int(rng.geometric(1 / regime))
        drift[k:k + ln] = rng.choice([mu_sig, -mu_sig, 0.0]) * s
        k += ln
    mid = S0 * np.exp(np.cumsum(rng.normal(0, s, n) + drift))
    t = np.arange(n, dtype=np.int64) + 1_700_000_000
    sp = np.full(n, spread_sig * s * S0)
    return t, mid, sp


def selftest():
    n = 14_000_000          # half the real DSI20 file; enough test trades for the 300 minimum
    a = evaluate(*simulate(n, 0.06, 1200, 2.0, 1), verbose=False)
    b = evaluate(*simulate(n, 0.06, 1200, 60.0, 2), verbose=False)
    c = evaluate(*simulate(n, 0.0, 1200, 2.0, 3), verbose=False)
    print(f"strong drift, small spread : {a['verdict']}  (net {a['test']['mean']:.3f} bp, lb {a['test']['lb']:.3f})")
    print(f"strong drift, large spread : {b['verdict']}  (net {b['test']['mean']:.3f} bp)")
    print(f"random walk,  small spread : {c['verdict']}  (net {c['test']['mean']:.3f} bp)")
    assert a['verdict'] == 'PASS' and b['verdict'] == 'KILL' and c['verdict'] == 'KILL'
    print('selftest ok')


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--npz', help='file from mt5_ticks_to_npz.py (mid price recommended)')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.npz:
        ap.error('give --npz')
    evaluate(*load(a.npz))


if __name__ == '__main__':
    main()
