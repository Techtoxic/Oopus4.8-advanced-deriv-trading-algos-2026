"""btc_leadlag.py: does Binance BTC lead Deriv's cryBTCUSD by enough to pay the multiplier cost?

THE IDEA (ledger 12.25, never tested)
Deriv's crypto price is built from outside exchanges. If it updates later than Binance, a big
Binance move tells you where Deriv is about to go. cryBTCUSD only offers multipliers
(MULTUP/MULTDOWN), so any lead must pay the multiplier commission.

DATA (public, no accounts, read-only)
  Binance  1-second BTCUSDT candles from data-api.binance.vision (the public market-data mirror)
  Deriv    cryBTCUSD ticks from the public ticks_history endpoint, last tick at or before each second
  cost     a MULTUP proposal on cryBTCUSD: commission as basis points of notional (stake x multiplier).
           If the proposal fails, pass --cost-bp yourself.

WHAT IS MEASURED
  1. Cross-correlation of 1-second returns, Binance at t vs Deriv at t+k, k = -10..+10 s.
     A peak at k > 0 means Binance leads.
  2. The trade, with latency: at second t, signal = Binance log move over the last W seconds.
     Deriv entry at t + LATENCY (default 2 s: your order plus the next tick; the entry-boundary
     argument of ledger A5 applies here too), exit H seconds later. Gain = Deriv move in the
     signal's direction, in bp, minus the round-trip cost. Events never overlap.

PRE-REGISTERED (fixed 2026-10-01, before any real run)
  grid: W in {1, 2, 5, 10} s, H in {5, 10, 30, 60} s, threshold = the 99th or 99.9th percentile of
        |signal| measured on the first half
  the FIRST half of the window picks one setting (highest 99% lower bound of net gain, among settings
  with >= 300 events there, so the equal-length second half can reach the 300 it needs to judge;
  with a 50-event floor the self-test picked the rarest threshold and could never pass);
  the SECOND half judges it:
  PASS  >= 300 events, mean net gain > 0 with its one-sided 99% lower bound > 0
  KILL  otherwise
  A PASS is a lead, not an edge: the next step is a live recorder that timestamps both feeds on
  arrival at your machine, because historical timestamps can hide or invent a delay.

Run:
  python3 btc_leadlag.py --hours 48
  python3 btc_leadlag.py --hours 48 --cost-bp 10       # if the proposal is refused
  python3 btc_leadlag.py --selftest
"""
import argparse
import json
import math
import time
import urllib.request

import numpy as np

LATENCY = 2
WS_ = [1, 2, 5, 10]
HS = [5, 10, 30, 60]
QS = [0.99, 0.999]
Z99 = 2.326
BINANCE = 'https://data-api.binance.vision/api/v3/klines'


# ---------------------------------------------------------------- data
def binance_1s(start_s, end_s, symbol='BTCUSDT'):
    out = {}
    cur = start_s * 1000
    while cur < end_s * 1000:
        url = f'{BINANCE}?symbol={symbol}&interval=1s&startTime={cur}&limit=1000'
        rows = json.load(urllib.request.urlopen(url, timeout=30))
        if not rows:
            break
        for r in rows:
            # the close belongs to the END of the 1 s candle; keying it by the open time made
            # Binance look 1 s later than it is (first real run: the +1 s peak was partly this)
            out[int(r[0]) // 1000 + 1] = float(r[4])
        cur = int(rows[-1][0]) + 1000
        time.sleep(0.05)
    ks = np.array(sorted(k for k in out if start_s <= k < end_s), dtype=np.int64)
    return ks, np.array([out[k] for k in ks])


def deriv_ticks(hours):
    from deriv_api import DerivWS
    ws = DerivWS(token='')
    try:
        t, p, _ = ws.history_paged('cryBTCUSD', int(hours * 3600 * 2.2), sleep=0.2)
        cost = multiplier_cost(ws)
    finally:
        ws.close()
    t = np.asarray(t, dtype=np.int64)
    keep = t >= t[-1] - int(hours * 3600)
    return t[keep], np.asarray(p, dtype=float)[keep], cost


def multiplier_cost(ws, stake=10.0):
    """Commission of a MULTUP on cryBTCUSD as bp of notional, at the lowest multiplier offered."""
    for mult in (10, 20, 50, 100):
        r = ws.call({'proposal': 1, 'amount': stake, 'basis': 'stake', 'contract_type': 'MULTUP',
                     'currency': 'USD', 'underlying_symbol': 'cryBTCUSD', 'multiplier': mult})
        if 'proposal' in r:
            com = float(r['proposal'].get('commission') or 0)
            return dict(multiplier=mult, commission=com, cost_bp=com / (stake * mult) * 1e4)
    return None


def on_grid(t, p, grid):
    """Last value at or before each grid second."""
    i = np.searchsorted(t, grid, side='right') - 1
    ok = i >= 0
    out = np.full(len(grid), np.nan)
    out[ok] = p[i[ok]]
    return out


# ---------------------------------------------------------------- analysis
def xcorr(b, d, kmax=10):
    rb, rd = np.diff(np.log(b)), np.diff(np.log(d))
    rows = []
    for k in range(-kmax, kmax + 1):
        if k >= 0:
            x, y = rb[:len(rb) - k], rd[k:]
        else:
            x, y = rb[-k:], rd[:len(rd) + k]
        m = np.isfinite(x) & np.isfinite(y)
        rows.append((k, float(np.corrcoef(x[m], y[m])[0, 1])))
    return rows


def events(b, d, W, H, thr, lo, hi, lat=LATENCY):
    """Non-overlapping signed Deriv moves (bp) after Binance moves beyond thr."""
    lb = np.log(b)
    ld = np.log(d)
    t = np.arange(max(lo, W), min(hi, len(b)) - lat - H)
    sig = lb[t] - lb[t - W]
    fire = t[np.abs(sig) > thr]
    out, free = [], -1
    for s in fire.tolist():
        if s < free:
            continue
        e, x = s + lat, s + lat + H
        if np.isfinite(ld[e]) and np.isfinite(ld[x]):
            out.append(np.sign(lb[s] - lb[s - W]) * (ld[x] - ld[e]) * 1e4)
            free = x
    return np.array(out)


def lb99(g):
    if len(g) < 2:
        return float('nan')
    return float(g.mean() - Z99 * g.std(ddof=1) / math.sqrt(len(g)))


def analyse(b, d, cost_bp, verbose=True):
    n = len(b)
    half = n // 2
    lbn = np.log(b)
    rows = []
    for W in WS_:
        s1 = np.abs(lbn[W:half] - lbn[:half - W])
        for q in QS:
            thr = float(np.quantile(s1[np.isfinite(s1)], q))
            for H in HS:
                g = events(b, d, W, H, thr, 0, half) - cost_bp
                rows.append(dict(W=W, H=H, q=q, thr=thr, n=len(g),
                                 gross=float((g + cost_bp).mean()) if len(g) else float('nan'),
                                 net=float(g.mean()) if len(g) else float('nan'), lb=lb99(g)))
    elig = [r for r in rows if r['n'] >= 300]
    if verbose:
        print(f"\nFIRST HALF (chooses), cost {cost_bp:.2f} bp round trip, latency {LATENCY} s")
        print(f"  {'W':>3} {'H':>3} {'q':>6} {'events':>7} {'gross bp':>9} {'net bp':>8} {'lb99':>8}")
        for r in sorted(rows, key=lambda r: -(r['lb'] if r['n'] >= 300 else -1e9))[:10]:
            print(f"  {r['W']:>3} {r['H']:>3} {r['q']:>6} {r['n']:>7} {r['gross']:>9.3f} {r['net']:>8.3f} {r['lb']:>8.3f}")
    if not elig:
        return dict(verdict='KILL', reason='no setting reached 300 events in the first half')
    best = max(elig, key=lambda r: r['lb'])
    g = events(b, d, best['W'], best['H'], best['thr'], half, n) - cost_bp
    res = dict(chosen=best, n=len(g), gross=float((g + cost_bp).mean()) if len(g) else float('nan'),
               net=float(g.mean()) if len(g) else float('nan'), lb=lb99(g))
    res['verdict'] = 'PASS' if res['n'] >= 300 and res['lb'] > 0 else 'KILL'
    if verbose:
        print(f"\nCHOSEN: W {best['W']} s, H {best['H']} s, threshold q{best['q']} ({best['thr']*1e4:.2f} bp)")
        print(f"SECOND HALF (judges): events {res['n']}  gross {res['gross']:.3f} bp  net {res['net']:.3f} bp  "
              f"99% lower bound {res['lb']:.3f} bp")
        print(f"VERDICT: {res['verdict']}")
    return res


def run(hours, cost_bp_arg):
    dt, dp, cost = deriv_ticks(hours)
    print(f"Deriv cryBTCUSD: {len(dt):,} ticks, {(dt[-1]-dt[0])/3600:.1f} h, "
          f"median gap {np.median(np.diff(dt)):.0f} s")
    if cost:
        print(f"multiplier cost: x{cost['multiplier']} commission ${cost['commission']:.4f} on $10 stake "
              f"= {cost['cost_bp']:.2f} bp of notional")
    cost_bp = cost_bp_arg if cost_bp_arg is not None else (cost['cost_bp'] if cost else None)
    if cost_bp is None:
        raise SystemExit('multiplier proposal refused; rerun with --cost-bp <bp>')
    bt, bp = binance_1s(int(dt[0]), int(dt[-1]))
    print(f"Binance BTCUSDT 1s: {len(bt):,} candles")
    grid = np.arange(max(dt[0], bt[0]), min(dt[-1], bt[-1]) + 1)
    b, d = on_grid(bt, bp, grid), on_grid(dt, dp, grid)
    print(f"aligned seconds: {len(grid):,}  Deriv seconds with a new price: "
          f"{float(np.mean(np.diff(d) != 0))*100:.0f}%")
    print("\nCROSS-CORRELATION of 1 s returns, Binance(t) vs Deriv(t+k)  (peak at k > 0 = Binance leads)")
    xc = xcorr(b, d)
    for k, c in xc:
        print(f"  k {k:+3d} s  {c:+.4f}  {'#' * int(max(c, 0) * 60)}")
    analyse(b, d, cost_bp)


# ---------------------------------------------------------------- self-test
def simulate(n, lag, noise_bp, seed):
    rng = np.random.default_rng(seed)
    b = 60000 * np.exp(np.cumsum(rng.normal(0, 1.5e-4, n)))
    d = np.r_[np.full(lag, b[0]), b[:n - lag]] * np.exp(rng.normal(0, noise_bp / 1e4, n))
    return b, d


def selftest():
    b, d = simulate(172_800, 3, 0.5, 1)
    xc = dict(xcorr(b, d))
    assert max(xc, key=xc.get) == 3, xc
    a = analyse(b, d, cost_bp=2.0, verbose=False)
    z = analyse(*simulate(172_800, 0, 0.5, 2), cost_bp=2.0, verbose=False)
    big = analyse(b, d, cost_bp=40.0, verbose=False)
    print(f"Deriv lags 3 s, cost 2 bp  : {a['verdict']} (net {a['net']:.2f} bp, n {a['n']})")
    print(f"no lag,       cost 2 bp    : {z['verdict']} (net {z['net']:.2f} bp)")
    print(f"Deriv lags 3 s, cost 40 bp : {big['verdict']} (net {big['net']:.2f} bp)")
    assert a['verdict'] == 'PASS' and z['verdict'] == 'KILL' and big['verdict'] == 'KILL'
    print('selftest ok')


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--hours', type=float, default=48)
    ap.add_argument('--cost-bp', type=float, default=None, help='round-trip cost in bp if the proposal is refused')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    run(a.hours, a.cost_bp)


if __name__ == '__main__':
    main()
