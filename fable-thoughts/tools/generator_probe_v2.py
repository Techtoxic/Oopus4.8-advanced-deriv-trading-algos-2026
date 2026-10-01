"""generator_probe_v2.py — randomness / predictability battery for a Deriv synthetic index.

Read-only: public tick history only. No token, no orders.

WHAT THIS CAN AND CANNOT SHOW (read first)
Deriv says its synthetic indices are driven by a cryptographically secure RNG. If that is true, no
statistical battery can predict the next tick: a CSPRNG is by definition indistinguishable from true
randomness by any feasible test. We also never see raw generator output, only prices built from it
(a scaled, rounded, cumulative sum), which hides any algebraic structure even if it existed.
So a clean pass here does NOT prove a CSPRNG, and a single flag does NOT break one. What the battery
CAN do is (1) catch a broken pipeline or feed (replayed history, leaked state, bad rounding),
(2) confirm the published model (Gaussian steps, sigma from the name, zero drift in price), and
(3) measure, in bits, how much the past tells you about the next tick, and compare that with what
a payout needs (blocks K and L). If the answer is "nothing", the only place left for an edge is
PRICING: a payout, barrier or rule that does not match the generator (see PROJECT_BRIEF.md §8).

Blocks
  0  data integrity: duplicate / non-monotone epochs, gaps, off-lattice prices, and REPLAYED
     history (identical 1000-tick paths: the looping-fetcher bug that once faked a chi2 of 661)
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
  J  model fit (Volatility indices): log-return sigma vs the nominal sigma in the name, price drift
     (a martingale has zero mean simple return), log drift vs -sigma^2/2, excess kurtosis
  K  information bound: bias-corrected (Miller-Madow) mutual information between the last k moves /
     digits and the next one, minus a shuffled control, against the bits a payout needs to break even
  L  out-of-sample prediction at the real entry timing (decide on tick T, contract uses T+1): a
     k-order table is fitted on the first 70% and traded on the last 30%; hit rate and EV per trade
     at the payouts you give, with 99% intervals, a shuffled placebo and a planted-signal control

Controls (known-answer): every statistic is also computed on
  - NULL: the real increments randomly shuffled and re-rounded (same step law, no order) -> should pass
  - PLANTED: the same walk with AR(1) phi=0.01 and mild GARCH added -> should be flagged
so you can see the battery has power, not just that the real series "passes".

Multiple testing: all p-values are Holm-corrected at family alpha 0.01. Calibrated p-values use a
Student-t prediction interval over the simulated nulls (a normal z from 12 sims overstates tails).
Volatility indices (R_*, 1HZ*V) use a spot-scaled null (GBM with the measured sigma, rounded to the
pip grid): step size in pips grows with spot, and a constant-sd null would flag that as clustering.

Run (reuses a cache so 9M ticks are fetched once):
  python3 generator_probe_v2.py --symbol R_50 --ticks 10000000
  python3 generator_probe_v2.py --symbol R_50 --cache ../results/R_50_10000000.npz \
          --payout-evenodd 1.923 --payout-risefall 1.953 --json-out ../results/R_50_probe.json
Use EXECUTED payouts from an authenticated account for the two --payout arguments (proposals and the
public feed can differ from what fills); the defaults are only placeholders.
"""
import argparse
import json
import math
import re
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


def calib_p(v, ref):
    """Two-sided p of a real statistic against simulated nulls: Student-t prediction interval with
    len(ref)-1 degrees of freedom (scipy if present, else the normal approximation)."""
    ref = np.asarray(ref, dtype=float)
    n = len(ref)
    sd = ref.std(ddof=1) if n > 2 else 0.0
    if sd <= 0:
        return 1.0
    tv = (v - ref.mean()) / (sd * math.sqrt(1 + 1 / n))
    try:
        from scipy import stats
        return float(2 * stats.t.sf(abs(tv), n - 1))
    except ImportError:
        return two_sided(tv)


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
        ws.close()
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
def bollinger(level, n=20, k=2.0, horizons=(1, 5, 20, 60), block=5000, seed=0, min_blocks=20):
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
        # block bootstrap by time block (touches cluster); the block shrinks on short series (1-minute
        # bars) so there are at least 50 blocks, and with fewer than min_blocks the z is not reported
        blk = max(1, min(block, len(px) // 50))
        blocks = pos // blk
        ub, inv = np.unique(blocks, return_inverse=True)
        sums, cnts = np.bincount(inv, weights=sig), np.bincount(inv)
        rng = np.random.default_rng(seed)
        draws = rng.integers(0, len(ub), (2000, len(ub)))
        bm = sums[draws].sum(1) / cnts[draws].sum(1)
        se = bm.std() if len(ub) >= min_blocks else 0.0
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


GBM_NULL = None      # (start level in pips, per-tick relative sigma) for Volatility indices; set in main


def simulate(dx_real, n, seed, planted=False):
    rng = np.random.default_rng(seed)
    mode = null_mode(dx_real)
    if GBM_NULL is not None:
        # spot-scaled null: a continuous GBM with the measured sigma, so the step in pips grows and
        # shrinks with spot exactly as on the real index; rounded to the pip grid below
        l0, sig = GBM_NULL
        path = l0 * np.exp(np.cumsum(rng.normal(-sig * sig / 2, sig, n)))
        steps = np.diff(np.r_[l0, path])
        mode = 'gauss'
    elif mode == 'shuffle':
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
    base = rng.uniform(0, 1) + (GBM_NULL[0] if GBM_NULL is not None else 1e6)
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


# ---------------------------------------------------------------- block 0 (data integrity)
def integrity_block(t, p, pip, window=1000):
    """Is the data what it claims to be? Replayed history is the failure that matters most: a paging
    bug once returned the same day ~18 times, and every statistic built on it looked significant."""
    t = np.asarray(t, dtype=np.int64)
    n = len(t)
    d = np.diff(t)
    iv = int(np.median(d)) if n > 1 else 0
    x = np.asarray(p, dtype=float) * 10 ** pip
    lv = np.rint(x).astype(np.int64)
    res = {'ticks': n, 'unique_epochs': int(len(np.unique(t))), 'non_increasing_epochs': int((d <= 0).sum()),
           'days': round(float((t[-1] - t[0]) / 86400), 2) if n > 1 else 0.0, 'interval_s': iv,
           'gaps': int((d != iv).sum()), 'largest_gap_s': int(d.max()) if n > 1 else 0,
           'off_lattice_prices': int((np.abs(x - np.rint(x)) > 1e-6).sum())}
    nb = n // window
    if nb > 1:
        shapes = lv[:nb * window].reshape(nb, window)
        shapes = shapes - shapes[:, :1]                      # the path shape, whatever the start level
        _, cnt = np.unique(shapes, axis=0, return_counts=True)
        res['repeated_1000_tick_paths'] = int((cnt > 1).sum())
    bad = (res['unique_epochs'] < n or res['non_increasing_epochs'] or res['off_lattice_prices']
           or res.get('repeated_1000_tick_paths', 0))
    res['verdict'] = 'CHECK DATA: fix this before trusting anything below' if bad else 'OK'
    return res


# ---------------------------------------------------------------- block J (model fit, vol indices)
def vol_nominal_sigma(symbol, interval):
    """Per-tick relative sigma a Volatility index is built with: sigma_ann / sqrt(ticks per year)."""
    m = re.match(r'^(R_|1HZ)(\d+)V?$', symbol)
    if not m:
        return None
    return int(m.group(2)) / 100 / math.sqrt(365 * 86400 / interval)


def model_block(level, ok, sig_nominal):
    lv = level.astype(float)
    r = np.diff(np.log(lv))[ok]
    rounding = np.mean(1 / 6 / lv[:-1][ok] ** 2)          # variance the pip rounding adds to log returns
    sig = math.sqrt(max(r.var() - rounding, 1e-30))
    simple = np.expm1(r)
    t_drift = simple.mean() / (simple.std() / math.sqrt(len(simple)))
    ito = r.mean() + r.var() / 2                             # 0 when the price itself has no drift
    res = {'sigma_rel_measured': float(sig), 'sigma_rel_nominal': sig_nominal,
           'measured_over_nominal': round(sig / sig_nominal, 5) if sig_nominal else None,
           'mean_simple_return_per_tick': float(simple.mean()), 'price_drift_t': round(float(t_drift), 2),
           'price_drift_p': two_sided(t_drift), 'log_drift_plus_half_var': float(ito),
           'excess_kurtosis_log_returns': round(float(((r - r.mean()) ** 4).mean() / r.var() ** 2 - 3), 4),
           'sigma_in_pips_now': round(float(sig * lv[-1]), 2)}
    return res, sig


# ---------------------------------------------------------------- block K (information bound)
def mm_entropy(counts):
    """Shannon entropy in bits with the Miller-Madow bias correction."""
    c = counts[counts > 0].astype(float)
    n = c.sum()
    q = c / n
    return float(-(q * np.log2(q)).sum() + (len(c) - 1) / (2 * n * math.log(2)))


def mi_past_next(sym, k, nsym):
    """Bias-corrected I(last k symbols ; next symbol) in bits."""
    n = len(sym) - k
    if n <= 0:
        return float('nan')
    code = np.zeros(n, dtype=np.int64)
    for j in range(k):
        code = code * nsym + sym[j:j + n]
    nxt = sym[k:k + n].astype(np.int64)
    joint = np.bincount(code * nsym + nxt, minlength=nsym ** (k + 1))
    return (mm_entropy(np.bincount(code, minlength=nsym ** k)) + mm_entropy(np.bincount(nxt, minlength=nsym))
            - mm_entropy(joint))


def breakeven_bits(payout):
    """Bits a binary bet paying `payout` needs: 1 - H(1/payout)."""
    q = 1 / payout
    return 1 + q * math.log2(q) + (1 - q) * math.log2(1 - q)


def info_block(dx, level, payout, seed=5, shuffles=20, min_per_cell=20):
    """MI of the real series against a shuffled null. Only the excess over the null's 99th percentile
    counts (the MI estimator is noisy and biased when the table is sparse), and tables with fewer than
    `min_per_cell` observations per cell are skipped."""
    rng = np.random.default_rng(seed)
    moves = (np.sign(dx) + 1).astype(np.int64)             # 0 down, 1 flat, 2 up
    digits = (level % 10).astype(np.int64)
    rows = []
    for name, sym, nsym, ks in (('moves', moves, 3, (1, 2, 3, 4)), ('digits', digits, 10, (1, 2, 3))):
        for k in ks:
            if len(sym) < min_per_cell * nsym ** (k + 1):
                continue
            real = mi_past_next(sym, k, nsym)
            null = np.array([mi_past_next(rng.permutation(sym), k, nsym) for _ in range(shuffles)])
            q99 = float(np.quantile(null, 0.99))
            rows.append({'series': name, 'k': k, 'mi_bits': real, 'shuffled_bits': float(null.mean()),
                         'shuffled_q99_bits': q99, 'excess_bits': max(0.0, real - q99),
                         'above_null': bool(real > q99)})
    return rows, breakeven_bits(payout)


# ---------------------------------------------------------------- block L (out-of-sample prediction)
def wilson(k, n, z=2.5758):
    if n == 0:
        return float('nan'), float('nan')
    ph = k / n
    den = 1 + z * z / n
    c = (ph + z * z / (2 * n)) / den
    h = z * math.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n)) / den
    return c - h, c + h


def markov_oos(feat, target, k, nfeat, ncls, winmat, payout, train=0.7, gate=True):
    """Fit counts of the target class per context (last k features) on the first `train` share, bet the
    class with the best TRAIN win rate, trade the rest. winmat[pred, actual] = 1 when the bet wins.
    With gate, only contexts whose train win rate beats 1/payout are traded."""
    n = len(target)
    code = np.zeros(n, dtype=np.int64)
    for j in range(k):
        code = code * nfeat + feat[:, j]
    cut = int(n * train)
    tab = np.zeros((nfeat ** k, ncls))
    np.add.at(tab, (code[:cut], target[:cut]), 1)
    tot = tab.sum(1)
    winrate = (tab @ winmat.T) / np.maximum(tot, 1)[:, None]        # [context, bet] train win rate
    best = winrate.argmax(1)
    best_rate = winrate[np.arange(len(tab)), best]
    c = code[cut:]
    use = tot[c] > 0
    if gate:
        use &= best_rate[c] > 1 / payout
    wins = winmat[best[c][use], target[cut:][use]].astype(bool)
    ntr = int(len(wins))
    k_ = int(wins.sum())
    lo, hi = wilson(k_, ntr)
    return {'trades': ntr, 'hit': k_ / ntr if ntr else float('nan'), 'hit_ci99': (lo, hi),
            'ev_per_trade': k_ / ntr * payout - 1 if ntr else float('nan'),
            'ev_ci99': (lo * payout - 1, hi * payout - 1), 'breakeven_hit': 1 / payout}


def prediction_block(dx, level, pay_eo, pay_rf, seed=3, ks=(1, 2, 3)):
    """Real timing: decide after tick T. A 1-tick digit contract settles on the digit of T+1; a 1-tick
    rise/fall enters at T+1 and settles at T+2 (strictly higher / lower; a tie loses both sides).
    `level` must be contiguous with dx (level[i+1] - level[i] == dx[i])."""
    rng = np.random.default_rng(seed)
    mv = (np.sign(dx) + 1).astype(np.int64)                  # 0 down, 1 flat, 2 up; dx[i] = L[i+1] - L[i]
    dg = (level % 10).astype(np.int64)
    eo = np.eye(2)                                           # bet even (0) or odd (1)
    rf = np.array([[1, 0, 0], [0, 0, 0], [0, 0, 1]], float)  # bet down / (never flat) / up
    out = {}
    for k in ks:
        T = np.arange(k - 1, len(dg) - 1)
        feat = np.stack([dg[T - (k - 1 - j)] for j in range(k)], axis=1)
        tgt = dg[T + 1] % 2
        out[f'even_odd k={k}'] = markov_oos(feat, tgt, k, 10, 2, eo, pay_eo, gate=False)
        out[f'even_odd k={k} gated'] = markov_oos(feat, tgt, k, 10, 2, eo, pay_eo)
        out[f'even_odd k={k} PLACEBO'] = markov_oos(feat, rng.permutation(tgt), k, 10, 2, eo, pay_eo)
        T = np.arange(k, len(mv) - 1)
        feat = np.stack([mv[T - k + j] for j in range(k)], axis=1)
        tgt = mv[T + 1]
        out[f'rise_fall k={k}'] = markov_oos(feat, tgt, k, 3, 3, rf, pay_rf, gate=False)
        out[f'rise_fall k={k} gated'] = markov_oos(feat, tgt, k, 3, 3, rf, pay_rf)
        out[f'rise_fall k={k} PLACEBO'] = markov_oos(feat, rng.permutation(tgt), k, 3, 3, rf, pay_rf)
    return out


def planted_persistence(dx, q, seed=4, lag=2):
    """Control with a known signal: with probability q a move copies the sign of the move `lag` ticks
    earlier. lag=2 because a signal at lag 1 cannot be traded: you decide on T and the rise/fall
    contract only starts at T+1, so the move you can bet on is two moves after the last one you saw."""
    rng = np.random.default_rng(seed)
    s = dx.copy()
    for i in np.flatnonzero(rng.random(len(s)) < q):
        if i >= lag and s[i - lag] != 0:
            s[i] = (abs(s[i]) or 1) * np.sign(s[i - lag])
    return s


def show_info(rows, be, title):
    print(f'\n{"=" * 78}\n{title}\n{"=" * 78}')
    print(f'  a bet paying the even/odd payout needs {be:.6f} bits of information to break even')
    for r in rows:
        print(f"  {r['series']:<7} k={r['k']}  MI {r['mi_bits']:.6f}  shuffled mean {r['shuffled_bits']:.6f} "
              f"q99 {r['shuffled_q99_bits']:.6f}  excess over q99 {r['excess_bits']:.6f} bits "
              f"({r['excess_bits'] / be * 100:.1f}% of break-even){'  ABOVE NULL' if r['above_null'] else ''}")


def show_pred(res, title):
    print(f'\n{"=" * 78}\n{title}\n{"=" * 78}')
    for name, v in res.items():
        if not v['trades']:
            print(f'  {name:<26} no trades')
            continue
        print(f"  {name:<26} trades {v['trades']:>9,}  hit {v['hit']:.4f} [{v['hit_ci99'][0]:.4f}, "
              f"{v['hit_ci99'][1]:.4f}]  need {v['breakeven_hit']:.4f}  EV {v['ev_per_trade'] * 100:+.2f}% "
              f"[{v['ev_ci99'][0] * 100:+.2f}%, {v['ev_ci99'][1] * 100:+.2f}%]")


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
    ap.add_argument('--json-out', default=None, help='write every block as JSON (the file to send back)')
    ap.add_argument('--payout-evenodd', type=float, default=1.923,
                    help='EXECUTED 1-tick EVEN/ODD payout on this symbol (placeholder: 1.923)')
    ap.add_argument('--payout-risefall', type=float, default=1.953,
                    help='EXECUTED 1-tick RISE/FALL payout on this symbol (placeholder: 1.953)')
    ap.add_argument('--planted-q', type=float, default=0.1,
                    help='block K/L control: share of moves that copy the sign of the move 2 ticks earlier '
                         '(0.1 gives about a 55%% hit rate, above the payout break-even, so it must be found)')
    a = ap.parse_args()

    t, p, pip = load(a.symbol, a.ticks, a.cache)
    integ = integrity_block(t, p, pip)
    print(f'\n{"=" * 78}\n0  DATA INTEGRITY\n{"=" * 78}')
    for k, v in integ.items():
        print(f'  {k:<44} {v}')
    level = np.rint(p * 10 ** pip).astype(np.int64)
    interval = int(np.median(np.diff(t)))
    ok = np.diff(t) == interval
    dx = np.diff(level)[ok]
    global GBM_NULL
    model = None
    sig_nom = vol_nominal_sigma(a.symbol, interval)
    if sig_nom is not None:
        model, sig_meas = model_block(level, ok, sig_nom)
        print(f'\n{"=" * 78}\nJ  MODEL FIT (published: Gaussian log-returns, sigma from the name, zero price drift)\n{"=" * 78}')
        for k, v in model.items():
            print(f'  {k:<44} {v}')
        GBM_NULL = (float(level[0]), sig_meas)
        print('null model: gbm (spot-scaled, measured sigma, rounded to the pip grid)')
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
        cal[k] = calib_p(v, ref)
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
                ref = np.array([nl[k] for nl in hn if k in nl], dtype=float)
                r[k] = calib_p(v, ref)
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
            ref = np.array([nl[k] for nl in cnulls if k in nl], dtype=float)
            rc[k] = calib_p(v, ref)
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

    # K and L use the full contiguous-in-time series (gaps are rare; a move across a gap counts once)
    dx_all = np.diff(level)
    info_rows, be = info_block(dx_all, level, a.payout_evenodd)
    show_info(info_rows, be, 'K  INFORMATION BOUND (bias-corrected MI of the past about the next tick)')
    pdx, plevel = simulate(dx, min(a.control_n, len(dx)), seed=11)
    pdx = planted_persistence(pdx, a.planted_q)
    plevel = np.r_[plevel[0], plevel[0] + np.cumsum(pdx)]
    prow, _ = info_block(pdx, plevel, a.payout_evenodd)
    show_info(prow, be, f'K  PLANTED CONTROL ({a.planted_q:.0%} of moves copy the move 2 ticks back) '
                        f'-> moves k>=2 must be ABOVE NULL')
    pred = prediction_block(dx_all, level, a.payout_evenodd, a.payout_risefall)
    show_pred(pred, f'L  OUT-OF-SAMPLE PREDICTION (train 70% / trade 30%, decide on T, contract on T+1; '
                    f'payouts even/odd {a.payout_evenodd}, rise/fall {a.payout_risefall})')
    ppred = prediction_block(pdx, plevel, a.payout_evenodd, a.payout_risefall, ks=(1, 2))
    show_pred(ppred, 'L  PLANTED CONTROL -> rise_fall must show a positive EV lower bound (else block L has no power)')
    edge = [k for k, v in pred.items() if 'PLACEBO' not in k and v['trades'] and v['ev_ci99'][0] > 0]
    best_excess = max((r['excess_bits'] for r in info_rows), default=0.0)
    power_k = any(r['above_null'] for r in prow if r['series'] == 'moves' and r['k'] >= 2)
    power_l = any(v['trades'] and v['ev_ci99'][0] > 0 for k, v in ppred.items() if k.startswith('rise_fall') and 'PLACEBO' not in k)

    print(f'\n{"=" * 78}\nVERDICT\n{"=" * 78}')
    if integ['verdict'] != 'OK':
        print('  DATA INTEGRITY FAILED (block 0). Nothing below can be trusted until the data is fixed.')
    if model and model['price_drift_p'] < 0.01:
        print(f"  Model: price drift t = {model['price_drift_t']} (p < 0.01). Check it on fresh data before "
              f"calling it a tilt; a zero-drift index should not show this.")
    print(f'  Information: best MI above the shuffled 99th percentile {best_excess:.6f} bits vs {be:.6f} '
          f'needed for the even/odd payout ({best_excess / be * 100:.1f}%).')
    print(f"  Power: planted signal {'FOUND' if power_k else 'MISSED'} by block K, "
          f"{'FOUND' if power_l else 'MISSED'} by block L"
          f"{'' if power_k and power_l else ' -- a null result above is uninformative until this is fixed (more ticks)'}.")
    one_way = null_mode(dx) == 'shuffle'
    if one_way:
        print('  ONE-DIRECTIONAL INDEX (Boom/Crash): the steps between spikes all move one way by design, so')
        print('  blocks K and L find huge "information". Deriv does not offer 1-tick rise/fall or digit contracts')
        print('  on these symbols (only accumulators and multipliers), so block L EV is hypothetical, not an edge.')
    if edge and not one_way:
        print(f'  Out-of-sample: 99% EV lower bound > 0 for {edge}. Candidate only: it must repeat on data not')
        print('  seen here, at EXECUTED authenticated payouts, before any stake.')
    elif edge:
        print(f'  Out-of-sample: positive EV for {edge}, on contracts that are not offered here.')
    else:
        print('  Out-of-sample: no rule has a 99% EV lower bound above zero at these payouts.')
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
    if a.json_out:
        def clean(o):
            if isinstance(o, dict):
                return {str(k): clean(v) for k, v in o.items()}
            if isinstance(o, (list, tuple)):
                return [clean(v) for v in o]
            if isinstance(o, (np.integer,)):
                return int(o)
            if isinstance(o, (np.floating, float)):
                return float(o) if math.isfinite(o) else None
            return o
        Path(a.json_out).write_text(json.dumps(clean({
            'symbol': a.symbol, 'integrity': integ, 'model': model, 'distribution': dist,
            'real': {k: v for k, v in real.items() if k != '__stat'}, 'flagged': sorted(flagged),
            'information': info_rows, 'breakeven_bits': be, 'prediction': pred,
            'planted_information': prow, 'planted_prediction': ppred,
            'payouts': {'even_odd': a.payout_evenodd, 'rise_fall': a.payout_risefall}}), indent=1))
        print(f'wrote {a.json_out}')


if __name__ == '__main__':
    main()
