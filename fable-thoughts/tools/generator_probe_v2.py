"""generator_probe.py — randomness / predictability battery for a Deriv synthetic index.

Read-only: public tick history only. No token, no orders.

Blocks
  A  digit sequence (earlier probe): longest exact repeat, LZ76, digit marginal chi2
  B  linear dependence of increments: Ljung-Box Q(1..50), variance ratio at 2..256 ticks
     (VR>1 trending, VR<1 mean-reverting, VR=1 random walk)
  C  rounding-aware lag-1: a continuous walk rounded to the pip grid MUST show a small negative
     lag-1 autocorrelation; compared against the value predicted from the fitted step law
  D  nonlinear dependence: |dx| and dx^2 autocorrelation (volatility clustering), ordinal-pattern
     (permutation) test order 3..5, BDS-style correlation-integral test on a subsample
  E  spectral: Fisher's g test for any hidden periodicity (cycles) in the increment series
  F  bit-level NIST-style tests on the up/down sign stream: frequency, runs, longest run,
     serial (m=2..8), approximate entropy, cumulative sums
  G  distribution: jump/diffusion split, jump rate, jump-gap exponential (memoryless) check,
     diffusion step vs discretised normal
  H  Bollinger mean-reversion claim: after price closes outside a 20-bar 2-sd band, is the
     forward return biased back toward the band? At tick level and 1-minute bars, h = 1..60
  I  split-half replication of every flagged result

Controls (known-answer): every statistic is also computed on
  - NULL: the real increments randomly shuffled and re-rounded (same step law, no order) -> should pass
  - PLANTED: the same walk with AR(1) phi=0.01 and mild GARCH added -> should be flagged
so you can see the battery has power, not just that the real series "passes".

Multiple testing: all p-values are Holm-corrected at family alpha 0.01.

Run (reuses a cache so 9M ticks are fetched once):
  python3 generator_probe.py --symbol JD100 --ticks 9000000
  python3 generator_probe.py --symbol JD100 --cache ../results/JD100_9000000.npz
"""
import argparse
import math
import time
from pathlib import Path

import numpy as np

# ---------------------------------------------------------------- p-value helpers (numpy only)
def norm_sf(z):
    return 0.5 * math.erfc(z / math.sqrt(2))


def chi2_sf(x, k):
    """Wilson–Hilferty approximation; accurate enough for k >= 3."""
    if k <= 0:
        return float('nan')
    z = ((x / k) ** (1 / 3) - (1 - 2 / (9 * k))) / math.sqrt(2 / (9 * k))
    return norm_sf(z)


def two_sided(z):
    return 2 * norm_sf(abs(z))


# ---------------------------------------------------------------- data
def load(symbol, n, cache):
    path = Path(cache) if cache else Path(f'../results/{symbol}_{n}.npz')
    if path.exists():
        z = np.load(path)
        print(f'loaded cache {path}')
        return z['t'], z['p'], int(z['pip'])
    from deriv_api import DerivWS
    from derivfetch import fetch_ticks
    ws = DerivWS(token='', timeout=15)
    try:
        t, p, pip = fetch_ticks(ws, symbol, n)
    finally:
        ws.ws.close()
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, t=t, p=p, pip=pip)
    print(f'saved cache {path}')
    return t, p, pip


# ---------------------------------------------------------------- block A (earlier probe)
def longest_repeat_len(s, kmax=40):
    s = np.asarray(s, dtype=np.int64)
    n = len(s)

    def has(k):
        if k > n:
            return False
        w = np.lib.stride_tricks.sliding_window_view(s, k)
        h = np.zeros(len(w), dtype=np.uint64)
        for j in range(k):
            h = h * np.uint64(10) + w[:, j].astype(np.uint64)
        return len(np.unique(h)) < len(h)

    lo, hi = 0, min(kmax, 19)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        lo, hi = (mid, hi) if has(mid) else (lo, mid - 1)
    return lo


def lz76(s, cap):
    s = list(map(int, s[:cap]))
    n = len(s)
    i, c, l, k, kmax = 0, 1, 1, 1, 1
    while l + k <= n:
        if s[i + k - 1] == s[l + k - 1]:
            k += 1
        else:
            kmax = max(kmax, k)
            i += 1
            if i == l:
                c += 1
                l += kmax
                i, kmax = 0, 1
            k = 1
    return c + (l <= n)


# ---------------------------------------------------------------- blocks B–G on an increment series
def acf(x, lags):
    x = x - x.mean()
    v = np.dot(x, x)
    return np.array([np.dot(x[:-k], x[k:]) / v for k in lags])


def ljung_box(x, m=50):
    n = len(x)
    r = acf(x, range(1, m + 1))
    q = n * (n + 2) * np.sum(r ** 2 / (n - np.arange(1, m + 1)))
    return q, chi2_sf(q, m), r


def variance_ratio(x, q):
    """Lo–MacKinlay heteroskedasticity-robust z for VR(q)."""
    n = len(x)
    mu = x.mean()
    d = x - mu
    s1 = np.dot(d, d) / n
    cs = np.concatenate([[0], np.cumsum(x)])
    rq = cs[q:] - cs[:-q] - q * mu
    vr = (np.dot(rq, rq) / (n - q + 1)) / (q * s1)
    d2 = d ** 2
    theta = 0.0
    for j in range(1, q):
        delta = np.dot(d2[j:], d2[:-j]) / (np.dot(d, d) ** 2 / n)
        theta += (2 * (q - j) / q) ** 2 * delta
    z = (vr - 1) / math.sqrt(theta / n) if theta > 0 else 0.0
    return vr, z


def rounding_lag1_expected(dx_cont_sd):
    """Rounding a continuous walk to integers adds white noise e_t (var 1/12) to levels, so
    increments get an MA(1) term: lag-1 autocorr = -(1/12) / (sd^2 + 2/12)."""
    return -(1 / 12) / (dx_cont_sd ** 2 + 2 / 12)


def permutation_test(x, order):
    w = np.lib.stride_tricks.sliding_window_view(x, order)[::order]      # non-overlapping windows
    ranks = np.argsort(np.argsort(w + 1e-9 * np.arange(order), axis=1), axis=1)
    code = (ranks * (order ** np.arange(order))).sum(1)
    _, counts = np.unique(code, return_counts=True)
    k = math.factorial(order)
    full = np.zeros(k)
    full[:len(counts)] = np.sort(counts)[::-1]
    exp = len(w) / k
    chi = float(((counts - exp) ** 2 / exp).sum() + (k - len(counts)) * exp)
    return chi, chi2_sf(chi, k - 1)


def bds_like(x, m=3, eps_sd=1.0, n_sub=4000, seed=0):
    """Correlation-integral test: C_m - C_1^m should be 0 for iid. z via permutation null."""
    rng = np.random.default_rng(seed)
    start = rng.integers(0, len(x) - n_sub - m)
    y = x[start:start + n_sub]
    eps = eps_sd * y.std()

    def cm(v, m):
        e = np.lib.stride_tricks.sliding_window_view(v, m)
        e = e[: 2500]
        d = np.max(np.abs(e[:, None, :] - e[None, :, :]), axis=2)
        iu = np.triu_indices(len(e), 1)
        return (d[iu] < eps).mean()

    stat = cm(y, m) - cm(y, 1) ** m
    null = [cm(p, m) - cm(p, 1) ** m for p in (rng.permutation(y) for _ in range(40))]
    z = (stat - np.mean(null)) / (np.std(null) + 1e-12)
    return stat, z


def fisher_g(x, max_n=2 ** 22):
    y = x[:max_n] - x[:max_n].mean()
    I = np.abs(np.fft.rfft(y))[1:-1] ** 2
    skip = 10                                       # periods longer than n/10 are drift, not cycles
    I = I[skip:]
    g = I.max() / I.sum()
    m = len(I)
    p = min(1.0, m * (1 - g) ** (m - 1))           # first-term approximation of Fisher's exact p
    return g, p, int(np.argmax(I) + 1 + skip)


# ---------------------------------------------------------------- block F (NIST-style on signs)
def nist_signs(b):
    n = len(b)
    out = {}
    s = 2 * b.astype(np.int64) - 1
    out['frequency'] = (two_sided(s.sum() / math.sqrt(n)), s.sum() / math.sqrt(n))
    pi = b.mean()
    runs = 1 + np.count_nonzero(b[1:] != b[:-1])
    zr = (runs - 2 * n * pi * (1 - pi)) / (2 * math.sqrt(2 * n) * pi * (1 - pi))
    out['runs'] = (two_sided(zr), zr)
    # longest run of ones in 10,000-bit blocks vs simulation of iid blocks
    def longest(block):
        z = np.diff(np.concatenate([[0], block, [0]]))
        st, en = np.where(z == 1)[0], np.where(z == -1)[0]
        return int((en - st).max()) if len(st) else 0
    M = 10000
    nb = min(n // M, 400)
    obs = np.array([longest(b[i * M:(i + 1) * M]) for i in range(nb)])
    rng = np.random.default_rng(1)
    ref = np.array([longest(rng.integers(0, 2, M)) for _ in range(nb)])
    zl = (obs.mean() - ref.mean()) / math.sqrt(obs.var() / nb + ref.var() / nb + 1e-12)
    out['longest_run'] = (two_sided(zl), zl)
    for m in (2, 4, 6, 8):
        w = np.lib.stride_tricks.sliding_window_view(b[: min(n, 2_000_000)], m)
        code = (w * (1 << np.arange(m))).sum(1)
        c = np.bincount(code, minlength=2 ** m)
        e = len(code) / 2 ** m
        chi = float(((c - e) ** 2 / e).sum())
        out[f'serial_m{m}'] = (chi2_sf(chi, 2 ** m - 1), chi)
    # approximate entropy m=6 (Pincus) with chi2 on 2^m dof
    m = 6
    bb = b[: min(n, 2_000_000)]
    def phi(mm):
        w = np.lib.stride_tricks.sliding_window_view(np.concatenate([bb, bb[:mm - 1]]), mm)
        c = np.bincount((w * (1 << np.arange(mm))).sum(1), minlength=2 ** mm) / len(bb)
        c = c[c > 0]
        return float((c * np.log(c)).sum())
    apen = phi(m) - phi(m + 1)
    chi = 2 * len(bb) * (math.log(2) - apen)
    out['approx_entropy_m6'] = (chi2_sf(chi, 2 ** m), chi)
    cs = np.abs(np.cumsum(s)).max()
    out['cusum'] = (min(1.0, 2 * norm_sf(cs / math.sqrt(n)) * 2), cs / math.sqrt(n))
    return out


# ---------------------------------------------------------------- block G (distribution)
def jump_split(dx, k=6.0):
    mad = np.median(np.abs(dx - np.median(dx))) * 1.4826
    jump = np.abs(dx) > k * mad
    return jump, mad


def distribution_block(dx):
    jump, mad = jump_split(dx)
    idx = np.flatnonzero(jump)
    gaps = np.diff(idx)
    res = {'robust_sd_pips': round(float(mad), 4), 'jump_rate_per_1000': round(jump.mean() * 1000, 4),
           'n_jumps': int(jump.sum())}
    if len(gaps) > 50:
        cv = gaps.std() / gaps.mean()
        se = math.sqrt(1 / len(gaps))                         # CV of an exponential is 1
        res['jump_gap_cv'] = round(float(cv), 4)
        res['jump_gap_memoryless_p'] = two_sided((cv - 1) / se)
    d = dx[~jump]
    sd = d.std()
    vals, cnt = np.unique(d, return_counts=True)
    keep = np.abs(vals) <= 4 * sd
    edges = vals[keep]
    p = np.array([norm_sf((v - 0.5) / sd) - norm_sf((v + 0.5) / sd) for v in edges])
    p = p / p.sum()
    e = p * cnt[keep].sum()
    chi = float(((cnt[keep] - e) ** 2 / np.maximum(e, 1e-9)).sum())
    res['diffusion_vs_rounded_normal_chi2'] = round(chi, 1)
    res['diffusion_vs_rounded_normal_p'] = chi2_sf(chi, len(edges) - 2)
    res['diffusion_sd_pips'] = round(float(sd), 4)
    res['excess_kurtosis_diffusion'] = round(float(((d - d.mean()) ** 4).mean() / sd ** 4 - 3), 4)
    return res, d


# ---------------------------------------------------------------- block H (Bollinger)
def bollinger(level, n=20, k=2.0, horizons=(1, 5, 20, 60), block=5000, seed=0):
    x = level.astype(float)
    cs, cs2 = np.cumsum(np.r_[0, x]), np.cumsum(np.r_[0, x ** 2])
    mean = (cs[n:] - cs[:-n]) / n
    var = (cs2[n:] - cs2[:-n]) / n - mean ** 2
    sd = np.sqrt(np.maximum(var, 0))
    px = x[n - 1:]
    upper, lower = mean + k * sd, mean - k * sd
    out = {}
    for h in horizons:
        fwd = np.full(len(px), np.nan)
        fwd[:-h] = px[h:] - px[:-h]
        above = (px > upper)[:-h] & np.isfinite(fwd[:-h])
        below = (px < lower)[:-h] & np.isfinite(fwd[:-h])
        f = fwd[:-h]
        signal = np.where(above, -f, np.where(below, f, np.nan))    # +ve = moved back toward band
        sig = signal[np.isfinite(signal)]
        pos = np.flatnonzero(np.isfinite(signal))
        # block bootstrap by time block (touches cluster)
        blocks = pos // block
        ub, inv = np.unique(blocks, return_inverse=True)
        sums, cnts = np.bincount(inv, weights=sig), np.bincount(inv)
        rng = np.random.default_rng(seed)
        draws = rng.integers(0, len(ub), (2000, len(ub)))
        bm = sums[draws].sum(1) / cnts[draws].sum(1)
        se = bm.std()
        mean_rev = sig.mean()
        out[h] = {'touches': int(len(sig)), 'mean_reversion_pips': round(float(mean_rev), 4),
                  'z': round(float(mean_rev / se) if se > 0 else 0.0, 2),
                  'p': two_sided(mean_rev / se) if se > 0 else 1.0,
                  'reverts_%': round(float((sig > 0).mean() * 100), 2),
                  'continues_%': round(float((sig < 0).mean() * 100), 2)}
    return out


# ---------------------------------------------------------------- simulation controls
def null_mode(dx_real):
    """Boom/Crash/one-directional drift markets: steps between spikes are one-signed, so a
    Gaussian null is wrong -> shuffle the real steps. Two-sided markets (Volatility, Jump, Step):
    a continuous Gaussian walk + resampled jumps, rounded to the pip grid, reproduces the exact
    pip-rounding bounce that a shuffle would destroy."""
    jump, _ = jump_split(dx_real)
    d = dx_real[~jump]
    nz = d[d != 0]
    share = (nz > 0).mean() if len(nz) else 0.5
    return 'shuffle' if share > 0.9 or share < 0.1 else 'gauss'


def simulate(dx_real, n, seed, planted=False):
    rng = np.random.default_rng(seed)
    mode = null_mode(dx_real)
    if mode == 'shuffle':
        steps = rng.choice(dx_real, n, replace=n > len(dx_real)).astype(float)
    else:
        jump, _ = jump_split(dx_real)
        d = dx_real[~jump]
        sd_cont = math.sqrt(max(d.var() - 1 / 6, 1e-6))
        steps = rng.normal(d.mean(), sd_cont, n)
        j = rng.random(n) < jump.mean()
        if j.any():
            steps[j] += rng.choice(dx_real[jump], j.sum())
    if planted:
        mu = steps.mean()
        e = steps - mu
        sd = e.std()
        z = rng.normal(0, 1, n)
        h = np.empty(n)
        h[0] = 1.0
        for i in range(1, n):
            h[i] = 0.05 + 0.08 * z[i - 1] ** 2 * h[i - 1] + 0.87 * h[i - 1]
        e = e * np.sqrt(h / h.mean())
        for i in range(1, n):
            e[i] += 0.01 * e[i - 1]
        steps = e * (sd / e.std()) + mu
    base = rng.uniform(0, 1) + 1e6
    level = np.rint(np.cumsum(steps) + base).astype(np.int64) if mode == 'gauss' else \
        (np.cumsum(steps) + 1e6).astype(np.int64)
    return np.diff(level), level


# ---------------------------------------------------------------- the battery
def battery(dx, level, label, lz_cap, fast=False):
    r = {}
    stat = {}
    t0 = time.time()
    q, pq, rho = ljung_box(dx.astype(float), 50)
    r['B ljung_box_Q50'] = pq
    stat['B ljung_box_Q50'] = q
    for qv in (2, 4, 16, 64, 256):
        vr, z = variance_ratio(dx.astype(float), qv)
        r[f'B variance_ratio_{qv}'] = two_sided(z)
        stat[f'B variance_ratio_{qv}'] = vr
        r[f'_VR{qv}'] = round(float(vr), 5)
    jump, _ = jump_split(dx)
    d = dx[~jump].astype(float)
    sd_cont = math.sqrt(max(d.var() - 1 / 6, 1e-6))
    exp1 = rounding_lag1_expected(sd_cont)
    obs1 = acf(d, [1])[0]
    r['C rounding_lag1 (obs vs expected)'] = two_sided((obs1 - exp1) * math.sqrt(len(d)))
    stat['C rounding_lag1 (obs vs expected)'] = obs1 - exp1
    r['_lag1_obs'], r['_lag1_expected'] = round(float(obs1), 5), round(float(exp1), 5)
    for name, y in (('abs', np.abs(d)), ('sq', d ** 2)):
        qq, p, rr = ljung_box(y, 20)
        r[f'D {name}_increment_LB20 (vol clustering)'] = p
        stat[f'D {name}_increment_LB20 (vol clustering)'] = qq
        r[f'_{name}_lag1_acf'] = round(float(rr[0]), 5)
    # JD indices set sigma in pips from spot, so drifting spot alone creates |dx| autocorrelation.
    # Standardise by a slow rolling sd (20,000 ticks) and re-test: if the flag disappears, the
    # "clustering" is spot drift, not GARCH-type memory.
    w = 20_000
    if len(d) > 5 * w:
        c1, c2 = np.cumsum(np.r_[0, d]), np.cumsum(np.r_[0, d ** 2])
        rs = np.sqrt(np.maximum((c2[w:] - c2[:-w]) / w - ((c1[w:] - c1[:-w]) / w) ** 2, 1e-9))
        z = d[w:] / rs[:-1]
        qq, p, rr = ljung_box(np.abs(z), 20)
        r['D abs_LB20 after rolling-sd standardisation'] = p
        stat['D abs_LB20 after rolling-sd standardisation'] = qq
    for order in (3, 4, 5):
        c2, p = permutation_test(d[: 3_000_000], order)
        r[f'D ordinal_patterns_order{order}'] = p
        stat[f'D ordinal_patterns_order{order}'] = c2
    if not fast:
        _, z = bds_like(d)
        r['D BDS_like_m3'] = two_sided(z)
    g, p, k = fisher_g(d)
    r['E fisher_g_periodicity'] = p
    stat['E fisher_g_periodicity'] = g
    r['_fisher_peak_period_ticks'] = round(len(d[:2 ** 22]) / k, 1)
    b = (dx[dx != 0] > 0).astype(np.int8)
    for name, (p, st) in nist_signs(b).items():
        r[f'F nist_{name}'] = p
        stat[f'F nist_{name}'] = st
    if level is not None:
        dig = (level % 10).astype(np.int8)
        n = len(dig)
        L = longest_repeat_len(dig[: 4_000_000])
        r['_A_longest_repeat'] = L
        r['_A_expected_repeat'] = round(2 * math.log10(min(n, 4_000_000)), 1)
        cnt = np.bincount(dig, minlength=10)
        r['A digit_marginal'] = chi2_sf(float(((cnt - n / 10) ** 2 / (n / 10)).sum()), 9)
        if not fast and lz_cap:
            ref = np.random.default_rng(0).integers(0, 10, lz_cap)
            r['_A_LZ76_ratio_real_over_iid'] = round(lz76(dig, lz_cap) / lz76(ref, lz_cap), 5)
    r['_seconds'] = round(time.time() - t0, 1)
    r['__stat'] = stat
    return r


def holm(pvals, alpha=0.01):
    items = sorted(pvals.items(), key=lambda kv: kv[1])
    m = len(items)
    flagged, stop = set(), False
    for i, (k, p) in enumerate(items):
        if not stop and p <= alpha / (m - i):
            flagged.add(k)
        else:
            stop = True
    return flagged


def show(title, r, flagged):
    print(f'\n{"=" * 78}\n{title}\n{"=" * 78}')
    for k, v in r.items():
        if k == '__stat':
            continue
        if k.startswith('_'):
            print(f'  {k[1:]:<44} {v}')
        else:
            mark = '  <-- FLAG (Holm 1%)' if k in flagged else ''
            print(f'  {k:<44} p = {v:.3g}{mark}')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--symbol', default='JD100')
    ap.add_argument('--ticks', type=int, default=9_000_000)
    ap.add_argument('--cache', default=None, help='npz with t, p, pip (written automatically after a fetch)')
    ap.add_argument('--lz-cap', type=int, default=100_000, help='LZ76 is O(n^2); 1e5 takes a few minutes')
    ap.add_argument('--control-n', type=int, default=2_000_000, help='length of the planted-structure control')
    ap.add_argument('--null-sims', type=int, default=12, help='same-length simulated nulls used to calibrate p-values (more = steadier tails)')
    ap.add_argument('--out', default=None)
    a = ap.parse_args()

    t, p, pip = load(a.symbol, a.ticks, a.cache)
    level = np.rint(p * 10 ** pip).astype(np.int64)
    interval = int(np.median(np.diff(t)))
    ok = np.diff(t) == interval
    dx = np.diff(level)[ok]
    print(f'null model: {null_mode(dx)}')
    print(f'{a.symbol}: {len(level):,} ticks, {(t[-1] - t[0]) / 86400:.1f} days, interval {interval}s, '
          f'{(~ok).sum()} gaps removed, pip {pip}')

    dist, _ = distribution_block(dx)
    print(f'\n{"=" * 78}\nG  DISTRIBUTION\n{"=" * 78}')
    for k, v in dist.items():
        print(f'  {k:<44} {v}')

    real = battery(dx, level, 'real', a.lz_cap)
    # Rounding to the pip grid, zero steps and jumps break the textbook null of several tests
    # (sign runs, serial bit tests, ordinal patterns). So every statistic is also computed on
    # simulated walks with the SAME rounded step law and the SAME length, and the real value is
    # judged against that simulated null. These calibrated p-values are the ones that count.
    nulls = [battery(simulate(dx, len(dx), seed=100 + i)[0], None, 'null', 0, fast=True)['__stat']
             for i in range(a.null_sims)]
    cal = {}
    for k, v in real['__stat'].items():
        if k.startswith('C '):
            continue                         # rounding check is already model-based
        ref = np.array([nl[k] for nl in nulls if k in nl], dtype=float)
        sd = ref.std(ddof=1) if len(ref) > 2 else 0.0
        cal[k] = two_sided((v - ref.mean()) / sd) if sd > 0 else 1.0
        real[f'_{k} | textbook p'] = f'{real[k]:.3g}'
        real[k] = cal[k]
    pv = {k: v for k, v in real.items() if not k.startswith('_')}
    flagged = holm(pv)
    show(f'REAL SERIES {a.symbol}  ({len(pv)} tests; p calibrated on {a.null_sims} same-law simulations, '
         f'Holm family alpha 0.01)', real, flagged)

    half = len(dx) // 2
    if flagged:
        print(f'\n{"=" * 78}\nI  SPLIT-HALF REPLICATION OF FLAGGED TESTS\n{"=" * 78}')
        hn = [battery(simulate(dx, half, seed=300 + i)[0], None, 'n', 0, fast=True)['__stat'] for i in range(a.null_sims)]
        def calib(r):
            for k, v in r['__stat'].items():
                if k.startswith('C '):
                    continue
                ref = np.array([nl[k] for nl in hn], dtype=float)
                sd = ref.std(ddof=1)
                r[k] = two_sided((v - ref.mean()) / sd) if sd > 0 else 1.0
            return r
        r1 = calib(battery(dx[:half], level[:half + 1], 'h1', 0, fast=True))
        r2 = calib(battery(dx[half:], level[half:], 'h2', 0, fast=True))
        for k in sorted(flagged):
            print(f'  {k:<44} first half p = {r1.get(k, float("nan")):.3g}   second half p = {r2.get(k, float("nan")):.3g}')

    cn = a.control_n
    cnulls = [battery(simulate(dx, cn, seed=200 + i)[0], None, 'n', 0, fast=True)['__stat'] for i in range(a.null_sims)]
    for label, planted in (('NULL CONTROL (fresh same-law random walk) -> expect ~no flags', False),
                           ('PLANTED CONTROL (AR phi=0.01 + GARCH) -> expect flags', True)):
        sdx, slevel = simulate(dx, cn, seed=7, planted=planted)
        rc = battery(sdx, slevel, label, 0, fast=True)
        for k, v in rc['__stat'].items():
            if k.startswith('C '):
                continue
            ref = np.array([nl[k] for nl in cnulls], dtype=float)
            sd = ref.std(ddof=1)
            rc[k] = two_sided((v - ref.mean()) / sd) if sd > 0 else 1.0
        pc = {k: v for k, v in rc.items() if not k.startswith('_')}
        show(label, rc, holm(pc))

    print(f'\n{"=" * 78}\nH  BOLLINGER (20, 2) MEAN-REVERSION CLAIM\n{"=" * 78}')
    print('  mean_reversion_pips > 0 means price moved back toward the band after closing outside it.')
    bands = {'tick level': level}
    minute = level[np.r_[ok, True]][:: max(1, 60 // interval)]
    bands['1-minute bars'] = minute
    for name, lv in bands.items():
        res = bollinger(lv)
        print(f'  -- {name}')
        for h, v in res.items():
            print(f'     h={h:<3} touches {v["touches"]:>9,}  mean {v["mean_reversion_pips"]:>8}  '
                  f'z {v["z"]:>6}  p {v["p"]:.3g}  reverts {v["reverts_%"]}%  continues {v["continues_%"]}%')
    sdx, slevel = simulate(dx, a.control_n, seed=9)
    print('  -- NULL control (simulated random walk, tick level): should show ~0 mean reversion')
    for h, v in bollinger(slevel).items():
        print(f'     h={h:<3} touches {v["touches"]:>9,}  mean {v["mean_reversion_pips"]:>8}  z {v["z"]:>6}  p {v["p"]:.3g}')

    print(f'\n{"=" * 78}\nVERDICT\n{"=" * 78}')
    if not flagged:
        print('  No test survives Holm correction. The increments are indistinguishable from an iid walk')
        print('  by linear, nonlinear, spectral, bit-level and sequence tests, while the planted control')
        print('  shows the battery would have caught small structure. Bollinger touches carry no edge')
        print('  unless block H shows |z| > 3 in BOTH resolutions AND the effect exceeds contract costs.')
    else:
        print('  Flags above. Before believing any: it must replicate in both halves (block I), be absent')
        print('  from the NULL control, and be large enough to pay a contract margin. A rounding-lag1 flag')
        print('  or a vol-clustering flag alone is not a directional edge.')
    if a.out:
        Path(a.out).write_text(repr({'real': real, 'distribution': dist}))


if __name__ == '__main__':
    main()
