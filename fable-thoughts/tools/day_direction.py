#!/usr/bin/env python3
"""day_direction.py -- is each UTC day's direction decided in advance on Deriv synthetics?

THE CLAIM (from a reader): the engine is a volatility generator, but each day's net direction is decided
and the randomness is "veered" toward it, so a day is bullish or bearish by design and spikes or quiet
days relate to time of day.

WHAT THAT CLAIM PREDICTS, AND WHAT IT DOES NOT
- Deciding the close in advance and drawing the path as a bridge to it leaves NO trace if the close is
  drawn from the same distribution a free random walk would reach: a Brownian bridge to a
  N(0, sigma^2 T) endpoint is exactly Brownian motion. So "decided" on its own is untestable and
  harmless. It matters only if the decision is BIASED, and a bias leaves one of these signatures:
    H1 a fixed tilt: the mean hourly (simple) return is not zero          -> drift t-test
    H2 a per-day tilt: hours within a day share a direction, so the day's
       variance exceeds 24 x the hour variance                            -> variance ratio VR > 1
    H3 the early day tells you the rest: corr(first 12 h, last 12 h) > 0,
       and corr(first 3 h, last 21 h) > 0                                 -> half / early correlation
    H4 time matters: mean return (direction) or squared return (spikes,
       volatility) differs by UTC hour                                    -> hour-of-day F tests
  Each null comes from shuffling the hours across all days and positions (2000 permutations), which
  keeps the return distribution (spikes included) and destroys any day or clock structure.

CONTROLS
  RDBULL / RDBEAR are built with a daily drift (Deriv says so), so H1 must fire there, or the tool is
  broken. Every other symbol is expected to be null. A significant result elsewhere must survive
  Bonferroni over symbols x 6 tests before anyone believes it.

READ-ONLY: public tick_history candles, no token, no proposals, no orders.
RUN (from fable-thoughts/tools; needs websocket-client>=1.6, numpy, scipy)
  python3 day_direction.py                                      # a year of hourly candles, 13 symbols
  python3 day_direction.py --symbols BOOM300N CRASH1000 --hours 4000
  python3 day_direction.py --from-dir ../results/day_direction_<UTC>   # re-run offline on saved candles
Writes candles_<SYM>.json, day_direction.json and summary.txt to ../results/day_direction_<UTC>/.
"""
import argparse, datetime as dt, json, math, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import numpy as np
from scipy import stats

UTC = dt.timezone.utc
SYMBOLS = ('RDBULL', 'RDBEAR', 'CRASH1000', 'CRASH500', 'CRASH300N', 'BOOM1000', 'BOOM500', 'BOOM300N',
           '1HZ100V', 'R_100', '1HZ10V', 'JD100', 'stpRNG')
TESTS = ('H1_drift', 'H2_VR', 'H3_half', 'H3_early', 'H4_hour_mean', 'H4_hour_vol')
ALPHA = 0.01


def fetch_hourly(ws, sym, hours):
    seen, end = {}, 'latest'
    for _ in range(math.ceil(hours / 1000) + 2):
        req = {'ticks_history': sym, 'style': 'candles', 'granularity': 3600, 'count': 1000, 'end': end}
        if isinstance(end, int):
            req['start'] = end - 1000 * 3600 - 3600
        r = ws.call(req)
        if 'error' in r:
            print(f"  {sym}: {r['error'].get('code')}: {r['error'].get('message')}")
            break
        cs = r.get('candles') or []
        if not cs:
            break
        for c in cs:
            seen[int(c['epoch'])] = (float(c['open']), float(c['close']))
        end = min(int(c['epoch']) for c in cs) - 1
        if len(seen) >= hours:
            break
        time.sleep(0.4)
    ep = sorted(seen)[-hours:]
    return [[e, seen[e][0], seen[e][1]] for e in ep]


def day_matrix(candles):
    """Complete UTC days only -> (days, R[d, 24]) of within-day log returns, plus the flat hourly returns
    used for the drift test. Hour 0 runs from its own open: the midnight gap is left out, because
    RDBULL/RDBEAR reset to 1000 there and that jump would cancel the day's move (on continuous indices
    the gap is one tick)."""
    a = np.asarray(candles, float)
    ep, op, cl = a[:, 0].astype(np.int64), a[:, 1], a[:, 2]
    day, hod = ep // 86400, (ep % 86400) // 3600
    prev = np.concatenate([[op[0]], cl[:-1]])
    contiguous = np.concatenate([[False], np.diff(ep) == 3600])
    prev = np.where(contiguous & (hod != 0), prev, op)
    r = np.log(cl) - np.log(prev)
    rows, days = [], []
    for d in np.unique(day):
        m = day == d
        if m.sum() == 24 and np.array_equal(np.sort(hod[m]), np.arange(24)):
            rows.append(r[m][np.argsort(hod[m])])
            days.append(int(d))
    return np.array(days), (np.vstack(rows) if rows else np.zeros((0, 24))), r


def stats_of(R):
    """The five day/clock statistics of a days x 24 matrix."""
    D = R.sum(1)
    h1, h2 = R[:, :12].sum(1), R[:, 12:].sum(1)
    e1, e2 = R[:, :3].sum(1), R[:, 3:].sum(1)
    vr = D.var(ddof=1) / (24 * R.var(ddof=1))
    corr = lambda x, y: float(np.corrcoef(x, y)[0, 1])
    mu_h = R.mean(0)
    f_mean = R.shape[0] * mu_h.var(ddof=1) / R.var(ddof=1)
    Q = R ** 2
    f_vol = Q.shape[0] * Q.mean(0).var(ddof=1) / Q.var(ddof=1)
    return {'H2_VR': float(vr), 'H3_half': corr(h1, h2), 'H3_early': corr(e1, e2),
            'H4_hour_mean': float(f_mean), 'H4_hour_vol': float(f_vol),
            'P_same_sign_halves': float(np.mean(np.sign(h1) == np.sign(h2)))}


def analyse(sym, candles, n_perm=2000, seed=11):
    days, R, r = day_matrix(candles)
    out = {'sym': sym, 'hours': len(candles), 'complete_days': int(len(days))}
    if len(days) < 30:
        out['error'] = 'fewer than 30 complete days'
        return out
    simple = np.expm1(r[np.isfinite(r)])
    t = simple.mean() / (simple.std(ddof=1) / math.sqrt(len(simple)))
    out['H1_drift'] = {'mean_simple_per_hour': float(simple.mean()), 't': float(t),
                       'p': float(2 * stats.norm.sf(abs(t))), 'mean_log_per_hour': float(r.mean()),
                       'minus_var_half': float(-r.var() / 2), 'annualised_simple': float(simple.mean() * 8760)}
    obs = stats_of(R)
    rng = np.random.default_rng(seed)
    flat = R.ravel()
    null = {k: [] for k in obs}
    for _ in range(n_perm):
        s = stats_of(rng.permutation(flat).reshape(R.shape))
        for k, v in s.items():
            null[k].append(v)
    for k in ('H2_VR', 'H3_half', 'H3_early', 'H4_hour_mean', 'H4_hour_vol'):
        nv = np.asarray(null[k])
        p = (1 + np.sum(nv >= obs[k])) / (n_perm + 1)      # one-sided: the claim predicts "larger"
        out[k] = {'obs': obs[k], 'null_mean': float(nv.mean()), 'null_q99': float(np.quantile(nv, 0.99)), 'p': float(p),
                  'p_floor': 1 / (n_perm + 1), 'z': float((obs[k] - nv.mean()) / nv.std(ddof=1))}
    out['P_same_sign_halves'] = obs['P_same_sign_halves']
    out['P_day_up'] = float(np.mean(R.sum(1) > 0))
    out['day_return_sd'] = float(R.sum(1).std(ddof=1))
    return out


def report(results, say):
    n_tests = sum(1 for r in results if 'error' not in r) * len(TESTS)
    thr = ALPHA / max(n_tests, 1)
    say(f'Bonferroni threshold {thr:.2e} ({n_tests} tests at alpha {ALPHA})')
    say(f"{'symbol':10s} {'days':>5s} {'P(up)':>6s} {'drift t':>8s} {'VR':>6s} {'p':>7s} {'half r':>7s} {'p':>7s} "
        f"{'early r':>8s} {'p':>7s} {'hourF':>6s} {'p':>7s} {'volF':>6s} {'p':>7s}  flags")
    for r in results:
        if 'error' in r:
            say(f"{r['sym']:10s} {r['error']}")
            continue
        flags = [k for k in TESTS if r[k]['p'] < thr]
        say(f"{r['sym']:10s} {r['complete_days']:5d} {r['P_day_up']:6.3f} {r['H1_drift']['t']:8.2f} "
            f"{r['H2_VR']['obs']:6.3f} {r['H2_VR']['p']:7.4f} {r['H3_half']['obs']:7.3f} {r['H3_half']['p']:7.4f} "
            f"{r['H3_early']['obs']:8.3f} {r['H3_early']['p']:7.4f} {r['H4_hour_mean']['obs']:6.2f} "
            f"{r['H4_hour_mean']['p']:7.4f} {r['H4_hour_vol']['obs']:6.2f} {r['H4_hour_vol']['p']:7.4f}  "
            f"{' '.join(flags) or '-'}")
    ctrl = [r for r in results if r['sym'] in ('RDBULL', 'RDBEAR') and 'error' not in r]
    for r in ctrl:
        ok = r['H1_drift']['p'] < thr
        say(f"control {r['sym']}: built-in drift {'DETECTED' if ok else 'NOT detected -- the tool is not trustworthy'} "
            f"(t = {r['H1_drift']['t']:.1f})")
    return thr


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument('--symbols', nargs='+', default=list(SYMBOLS))
    ap.add_argument('--hours', type=int, default=8760)
    ap.add_argument('--perm', type=int, help='permutations per symbol (default: enough that the smallest '
                                              'possible p is a third of the Bonferroni threshold)')
    ap.add_argument('--from-dir')
    ap.add_argument('--outdir')
    a = ap.parse_args(argv)
    outdir = a.outdir or os.path.normpath(os.path.join(
        HERE, '..', 'results', 'day_direction_' + dt.datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')))
    os.makedirs(outdir, exist_ok=True)
    lines = []

    def say(s):
        print(s)
        lines.append(s)

    thr = ALPHA / (len(a.symbols) * len(TESTS))
    n_perm = a.perm or math.ceil(3 / thr)
    if 1 / (n_perm + 1) >= thr:
        say(f'WARNING: {n_perm} permutations cannot reach p < {thr:.2e}; H2-H4 can never flag')
    say(f'{n_perm} permutations per symbol (smallest possible p {1 / (n_perm + 1):.1e}, threshold {thr:.2e})')
    results = []
    ws = None
    for sym in a.symbols:
        path = os.path.join(a.from_dir or outdir, f'candles_{sym}.json')
        if a.from_dir:
            if not os.path.exists(path):
                say(f'{sym}: no saved candles')
                continue
            with open(path) as fh:
                candles = json.load(fh)
        else:
            if ws is None:
                from deriv_api import DerivWS
                ws = DerivWS(token='', timeout=30)
            try:
                candles = fetch_hourly(ws, sym, a.hours)
            except Exception as e:
                say(f'{sym}: fetch failed: {type(e).__name__}: {str(e)[:120]}')
                ws.close()
                ws = None
                continue
            with open(path, 'x') as fh:
                json.dump(candles, fh)
        if not candles:
            say(f'{sym}: no candles')
            continue
        print(f'  {sym}: {len(candles)} hours, {n_perm} permutations ...', flush=True)
        results.append(analyse(sym, candles, n_perm))
    if ws is not None:
        ws.close()
    report(results, say)
    with open(os.path.join(outdir, 'day_direction.json'), 'x') as fh:
        json.dump(results, fh, indent=1)
    with open(os.path.join(outdir, 'summary.txt'), 'x') as fh:
        fh.write('\n'.join(lines) + '\n')
    print(f'wrote {outdir}')
    return results


if __name__ == '__main__':
    main()
