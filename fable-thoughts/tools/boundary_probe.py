#!/usr/bin/env python3
"""boundary_probe.py — does the generator leak at its computational or macro boundaries?

generator_probe_v3 tests the tick-to-tick sequence. This tests the places a flawless GBM meets finite
machinery: years of history, number formats, and server maintenance. Read-only, public data only.

M  MACRO BOUNDARY (daily candles over the index's whole available life)
   A driftless GBM wanders without limit (median decays at -sigma^2/2 per unit time). If Deriv keeps an
   index in a "normal-looking" range, it must do it one of three ways, and each leaves a trace:
   M1 rebase: a jump between one day's close and the next day's open far larger than one tick
   M2 reflecting drift: next-day SIMPLE return biased against the move when spot sits in the top or
      bottom 5% of its RUNNING range. Running, not all-time: the all-time high is by construction a point
      after which price fell, so hindsight extremes manufacture "mean reversion" out of a pure random walk.
      Days near an extreme cluster in time (arcsine law), so the z is judged against simulated GBM paths
   M3 lifetime drift: mean simple return per day (zero for a price martingale) and log drift vs -sigma^2/2
   M4 volatility compression: NEXT-day |return| lower near the extremes than in the middle (today's move
      is biased: a big move is what put the price at a new extreme)
   Control: the same statistics on simulated GBM paths of the same length and sigma.

N  NUMBER FORMAT
   Double precision (53-bit mantissa) represents every quote to ~1e-8 pips or better, so rounding cannot
   skew a last digit. Single precision (24-bit) cannot: at R_75's spot one float32 step is ~39 pips.
   So the sharp test is "are quotes computed in float32 anywhere?":
   N1 float step at the observed spots, in pips, for float32 and float64
   N2 last digit and last two digits, chi2 per spot decile (a magnitude-dependent skew shows up here)
   N3 if a float32 step exceeds one pip, quotes would sit on a coarser grid: level mod that step must
      then concentrate; tested with chi2
   (The last-digit MARGINAL of a lattice random walk is uniform whatever sigma is, so N2 has a clean null.)

O  TIME BOUNDARIES
   A CSPRNG has no warm-up: after reseeding its output is fully random at once. What CAN show at a
   maintenance boundary is the plumbing: paused generation, a stale price, a burst.
   O1 feed gaps: when they happen (hour, weekday), and whether price moved through the gap like a walk
      that kept running (move ~ sigma*sqrt(gap)) or one that paused (move ~ sigma)
   O2 time-of-day volatility: realized variance in 288 five-minute bins; bins are permuted within each
      day (keeping each day's volatility) to get the null for "any bin differs"
   O3 midnight window (23:55-00:05 UTC): variance ratio, share of flat ticks, lag-1 autocorrelation and
      VR(8) against every other 10-minute window, with a day-block bootstrap
   Tradability bridge: volatility does not predict direction, but it moves accumulator survival. The
   report prints the volatility dip an accumulator at g=1% would need to break even, next to the
   largest dip found.

Run (from fable-thoughts/tools; uses the tick cache generator_probe wrote, or fetches one):
  python3 boundary_probe.py --symbol 1HZ30V --cache ../results/1HZ30V_9000000.npz --json-out ../results/1HZ30V_boundary.json
  python3 boundary_probe.py --symbol R_75 --cache ../results/R_75_1000000.npz        # fetches 1M ticks first
Block N is sharpest on the symbols with the most significant digits (R_75, 1HZ25V, 1HZ50V, 1HZ90V),
where one float32 step is larger than a pip.
The first run fetches daily candles for block M and caches them next to the tick cache.
"""
import argparse
import datetime as dt
import json
import math
import os
import time
from pathlib import Path

import numpy as np
from scipy import stats

UTC = dt.timezone.utc
ALPHA = 0.01


def utc(t):
    return dt.datetime.fromtimestamp(int(t), UTC).strftime('%Y-%m-%d %H:%M:%S')


def holm(p, alpha=ALPHA):
    items = sorted(((k, v) for k, v in p.items() if v == v), key=lambda kv: kv[1])
    flagged, m = set(), len(items)
    for i, (k, v) in enumerate(items):
        if v <= alpha / (m - i):
            flagged.add(k)
        else:
            break
    return flagged


def tstat(x):
    x = np.asarray(x, float)
    if len(x) < 3 or x.std(ddof=1) == 0:
        return float('nan'), float('nan')
    t = x.mean() / (x.std(ddof=1) / math.sqrt(len(x)))
    return float(t), float(2 * stats.t.sf(abs(t), len(x) - 1))


# ---------------------------------------------------------------- data
def load_ticks(cache):
    z = np.load(cache)
    t, p, pip = z['t'].astype(np.int64), z['p'].astype(float), int(z['pip'])
    o = np.argsort(t, kind='stable')
    return t[o], p[o], pip


def fetch_daily(symbol, max_days=20000):
    from deriv_api import DerivWS
    ws = DerivWS(token='', timeout=30)
    seen, end = {}, 'latest'
    try:
        while len(seen) < max_days:
            req = {'ticks_history': symbol, 'style': 'candles', 'granularity': 86400, 'count': 1000, 'end': end}
            if isinstance(end, int):
                req['start'] = end - 1000 * 86400 - 86400
            r = ws.call(req)
            if 'error' in r:
                print(f"  daily candles: {r['error'].get('code')}: {r['error'].get('message')}")
                break
            cs = r.get('candles') or []
            new = [c for c in cs if int(c['epoch']) not in seen]
            if not new:
                break
            for c in new:
                seen[int(c['epoch'])] = [int(c['epoch']), float(c['open']), float(c['high']), float(c['low']), float(c['close'])]
            end = min(int(c['epoch']) for c in cs) - 1
            time.sleep(0.4)
    finally:
        ws.close()
    return [seen[k] for k in sorted(seen)]


# ---------------------------------------------------------------- block M
def macro_stats(ep, op, cl, ticks_per_day, warmup=90):
    gap = np.log(op[1:] / cl[:-1])                # overnight: one tick apart on a 24/7 index
    intra = np.log(cl[1:] / op[1:])
    sig_tick = np.median(np.abs(intra)) * 1.4826 / math.sqrt(ticks_per_day)   # robust, rebase-proof
    gz = gap / sig_tick
    reb = np.abs(gz) > 10
    rebases = [{'utc': utc(ep[i + 1]), 'factor': float(op[i + 1] / cl[i]), 'z_ticks': float(gz[i])}
               for i in np.flatnonzero(reb)]
    r = np.where(reb, intra, gap + intra)         # daily log returns, chain-linked across rebases
    simple = np.expm1(r)
    sig_d = r.std(ddof=1)
    lc = np.log(cl[0]) + np.r_[0, np.cumsum(r)]
    pos = np.full(len(cl), np.nan)
    for i in range(warmup, len(cl)):
        lo, hi = lc[:i].min(), lc[:i].max()      # running range of the PAST only
        pos[i] = (lc[i] - lo) / (hi - lo) if hi > lo else 0.5
    pos, nxt = pos[:-1], simple                    # position today -> return to tomorrow
    ok = np.isfinite(pos)
    top, bot, mid = ok & (pos >= 0.95), ok & (pos <= 0.05), ok & (pos > 0.05) & (pos < 0.95)
    out = {'days': int(len(cl)), 'first_day': utc(ep[0])[:10], 'last_day': utc(ep[-1])[:10],
           'sigma_daily': float(sig_d), 'sigma_annual': float(sig_d * math.sqrt(365)),
           'rebases': rebases, 'max_overnight_gap_ticks': float(np.abs(gz).max()) if len(gz) else 0.0}
    t3, p3 = tstat(simple)
    out['M3 lifetime_simple_drift'] = {'mean_per_day': float(simple.mean()), 't': t3, 'p': p3,
                                       'log_drift_plus_half_var': float(r.mean() + r.var() / 2)}
    buckets = {}
    for name, m in (('top5', top), ('bottom5', bot), ('middle', mid)):
        t_, p_ = tstat(nxt[m])
        buckets[name] = {'days': int(m.sum()), 'mean_next_day': float(nxt[m].mean()) if m.any() else None,
                         't': t_, 'p': p_, 'mean_abs_next_day': float(np.abs(r[1:][m[:len(r) - 1]]).mean())
                         if m[:len(r) - 1].any() else None}
    out['M2 buckets'] = buckets
    if top.sum() >= 3 and bot.sum() >= 3:
        # reflection predicts top < 0 < bottom; test bottom - top
        diff = nxt[bot].mean() - nxt[top].mean()
        se = math.sqrt(nxt[bot].var(ddof=1) / bot.sum() + nxt[top].var(ddof=1) / top.sum())
        out['M2 reflection_bottom_minus_top'] = {'diff': float(diff), 'z': float(diff / se),
                                                 'p': float(2 * stats.norm.sf(abs(diff / se)))}
    ext = top | bot
    nr = np.abs(r[1:])                             # NEXT day's |return|: today's is biased (a big move is
    ext, mid_ = ext[:len(nr)], mid[:len(nr)]       # what put the price at a new extreme)
    if ext.sum() >= 3 and mid_.sum() >= 3:
        a, b = nr[ext], nr[mid_]
        z = (a.mean() - b.mean()) / math.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))
        out['M4 vol_extremes_over_middle'] = {'ratio': float(a.mean() / b.mean()), 'z': float(z),
                                              'p': float(2 * stats.norm.sf(abs(z)))}
    return out


def macro_block(candles, ticks_per_day, sims=200, seed=1):
    a = np.asarray(candles, float)
    ep, op, cl = a[:, 0].astype(np.int64), a[:, 1], a[:, 4]
    real = macro_stats(ep, op, cl, ticks_per_day)
    # control: GBM paths with the same length and daily sigma (no reflection by construction)
    rng = np.random.default_rng(seed)
    s = real['sigma_daily']
    diffs, vols = [], []
    for _ in range(sims):
        path = cl[0] * np.exp(np.cumsum(np.r_[0, rng.normal(-s * s / 2, s, len(cl) - 1)]))
        st = macro_stats(ep, np.r_[path[0], path[:-1]], path, ticks_per_day)   # open = previous close
        if 'M2 reflection_bottom_minus_top' in st:
            diffs.append(st['M2 reflection_bottom_minus_top']['z'])
        if 'M4 vol_extremes_over_middle' in st:
            vols.append(st['M4 vol_extremes_over_middle']['z'])
    real['control_M2_z_on_gbm'] = {'sims': len(diffs), 'mean_z': float(np.mean(diffs)) if diffs else None,
                                   'q01': float(np.quantile(diffs, 0.01)) if diffs else None,
                                   'q99': float(np.quantile(diffs, 0.99)) if diffs else None}
    # extreme days cluster in time (arcsine law), so the textbook z is too wide-tailed: judge the real z
    # against the spread of z over the simulated GBM paths instead
    for key, ctl in (('M2 reflection_bottom_minus_top', diffs), ('M4 vol_extremes_over_middle', vols)):
        if key in real and len(ctl) >= 20:
            zc = (real[key]['z'] - np.mean(ctl)) / np.std(ctl, ddof=1)
            real[key].update(z_textbook=real[key]['z'], p_textbook=real[key]['p'], z=float(zc),
                             p=float(2 * stats.norm.sf(abs(zc))), calibrated_on=len(ctl))
    return real


# ---------------------------------------------------------------- block N
def numeric_block(level, p, pip, deciles=10):
    spot = p
    out = {'spot_min': float(spot.min()), 'spot_median': float(np.median(spot)), 'spot_max': float(spot.max()),
           'significant_digits': int(len(str(int(spot.max()))) + pip)}
    for name, dtype in (('float64', np.float64), ('float32', np.float32)):
        out[f'{name}_step_pips_at_max_spot'] = float(np.spacing(dtype(spot.max())) * 10 ** pip)
    d1 = (level % 10).astype(np.int64)
    d2 = (level % 100).astype(np.int64)
    edges = np.quantile(spot, np.linspace(0, 1, deciles + 1))
    b = np.clip(np.searchsorted(edges, spot, side='right') - 1, 0, deciles - 1)
    rows, pv = [], {}
    for i in range(deciles):
        m = b == i
        c1 = np.bincount(d1[m], minlength=10)
        c2 = np.bincount(d2[m], minlength=100)
        x1 = float(((c1 - m.sum() / 10) ** 2 / (m.sum() / 10)).sum())
        x2 = float(((c2 - m.sum() / 100) ** 2 / (m.sum() / 100)).sum())
        p1, p2 = float(stats.chi2.sf(x1, 9)), float(stats.chi2.sf(x2, 99))
        rows.append({'decile': i, 'spot_from': float(edges[i]), 'spot_to': float(edges[i + 1]), 'n': int(m.sum()),
                     'last_digit_p': p1, 'last_two_digits_p': p2,
                     'digit_share_min': float(c1.min() / m.sum()), 'digit_share_max': float(c1.max() / m.sum())})
        pv[f'N2 last_digit decile {i}'] = p1
        pv[f'N2 last_two_digits decile {i}'] = p2
    out['N2 deciles'] = rows
    step32 = out['float32_step_pips_at_max_spot']
    if step32 > 1.5:
        g = int(round(step32))
        c = np.bincount((level % g).astype(np.int64), minlength=g)
        x = float(((c - len(level) / g) ** 2 / (len(level) / g)).sum())
        out['N3 float32_grid'] = {'grid_pips': g, 'chi2': x, 'p': float(stats.chi2.sf(x, g - 1)),
                                  'occupied_residues': int((c > 0).sum())}
        pv['N3 float32_grid'] = out['N3 float32_grid']['p']
    else:
        out['N3 float32_grid'] = 'not applicable: a float32 step is below 1.5 pips at this spot'
    return out, pv


# ---------------------------------------------------------------- block O
def accu_vol_dip_needed(g=0.01, p_stay=0.985):
    """Relative volatility dip that lifts (1+g)*P(stay) from its quoted level to 1 (Gaussian step)."""
    z0 = stats.norm.ppf((1 + p_stay) / 2)
    z1 = stats.norm.ppf((1 + 1 / (1 + g)) / 2)
    return 1 - z0 / z1


def time_block(t, level, interval, perms=200, seed=3):
    rng = np.random.default_rng(seed)
    d = np.diff(t)
    L = level.astype(float)
    r_all = np.diff(np.log(L))
    ok = d == interval
    r = r_all[ok]
    sig = r.std()
    out = {}
    # O1 gaps
    gi = np.flatnonzero(~ok)
    gaps = []
    for i in gi:
        n_missing = int(d[i] // interval)
        rr = r_all[i]
        gaps.append({'from_utc': utc(t[i]), 'seconds': int(d[i]), 'hour': int((t[i] % 86400) // 3600),
                     'weekday': dt.datetime.fromtimestamp(int(t[i]), UTC).strftime('%a'),
                     'z_if_paused': float(rr / sig), 'z_if_running': float(rr / (sig * math.sqrt(max(n_missing, 1))))})
    out['O1 gaps'] = gaps
    if gaps:
        zp = np.array([g['z_if_paused'] for g in gaps])
        zr = np.array([g['z_if_running'] for g in gaps])
        out['O1 gap_model'] = {'n': len(gaps), 'mean_z2_if_paused': float((zp ** 2).mean()),
                               'mean_z2_if_running': float((zr ** 2).mean()),
                               'reading': 'the model whose mean z^2 is near 1 describes the gaps'}
    # O2 five-minute-of-day volatility profile, bins permuted within each day
    ts = t[1:][ok]
    day = ts // 86400
    b = (ts % 86400) // 300
    ud, di = np.unique(day, return_inverse=True)
    full = np.bincount(di, minlength=len(ud)) >= 0.9 * 86400 / interval
    key = di * 288 + b
    s2 = np.bincount(key, weights=r * r, minlength=len(ud) * 288).reshape(len(ud), 288)
    cnt = np.bincount(key, minlength=len(ud) * 288).reshape(len(ud), 288)
    s2, cnt = s2[full], cnt[full]
    rv = s2 / np.maximum(cnt, 1)                  # mean squared return per (day, bin)
    valid = cnt > 0.5 * 300 / interval

    def profile(mat):
        m = np.where(valid, mat, np.nan)
        prof = np.nanmean(m, axis=0)
        return prof / np.nanmean(prof)

    prof = profile(rv)
    stat = float(np.nanvar(prof))
    null = []
    for _ in range(perms):
        idx = np.argsort(rng.random(rv.shape), axis=1)
        null.append(float(np.nanvar(profile(np.take_along_axis(rv, idx, axis=1)))))
    null = np.array(null)
    # p from a z-score against the permutation distribution (a count-based p cannot go below
    # 1/(perms+1), which is above the Holm threshold); the count-based p is kept for reference
    z2 = (stat - null.mean()) / null.std(ddof=1)
    out['O2 time_of_day_vol'] = {'days': int(full.sum()), 'dispersion': stat, 'null_q99': float(np.quantile(null, 0.99)),
                                 'z': float(z2), 'p': float(stats.norm.sf(z2)),
                                 'p_count': float((1 + (null >= stat).sum()) / (perms + 1)),
                                 'lowest_bin_ratio': float(np.nanmin(prof)), 'lowest_bin_utc': f'{int(np.nanargmin(prof)) * 5 // 60:02d}:{int(np.nanargmin(prof)) * 5 % 60:02d}',
                                 'highest_bin_ratio': float(np.nanmax(prof)), 'highest_bin_utc': f'{int(np.nanargmax(prof)) * 5 // 60:02d}:{int(np.nanargmax(prof)) * 5 % 60:02d}'}
    # O3 midnight window 23:55-00:05 vs all other 10-minute windows, day-block bootstrap
    sod = ts % 86400
    mid = (sod >= 86400 - 300) | (sod < 300)
    rest = ~mid
    flat = r == 0
    lag = np.r_[False, (np.diff(ts) == interval)]            # consecutive pair for lag-1 products
    prod = np.r_[0.0, r[1:] * r[:-1]]
    ub = np.unique(day)

    def stats_for(sel_days):
        m_mid = mid & np.isin(day, sel_days)
        m_rest = rest & np.isin(day, sel_days)
        v_ratio = (r[m_mid] ** 2).mean() / (r[m_rest] ** 2).mean()
        f_diff = flat[m_mid].mean() - flat[m_rest].mean()
        a_mid = prod[m_mid & lag].sum() / (r[m_mid & lag] ** 2).sum()
        a_rest = prod[m_rest & lag].sum() / (r[m_rest & lag] ** 2).sum()
        return v_ratio, f_diff, a_mid - a_rest

    base = stats_for(ub)
    boots = np.array([stats_for(rng.choice(ub, len(ub))) for _ in range(min(perms, 200))])
    names = ('variance_ratio', 'flat_share_diff', 'lag1_acf_diff')
    o3 = {}
    for j, nm in enumerate(names):
        centre = 1.0 if nm == 'variance_ratio' else 0.0
        se = boots[:, j].std(ddof=1)
        z = (base[j] - centre) / se if se > 0 else 0.0
        o3[nm] = {'value': float(base[j]), 'se': float(se), 'z': float(z), 'p': float(2 * stats.norm.sf(abs(z)))}
    out['O3 midnight_window'] = o3
    # VR(8) inside midnight windows vs rest (Hurst proxy: VR(q) = q^(2H-1))
    def vr8(mask):
        x = np.where(mask, r, 0.0)
        c = np.cumsum(np.r_[0, x])
        s8 = c[8:] - c[:-8]
        w = np.convolve(mask.astype(float), np.ones(8), 'valid') == 8
        return (s8[w] ** 2).mean() / (8 * (r[mask] ** 2).mean())
    v_mid, v_rest = vr8(mid), vr8(rest)
    out['O3 hurst_proxy'] = {'VR8_midnight': float(v_mid), 'VR8_rest': float(v_rest),
                             'H_midnight': float(0.5 + math.log(v_mid) / (2 * math.log(8))),
                             'H_rest': float(0.5 + math.log(v_rest) / (2 * math.log(8)))}
    out['accu_vol_dip_needed_g1pct'] = accu_vol_dip_needed()
    return out


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument('--symbol', required=True)
    ap.add_argument('--cache', required=True, help='tick cache .npz (t, p, pip) from generator_probe; if it does not '
                                               'exist, --ticks ticks are fetched (public) and saved there first')
    ap.add_argument('--ticks', type=int, default=1_000_000, help='ticks to fetch when --cache does not exist')
    ap.add_argument('--daily-cache', help='daily candles JSON (default: next to the tick cache)')
    ap.add_argument('--no-macro', action='store_true', help='skip block M (no network)')
    ap.add_argument('--perms', type=int, default=200)
    ap.add_argument('--json-out')
    a = ap.parse_args()

    if not os.path.exists(a.cache):
        from deriv_api import DerivWS
        from derivfetch import fetch_ticks
        print(f'{a.cache} not found: fetching {a.ticks:,} ticks of {a.symbol} (public) ...')
        ws = DerivWS(token='', timeout=15)
        try:
            tt, pp, pipd = fetch_ticks(ws, a.symbol, a.ticks)
        finally:
            ws.close()
        Path(a.cache).parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(a.cache, t=tt, p=pp, pip=pipd)
        print(f'saved {a.cache}')
    t, p, pip = load_ticks(a.cache)
    level = np.rint(p * 10 ** pip).astype(np.int64)
    interval = int(np.median(np.diff(t)))
    print(f'{a.symbol}: {len(t):,} ticks {utc(t[0])} -> {utc(t[-1])} UTC, interval {interval}s, pip {pip}')
    res, pvals = {'symbol': a.symbol}, {}

    # ---- M
    if not a.no_macro:
        dpath = Path(a.daily_cache or Path(a.cache).with_name(f'{a.symbol}_daily.json'))
        if dpath.exists():
            candles = json.loads(dpath.read_text())
            print(f'loaded daily candles {dpath}')
        else:
            print('fetching daily candles (public) ...')
            candles = fetch_daily(a.symbol)
            dpath.write_text(json.dumps(candles))
            print(f'saved {dpath}')
        if len(candles) > 120:
            M = macro_block(candles, 86400 / interval)
            res['M'] = M
            print(f'\n{"=" * 78}\nM  MACRO BOUNDARY ({M["days"]} days, {M["first_day"]} -> {M["last_day"]}, '
                  f'sigma {M["sigma_annual"]:.3f}/yr)\n{"=" * 78}')
            print(f"  M1 rebases (overnight jump > 10 tick-sigmas): {len(M['rebases'])}   "
                  f"largest overnight jump {M['max_overnight_gap_ticks']:.1f} tick-sigmas")
            for rb in M['rebases'][:10]:
                print(f"     {rb['utc']}  x{rb['factor']:.4f}  ({rb['z_ticks']:+.0f} tick-sigmas)")
            m3 = M['M3 lifetime_simple_drift']
            print(f"  M3 lifetime simple drift {m3['mean_per_day'] * 100:+.4f}%/day  t {m3['t']:.2f}  p {m3['p']:.3g}"
                  f"   log drift + var/2 = {m3['log_drift_plus_half_var']:+.2e}")
            pvals['M3 lifetime_simple_drift'] = m3['p']
            for k, v in M['M2 buckets'].items():
                print(f"  M2 {k:<8} days {v['days']:>5}  next-day mean {100 * (v['mean_next_day'] or 0):+.4f}%  "
                      f"t {v['t']:.2f}  next-day mean |r| {100 * (v['mean_abs_next_day'] or 0):.3f}%")
            if 'M2 reflection_bottom_minus_top' in M:
                m2 = M['M2 reflection_bottom_minus_top']
                c = M['control_M2_z_on_gbm']
                ctl = (f"GBM control z 1-99%: [{c['q01']:.2f}, {c['q99']:.2f}] over {c['sims']} paths"
                       if c['sims'] else 'GBM control: no path reached both extremes')
                print(f"  M2 reflection (bottom-5% minus top-5% next-day return) textbook z {m2.get('z_textbook', m2['z']):+.2f}"
                      f"   {ctl}   calibrated z {m2['z']:+.2f}  p {m2['p']:.3g}")
                pvals['M2 reflection'] = m2['p']
            if 'M4 vol_extremes_over_middle' in M:
                m4 = M['M4 vol_extremes_over_middle']
                print(f"  M4 next-day |return| at extremes / middle {m4['ratio']:.3f}  calibrated z {m4['z']:+.2f}  p {m4['p']:.3g}")
                pvals['M4 vol_compression'] = m4['p']
        else:
            print(f'block M skipped: only {len(candles)} daily candles')

    # ---- N
    N, pn = numeric_block(level, p, pip)
    res['N'] = N
    pvals.update(pn)
    print(f'\n{"=" * 78}\nN  NUMBER FORMAT\n{"=" * 78}')
    print(f"  spot {N['spot_min']:.6g} .. {N['spot_max']:.6g}, {N['significant_digits']} significant digits")
    print(f"  one float64 step = {N['float64_step_pips_at_max_spot']:.2e} pips, one float32 step = "
          f"{N['float32_step_pips_at_max_spot']:.3f} pips (at the highest spot)")
    for row in N['N2 deciles']:
        print(f"  decile {row['decile']}  spot {row['spot_from']:.6g}-{row['spot_to']:.6g}  last digit p {row['last_digit_p']:.3g}"
              f"  (shares {row['digit_share_min']:.4f}-{row['digit_share_max']:.4f})  last two digits p {row['last_two_digits_p']:.3g}")
    print(f"  N3 float32 grid: {N['N3 float32_grid']}")

    # ---- O
    O = time_block(t, level, interval, a.perms)
    res['O'] = O
    print(f'\n{"=" * 78}\nO  TIME BOUNDARIES\n{"=" * 78}')
    print(f"  O1 feed gaps: {len(O['O1 gaps'])}")
    for g in O['O1 gaps'][:20]:
        print(f"     {g['from_utc']} UTC ({g['weekday']})  {g['seconds']:>5}s   z if paused {g['z_if_paused']:+.2f}   "
              f"z if running {g['z_if_running']:+.2f}")
    if 'O1 gap_model' in O:
        gm = O['O1 gap_model']
        print(f"     mean z^2: paused {gm['mean_z2_if_paused']:.2f}  running {gm['mean_z2_if_running']:.2f}  ({gm['reading']})")
    o2 = O['O2 time_of_day_vol']
    print(f"  O2 5-minute volatility profile over {o2['days']} full days: dispersion {o2['dispersion']:.2e} vs null q99 "
          f"{o2['null_q99']:.2e}  z {o2['z']:+.2f}  p {o2['p']:.3g}")
    print(f"     lowest bin {o2['lowest_bin_utc']} UTC at {o2['lowest_bin_ratio']:.3f} x average, highest {o2['highest_bin_utc']} at "
          f"{o2['highest_bin_ratio']:.3f} x")
    pvals['O2 time_of_day_vol'] = o2['p']
    for k, v in O['O3 midnight_window'].items():
        print(f"  O3 midnight {k:<16} {v['value']:+.5f}  z {v['z']:+.2f}  p {v['p']:.3g}")
        pvals[f'O3 {k}'] = v['p']
    h = O['O3 hurst_proxy']
    print(f"  O3 Hurst proxy from VR(8): midnight H {h['H_midnight']:.3f}, rest H {h['H_rest']:.3f} (0.5 = random walk)")
    need = O['accu_vol_dip_needed_g1pct']
    dip = 1 - math.sqrt(o2['lowest_bin_ratio'])
    print(f"  bridge: an accumulator at g=1% breaks even only if volatility dips {need * 100:.1f}% below its quoted level;"
          f" the quietest 5-minute bin ({o2['lowest_bin_utc']} UTC) is {dip * 100:.1f}% below average volatility"
          f" (and a single-bin minimum over 288 bins is biased low by noise)")

    flagged = holm(pvals)
    print(f'\n{"=" * 78}\nVERDICT ({len(pvals)} tests, Holm family alpha {ALPHA})\n{"=" * 78}')
    if res.get('M', {}).get('rebases'):
        print(f"  M1: {len(res['M']['rebases'])} rebase(s) in the daily history (listed under M). A rebase is how the")
        print('  index is kept in range without touching the tick law; it is visible, not predictable.')
    if flagged:
        for k in sorted(flagged):
            print(f'  FLAG {k}  p = {pvals[k]:.3g}')
        print('  A flag is a lead, not an edge: it must repeat on a fresh, later sample, and it must change the odds')
        print('  of a contract you can actually buy, by more than that contract\'s margin.')
    else:
        print('  No boundary test survives correction: no rebase or reflecting drift, no float32 grid or')
        print('  magnitude-dependent digit skew, no time-of-day or midnight anomaly.')
    res['pvals'] = pvals
    res['flagged'] = sorted(flagged)
    if a.json_out:
        Path(a.json_out).write_text(json.dumps(res, indent=1, default=float))
        print(f'wrote {a.json_out}')


if __name__ == '__main__':
    main()
