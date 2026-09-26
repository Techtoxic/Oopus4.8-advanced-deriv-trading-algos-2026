"""accu_phase_lib.py -- shared pure functions for the ACCU phase-lattice decision tools.

No network, no orders. Used by accu_phase_decide.py and test_accu_phase.py.
Spec: ACCU_PHASE_LATTICE.md section 4 (lead synthesis 2026-09-24, build spec section 4).

KNOCKOUT RULE ("raw_incl", confirmed live on CRASH1000 and by the ticks_stayed_in oracle)
    P        displayed price in integer pips
    w_t      b * P_{t-1}               b = tick_size_barrier exactly as quoted (kept as repr(float))
    K, phase floor(w), w - K
    stay     |P_t - P_{t-1}| < w_t     (a move that touches the barrier knocks out)
Computed in float64. Where |w - round(w)| < 1e-7, K and phase are recomputed exactly from b's
decimal expansion times the integer price. Only contiguous pairs count: the epoch step must equal
the symbol's native interval (1 s for Boom/Crash and 1HZ, 2 s for R_).

FROZEN SMALL-STEP LAW (accu_phase_data/frozen_steplaw.json, pre-registered before any fresh data)
    y = x / (m * P_prev),  m = 1e-3/N  (the measured m_hat when |m_hat*N*1e3 - 1| > 1%, i.e. N-series)
    V1 rect:     x = |D| + U - 1/2  (D = 0 -> U/2)   P(stay|K,phi) = (1-lam) F1(kappa (K+1/2)/(K+phi))
    V2 internal: x = |D + U1 - U2|                    P(stay|K,phi) = (1-lam) mean_f F2(kappa (K+1/2-f)/(K+phi))
    kappa = b/m, f = 16 points in (-1/2, 1/2), lam = 1/N unless the 99% Poisson CI of lam_hat*N excludes 1.
    G = (1+g) P(stay).  G_min = min(V1, V2), each averaged over 32 phases of the band.
"""
import json, math, os, re
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP, ROUND_FLOOR
import numpy as np
from scipy import stats
from scipy.optimize import brentq

from derivfetch import native_interval

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, 'accu_phase_data')

# Every pass/fail threshold of spec section 4.2, and the conventions the criteria rely on.
THRESH = {
    # conventions (spec 4.0 / 4.1)
    'eligible_gmin': 1.0005,            # cell (sym, g, K) eligible if G_min over band_core >= this
    'band_core': (0.05, 0.125),
    'band_ext': (0.0, 0.125),           # D and M use it only if OR-2 cleared the extension
    'boot_ticks': 5000,                 # hour-block bootstrap reps for tick statistics
    'boot_model': 1000,                 # ... for model refits
    'ci_q': (0.005, 0.995),             # percentile 99% intervals
    'min_blocks': 8,                    # fewer hour-blocks -> interval INVALID
    'chi2_min_n': 500,                  # S4 cells used in the chi-square
    'decision_symbols': ('CRASH1000', 'CRASH500'),
    # PASS-A
    'A1_z_min': 3.0, 'A1_ratio': (0.65, 1.35),      # C4 at 4%
    'A2_p_min': 0.01,                               # C5, V1 or V2, each Crash symbol
    'A3_M_lo_min': 1.0,                             # M lower 99%, worse kernel, every tradable cell
    'A4_z_min': -2.576,                             # (D - M) / SE_D
    'A5_C3_max': 0.999, 'A5_C1_max': 0.998,         # placebo and ungated controls
    # PASS-B
    'B_D_lo_min': 1.0, 'B_min_blocks': 16,
    # KILL
    'K1_D_hi_max': 1.0, 'K1_min_inband': 100_000,
    'K2_M_hi_max': 1.0,
    'K3_z_max': 1.0, 'K3_min_ticks': 300_000,
    # oracle outcomes (spec 4.1 oracle)
    'OR1_min_runs': 50, 'HALT_min_runs': 20, 'HALT_match': 0.95, 'OR2_min_events': 3,
    'E0_band': (0.0, 0.05), 'OR3_band': (0.9, 1.0),
    # ladder checks
    'L1_tol': 0.003,
    # ST-H2 kill lines
    'S2_z': 3.0, 'S2_clock_ratio': 1.15, 'S2_clock_min_spikes': 1500,
    # collect
    'short_frac': 0.5,
}

RATES = (0.01, 0.02, 0.03, 0.04, 0.05)
CRASH = ('CRASH50', 'CRASH150N', 'CRASH300N', 'CRASH500', 'CRASH600', 'CRASH900', 'CRASH1000')
BOOM = ('BOOM50', 'BOOM150N', 'BOOM300N', 'BOOM500', 'BOOM600', 'BOOM900', 'BOOM1000')
BC = CRASH + BOOM
VOL = ('R_100', '1HZ100V', '1HZ10V')
# 20-tick take-profit value per $1 stake: (half-up, floor); they differ only at 2% and 3%
PAYOUT20 = {0.01: (1.22, 1.22), 0.02: (1.49, 1.48), 0.03: (1.81, 1.80), 0.04: (2.19, 2.19), 0.05: (2.65, 2.65)}
S3_BANDS = {'[0,.05)': (0.0, 0.05), '[.05,.125)': (0.05, 0.125), '[0,.125)': (0.0, 0.125),
            '[.125,.25)': (0.125, 0.25), '[0,.25)': (0.0, 0.25), '[.75,1)': (0.75, 1.0)}
PROBS = np.linspace(0, 1, 4001)
FS16 = (np.arange(16) + 0.5) / 16 - 0.5
TIE_EPS = 1e-7
Z99 = 2.5758293035489


# ------------------------------------------------------------------ symbols, pips
def family(sym):
    if sym.startswith('CRASH'):
        return 'crash'
    if sym.startswith('BOOM'):
        return 'boom'
    return 'vol'


def sym_N(sym):
    """BOOM300N -> 300, CRASH1000 -> 1000; None for volatility indices."""
    if family(sym) == 'vol':
        return None
    return int(re.search(r'(\d+)', sym).group(1))


def is_nseries(sym):
    return family(sym) != 'vol' and sym.endswith('N')


def to_pips(px, pipdec):
    x = np.asarray(px, float) * 10.0 ** int(pipdec)
    P = np.rint(x).astype(np.int64)
    err = float(np.max(np.abs(x - P))) if len(P) else 0.0
    assert err < 1e-6, f'prices are not on the 1e-{pipdec} lattice (max error {err:.2e})'
    return P


def infer_pipdec(px, maxdec=8):
    """Smallest number of decimals that puts every price on the lattice (use only without pip_size)."""
    px = np.asarray(px, float)
    for d in range(maxdec + 1):
        x = px * 10.0 ** d
        if np.max(np.abs(x - np.rint(x))) < 1e-6:
            return d
    raise ValueError('cannot infer the pip size')


def vol_sigma_pips(sym, spot, pipdec):
    """Per-tick sigma in pips of a volatility index (spec 4.3 vol trigger):
    sigma_ann / sqrt(365*86400/dt) * S/pip, sigma_ann from the name (R_100, 1HZ100V -> 1.0; 1HZ10V -> 0.10),
    dt = 2 s on R_ symbols, 1 s on 1HZ. None for any other symbol."""
    m = re.match(r'(R_|1HZ)(\d+)', sym)
    if not m:
        return None
    dt_s = 2 if m.group(1) == 'R_' else 1
    return int(m.group(2)) / 100 / math.sqrt(365 * 86400 / dt_s) * float(spot) * 10.0 ** int(pipdec)


def spike_mask(D, sym):
    """Spikes are counter-drift steps: down on CRASH, up on BOOM. Everything else is a small step."""
    f = family(sym)
    if f == 'crash':
        return D < 0
    if f == 'boom':
        return D > 0
    return np.zeros(len(D), bool)


# ------------------------------------------------------------------ knockout rule
def b_frac(b):
    """Exact decimal expansion of b as received (repr of the float) -> (num, den) integers."""
    t = Decimal(repr(float(b))).as_tuple()
    num = int(''.join(map(str, t.digits))) * (-1 if t.sign else 1)
    if t.exponent >= 0:
        return num * 10 ** t.exponent, 1
    return num, 10 ** (-t.exponent)


def barrier_levels(Pprev, b):
    """w = b*P_prev (pips), K = floor(w), phase = w - K; exact where w is within 1e-7 of an integer."""
    Pprev = np.asarray(Pprev, np.int64)
    w = float(b) * Pprev.astype(float)
    K = np.floor(w).astype(np.int64)
    ph = w - K
    tie = np.flatnonzero(np.abs(w - np.rint(w)) < TIE_EPS)
    if len(tie):
        num, den = b_frac(b)
        for i in tie:
            q, r = divmod(num * int(Pprev[i]), den)
            K[i] = q
            ph[i] = r / den
    return w, K, ph


def stay_mask(D, K, ph):
    """|D| < K + phase for integer |D|: survive when |D| < K, or |D| == K with a positive phase."""
    a = np.abs(D)
    return (a < K) | ((a == K) & (ph > 0))


def knockout(Pprev, Pnext, b):
    w, K, ph = barrier_levels(Pprev, b)
    D = np.asarray(Pnext, np.int64) - np.asarray(Pprev, np.int64)
    return {'w': w, 'K': K, 'ph': ph, 'stay': stay_mask(D, K, ph)}


# ------------------------------------------------------------------ pre-registered inputs
def load_data_json(name):
    with open(os.path.join(DATA_DIR, name)) as fh:
        return json.load(fh)


def load_frozen():
    L = load_data_json('frozen_steplaw.json')
    return {'V1': np.asarray(L['qgrid_V1_rect'], float), 'V2': np.asarray(L['qgrid_V2_internal'], float),
            'n': int(L['n']), 'per_symbol': L.get('per_symbol', {})}


def load_prereg():
    return load_data_json('prereg.json')


def F(qgrid, t):
    return np.interp(t, qgrid, PROBS)


def pstay(K, ph, kappa, lam, law, kernel):
    """Model P(stay | K, phase) under kernel V1 (rect) or V2 (internal price)."""
    K = np.asarray(K, float)
    ph = np.asarray(ph, float)
    if kernel == 'V1':
        return (1 - lam) * F(law['V1'], kappa * (K + 0.5) / (K + ph))
    thr = kappa * (K[..., None] + 0.5 - FS16) / (K + ph)[..., None]
    return (1 - lam) * F(law['V2'], thr).mean(-1)


def band_phases(band, n=32):
    lo, hi = band
    return lo + (hi - lo) * (np.arange(n) + 0.5) / n


def band_G(g, K, kappa, lam, law, kernel, band):
    ph = band_phases(band)
    return float((1 + g) * pstay(np.full(len(ph), K), ph, kappa, lam, law, kernel).mean())


def g_min(g, K, kappa, lam, law, band=None):
    band = band or THRESH['band_core']
    v1 = band_G(g, K, kappa, lam, law, 'V1', band)
    v2 = band_G(g, K, kappa, lam, law, 'V2', band)
    return min(v1, v2), v1, v2


def poisson_ci(k, q=None):
    q = q or THRESH['ci_q']
    lo = stats.chi2.ppf(q[0], 2 * k) / 2 if k > 0 else 0.0
    hi = stats.chi2.ppf(q[1], 2 * k + 2) / 2
    return float(lo), float(hi)


def model_params(sym, mhat_N1e3=None, n_spikes=None, n_trans=None):
    """m and lambda for the frozen law (spec 4.0): nominal unless the data clearly says otherwise."""
    N = sym_N(sym)
    out = {'N': N, 'm': 1e-3 / N, 'm_source': 'nominal 1e-3/N', 'lam': 1.0 / N, 'lam_source': 'nominal 1/N'}
    if mhat_N1e3 is not None and abs(mhat_N1e3 - 1) > 0.01:
        out.update(m=mhat_N1e3 * 1e-3 / N, m_source='measured (|m_hat*N*1e3 - 1| > 1%)')
    if n_spikes is not None and n_trans:
        lo, hi = poisson_ci(n_spikes)
        if not (lo / n_trans * N <= 1 <= hi / n_trans * N):
            out.update(lam=n_spikes / n_trans, lam_source='measured (99% CI of lam_hat*N excludes 1)')
    return out


# ------------------------------------------------------------------ law A barrier ladder
FN_MU, FN_SG = 0.8536136205180834, 0.8633619303351493
PG = {0.01: .985, 0.02: .977, 0.03: .967, 0.04: .9575, 0.05: .9465}


def law_a_kappa(N, g):
    """kappa_g solving F_Y(kappa) = P_g/(1-1/N), Y ~ |N(0.8536, 0.8634)|; None where infeasible."""
    t = PG[g] / (1 - 1 / N)
    if t >= 1:
        return None
    FY = lambda y: stats.norm.cdf((y - FN_MU) / FN_SG) - stats.norm.cdf((-y - FN_MU) / FN_SG)
    return float(brentq(lambda y: FY(y) - t, 0.01, 20))


def law_a_ratio(N, g, prereg=None):
    """b_g / b_0.04 under law A (pre-registered table when N is in it)."""
    if prereg is not None:
        tab = prereg['ladder_ratio_lawA'].get(str(N))
        if tab is not None and str(g) in tab:
            return tab[str(g)]
    k, k4 = law_a_kappa(N, g), law_a_kappa(N, 0.04)
    return None if (k is None or k4 is None) else k / k4


def gauss_ratio(g):
    z = lambda p: stats.norm.ppf((1 + p) / 2)
    return float(z(PG[g]) / z(PG[0.04]))


def recorded_tol(v):
    """Half a unit in the last recorded significant digit, relative (2.447e-6 -> 2.0e-4)."""
    s = repr(float(v)).lower().split('e')
    mant = s[0].replace('-', '').replace('.', '').lstrip('0')
    exp = int(s[1]) if len(s) > 1 else 0
    digits_after = len(s[0].split('.')[1]) if '.' in s[0] else 0
    unit = 10.0 ** (exp - digits_after)
    return 0.5 * unit / abs(float(v)) if mant else 0.0


def rounding_directions(b, spot, pipdec, distance):
    """Which quantisations of b*spot to 10^-(pip+1) reproduce the quoted barrier_spot_distance."""
    D = Decimal(repr(float(b))) * Decimal(repr(float(spot)))
    q = Decimal(1).scaleb(-(int(pipdec) + 1))
    target = Decimal(str(distance))
    return [name for name, mode in (('ceil', ROUND_CEILING), ('near', ROUND_HALF_UP), ('floor', ROUND_FLOOR))
            if D.quantize(q, rounding=mode) == target]


# ------------------------------------------------------------------ statistics
def wilson(k, n, z=Z99):
    if n == 0:
        return float('nan'), float('nan')
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return c - h, c + h


def boot_matrices(nb, reps, seed, chunk=500):
    """Hour-block resampling as multinomial block counts, in chunks of `chunk` reps."""
    rng = np.random.default_rng(seed)
    done = 0
    while done < reps:
        r = min(chunk, reps - done)
        yield rng.multinomial(nb, np.full(nb, 1.0 / nb), size=r).astype(float)
        done += r


def boot_generic(S, fn, reps, seed=1, q=None):
    """S: per-block sums (nb x k). fn maps resampled column sums (r x k) -> statistic (r,).
    Returns point, percentile 99% CI, SE, block count and the INVALID flag (< 8 blocks)."""
    q = q or THRESH['ci_q']
    S = np.atleast_2d(np.asarray(S, float))
    nb = S.shape[0]
    with np.errstate(divide='ignore', invalid='ignore'):
        point = float(fn(S.sum(0)[None, :])[0]) if nb else float('nan')
    out = {'point': point, 'lo': float('nan'), 'hi': float('nan'), 'se': float('nan'),
           'n_blocks': int(nb), 'valid': bool(nb >= THRESH['min_blocks'])}
    if nb < 2 or not np.isfinite(point):
        out['valid'] = False
        return out
    with np.errstate(divide='ignore', invalid='ignore'):
        vals = np.concatenate([fn(C @ S) for C in boot_matrices(nb, reps, seed)])
    vals = vals[np.isfinite(vals)]
    if len(vals) < 10:
        out['valid'] = False
        return out
    out.update(lo=float(np.quantile(vals, q[0])), hi=float(np.quantile(vals, q[1])), se=float(vals.std(ddof=1)))
    return out


def block_sums(blk, *cols):
    """Per-block sums of each column (blocks = any hashable-int ids), dropping empty blocks."""
    ub, inv = np.unique(blk, return_inverse=True)
    return np.column_stack([np.bincount(inv, weights=np.asarray(c, float), minlength=len(ub)) for c in cols])


def boot_mean(vals, blk, reps, seed=1):
    """Hour-block bootstrap of a mean."""
    if len(vals) == 0:
        return {'point': float('nan'), 'lo': float('nan'), 'hi': float('nan'), 'se': float('nan'),
                'n_blocks': 0, 'valid': False, 'n': 0}
    S = block_sums(blk, vals, np.ones(len(vals)))
    out = boot_generic(S, lambda X: X[:, 0] / X[:, 1], reps, seed)
    out['n'] = int(len(vals))
    return out


# ------------------------------------------------------------------ oracle (port of refute_h2_emp e1/e3)
ORACLE_RULES = ('raw_incl', 'raw_strict', 'ceil_incl', 'ceil_strict', 'near_incl', 'near_strict',
                'floor_incl', 'floor_strict', 'K-1', 'K+1')
QUANT_RULES = ORACLE_RULES[:8]
ORACLE_MAX_LAG = 2                      # feed ticks the house list's in-progress entry may trail by


def rule_breaches(P, b, rules=ORACLE_RULES):
    """Knockout flags for every transition of integer-pip series P under each candidate rule. Exact:
    w = num*P_prev/den; the displayed distance D = b*S_prev is quantised to 1/10 pip (ceil / half-up /
    floor); 'incl' knocks out when |move| >= D, 'strict' when |move| > D; 'K-1'/'K+1' survive iff
    |move| <= K-1 / K+1 with K = floor(w)."""
    num, den = b_frac(b)
    P = [int(x) for x in P]
    n = len(P) - 1
    x = [num * P[i] for i in range(n)]                     # w = x/den
    a = [abs(P[i + 1] - P[i]) for i in range(n)]
    K = [xi // den for xi in x]
    qc = [-((-10 * xi) // den) for xi in x]               # ceil(10 w)
    qn = [(20 * xi + den) // (2 * den) for xi in x]       # half-up(10 w)
    qf = [(10 * xi) // den for xi in x]                   # floor(10 w)
    br = {}
    for r in rules:
        if r == 'raw_incl':
            v = [a[i] * den >= x[i] for i in range(n)]
        elif r == 'raw_strict':
            v = [a[i] * den > x[i] for i in range(n)]
        elif r in ('ceil_incl', 'ceil_strict', 'near_incl', 'near_strict', 'floor_incl', 'floor_strict'):
            q = {'ceil': qc, 'near': qn, 'floor': qf}[r.split('_')[0]]
            v = ([10 * a[i] >= q[i] for i in range(n)] if r.endswith('incl')
                 else [10 * a[i] > q[i] for i in range(n)])
        elif r == 'K-1':
            v = [a[i] > K[i] - 1 for i in range(n)]
        elif r == 'K+1':
            v = [a[i] > K[i] + 1 for i in range(n)]
        else:
            raise ValueError(r)
        br[r] = np.array(v, bool)
    lev = {'K': np.array(K, np.int64), 'd': np.array(a, np.int64),
           'ph': np.array([(x[i] - K[i] * den) / den for i in range(n)]),
           'w': np.array([x[i] / den for i in range(n)]),
           'touch': np.array([10 * a[i] == qc[i] for i in range(n)], bool)}
    return br, lev


def run_lengths(br):
    """Replay a breach sequence with a counter (survival +1; breach -> append, reset). The partial first
    run is dropped (it started before the first tick); the final counter is the run in progress."""
    idx = np.flatnonzero(br)
    if len(idx) == 0:
        return np.zeros(0, np.int64), None, idx
    return np.diff(idx) - 1, int(len(br) - 1 - idx[-1]), idx


def backward_match(runs, in_prog, L, cap):
    """Pre-registered primary comparison: house L[-1] vs the run in progress, then L[-k-1] vs
    completed[-k], counting consecutive matches backward (at most `cap` list entries)."""
    if cap <= 0 or in_prog is None or in_prog != L[-1]:
        return 0
    m = 1
    for k in range(1, cap):
        if k > len(runs) or int(runs[-k]) != int(L[-k - 1]):
            break
        m += 1
    return m


def longest_match(runs, Lx):
    """Longest contiguous exact match between run sequence and list: (length, last run idx, last list idx)."""
    runs = np.asarray(runs)
    Lx = np.asarray(Lx)
    best = (0, -1, -1)
    m = len(Lx)
    prev = np.zeros(m + 1, np.int32)
    for i in range(len(runs)):
        cur = np.zeros(m + 1, np.int32)
        eq = runs[i] == Lx
        cur[1:][eq] = prev[:-1][eq] + 1
        j = int(cur.argmax())
        if cur[j] > best[0]:
            best = (int(cur[j]), i, j - 1)
        prev = cur
    return best


def house_from_end(n, L):
    """Lay the house list backward from the snapshot's last transition. Returns the house verdict per
    transition (-1 unknown, 0 survive, 1 breach) and how many list entries lie wholly in the feed."""
    house = np.full(n, -1, np.int8)
    end, covered = n, 0
    for r in reversed(L):
        start = end - int(r)
        if start - 1 < 0:
            break                              # the breach that started this run predates the feed
        house[start:end] = 0
        house[start - 1] = 1
        covered += 1
        end = start - 1
    return house, covered


def _verdict(x):
    return 'breach' if x else 'survive'


def oracle_align(ep, P, b, L, last_epoch, interval=None, window=30000, eps_sweep=True, n_shuffle=200,
                 seed=7, rules=ORACLE_RULES):
    """Align candidate knockout rules against the house's ticks_stayed_in list (oldest first, last entry
    = run in progress at last_tick_epoch). Mode 'primary' when the feed reaches last_tick_epoch (the
    pre-registered backward comparison); mode 'sub' otherwise (longest contiguous exact match)."""
    ep = np.asarray(ep, np.int64)
    P = np.asarray(P, np.int64)
    L = [int(v) for v in L]
    keep = ep <= int(last_epoch)
    ep, P = ep[keep], P[keep]
    res = {'b': float(b), 'b_repr': repr(float(b)), 'list_len': len(L), 'last_tick_epoch': int(last_epoch)}
    if len(ep) < 3 or len(L) < 2:
        res.update(mode='none', error='feed does not reach back to the snapshot or list too short')
        return res
    iv = interval or native_interval(ep)
    gaps = np.flatnonzero(np.diff(ep) != iv)
    s0 = int(gaps[-1]) + 1 if len(gaps) else 0          # contiguous tail segment only
    s0 = max(s0, len(ep) - window)
    ep, P = ep[s0:], P[s0:]
    n = len(P) - 1
    mode = 'primary' if int(ep[-1]) == int(last_epoch) else 'sub'
    lag = 0
    if mode == 'primary' and len(P) > ORACLE_MAX_LAG + 3:
        # The list's in-progress entry can trail last_tick_epoch by a tick or two (the snapshot is read
        # between feed and contract updates). A one-tick offset zeroes every rule at once, so pick the lag
        # (0..ORACLE_MAX_LAG) that the best rule aligns at; all rules and controls are scored at that lag.
        best = (-1, 0)
        for lg in range(ORACLE_MAX_LAG + 1):
            Pl = P[:len(P) - lg]
            hl, cov = house_from_end(len(Pl) - 1, L)
            brl, _ = rule_breaches(Pl, b, rules)
            m = max(backward_match(*run_lengths(brl[r])[:2], L, cov) for r in rules)
            if m > best[0]:
                best = (m, lg)
        lag = best[1]
        if lag:
            ep, P = ep[:len(ep) - lag], P[:len(P) - lag]
            n = len(P) - 1
    res.update(mode=mode, interval=int(iv), n_ticks=int(len(P)), feed_start=int(ep[0]), feed_end=int(ep[-1]),
               feed_short_of_snapshot_s=int(last_epoch - ep[-1]), lag=lag)
    if n < 2:
        res['error'] = 'too few contiguous ticks'
        return res
    br, lev = rule_breaches(P, b, rules)
    per = {}
    if mode == 'primary':
        house, covered = house_from_end(n, L)
        known = house >= 0
        for r in rules:
            runs, inprog, _ = run_lengths(br[r])
            m = backward_match(runs, inprog, L, covered)
            per[r] = {'matched': m, 'covered': covered, 'full': bool(covered > 0 and m == covered),
                      'frac': m / covered if covered else 0.0, 'n_breaches': int(br[r].sum()),
                      'in_progress': inprog, 'longest': longest_match(runs, L[:-1])[0],
                      'transition_disagreements_vs_house': int((br[r][known] != (house[known] == 1)).sum())}
        anchor = 'snapshot end'
    else:
        best = None
        for r in rules:
            runs, _, idx = run_lengths(br[r])
            k, ri, li = longest_match(runs, L[:-1])
            cov = min(len(runs), len(L) - 1)
            per[r] = {'matched': k, 'covered': cov, 'full': bool(cov > 0 and k == cov),
                      'frac': k / cov if cov else 0.0, 'n_breaches': int(br[r].sum()), 'longest': k,
                      'list_pos': (li - k + 1) if k else None}
            if best is None or k > best[0]:
                best = (k, r, ri, idx)
        k, anchor, ri, idx = best
        house = np.full(n, -1, np.int8)
        if k > 0:                                # house verdicts inside the stretch the best rule matched
            j0, j1 = int(idx[ri - k + 1]), int(idx[ri + 1])
            house[j0:j1 + 1] = br[anchor][j0:j1 + 1]
        known = house >= 0
    res['covered'] = per['raw_incl']['covered'] if 'raw_incl' in per else None
    res['anchor'] = anchor
    res['rules'] = per
    res['house_known_transitions'] = int(known.sum())

    # every transition in the house-known span where the quantisation rules (or the house) disagree
    qr = [r for r in QUANT_RULES if r in br]
    Q = np.vstack([br[r] for r in qr])
    dis = known & ((Q.any(0) != Q.all(0)) | (br[qr[0]] != (house == 1)))
    rows = []
    for i in np.flatnonzero(dis):
        row = {'epoch': int(ep[i + 1]), 'w': round(float(lev['w'][i]), 6), 'phase': round(float(lev['ph'][i]), 6),
               'K': int(lev['K'][i]), 'abs_move': int(lev['d'][i]), 'exact_touch': bool(lev['touch'][i]),
               'house': _verdict(house[i] == 1)}
        row.update({r: _verdict(br[r][i]) for r in rules})
        rows.append(row)
    res['disagreements'] = rows

    # OR-2 / OR-3 events inside the house-known span
    def events(mask):
        return [{'epoch': int(ep[i + 1]), 'w': round(float(lev['w'][i]), 6), 'phase': round(float(lev['ph'][i]), 6),
                 'abs_move': int(lev['d'][i]), 'house': _verdict(house[i] == 1)} for i in np.flatnonzero(mask)]
    ph, d, K = lev['ph'], lev['d'], lev['K']
    e0lo, e0hi = THRESH['E0_band']
    o3lo, o3hi = THRESH['OR3_band']
    res['E0_events'] = events(known & (ph > e0lo) & (ph < e0hi) & (d == K))
    res['OR3_events'] = events(known & (ph >= o3lo) & (ph < o3hi) & (d == K + 1))

    # shuffled-list null (longest contiguous match, as in e3)
    rng = np.random.default_rng(seed)
    runs0, inprog0, _ = run_lengths(br['raw_incl'])
    null = [longest_match(runs0, rng.permutation(L))[0] for _ in range(n_shuffle)]
    res['shuffle_null'] = {'n_perm': n_shuffle, 'mean_longest': float(np.mean(null)) if null else None,
                           'max_longest': int(max(null)) if null else None,
                           'real_longest_raw_incl': int(longest_match(runs0, L)[0])}

    # eps-sweep of raw_incl with b(1+eps): the set of effective barriers that reproduce every covered run
    if eps_sweep:
        a = lev['d']
        Pf = P[:-1].astype(float)
        target = per['raw_incl']['covered'] if mode == 'primary' else per['raw_incl']['matched']
        good = []
        for e in np.round(np.arange(-200, 201) * 1e-4, 6):
            brx = a >= (float(b) * (1 + e)) * Pf
            rx, ipx, _ = run_lengths(brx)
            m = (backward_match(rx, ipx, L, target) if mode == 'primary' else longest_match(rx, L[:-1])[0])
            if target > 0 and m >= target:
                good.append(float(e))
        res['eps_sweep'] = {'grid': '-2%..+2% step 0.01%', 'target_runs': target, 'n_ok': len(good),
                            'eps_min': min(good) if good else None, 'eps_max': max(good) if good else None,
                            'contiguous': bool(good and len(good) == round((max(good) - min(good)) / 1e-4) + 1)}
    return res


def oracle_outcomes(records):
    """OR-1 / HALT / OR-2 / OR-3 over alignment records [{'sym', 'g', 'snapshot', **oracle_align}].
    Only 'primary' records count: in 'sub' mode (feed ends before last_tick_epoch) 'covered' includes list
    entries the feed cannot contain, and the house-known span comes from a best-rule match, not the house."""
    T = THRESH
    out = {'OR1': {}, 'HALT': [], 'OR2': {}, 'OR3': {}}
    for sym in T['decision_symbols']:
        recs = [r for r in records if r.get('sym') == sym and r.get('rules') and r.get('mode') == 'primary'
                and (r.get('covered') or 0) >= T['OR1_min_runs']]
        fails = [f"{r.get('snapshot')} g={r.get('g')}" for r in recs if not r['rules']['raw_incl']['full']]
        status = 'NOT_EVALUATED' if not recs else ('FAIL' if fails else 'PASS')
        out['OR1'][sym] = {'status': status, 'qualifying_snapshots': len(recs), 'failures': fails}
    st = [v['status'] for v in out['OR1'].values()]
    out['OR1']['status'] = 'FAIL' if 'FAIL' in st else ('PASS' if st and all(s == 'PASS' for s in st) else 'NOT_EVALUATED')
    for r in records:
        if family(r.get('sym', '')) == 'vol' or not r.get('rules') or r.get('mode') != 'primary':
            continue
        if (r.get('covered') or 0) >= T['HALT_min_runs']:
            best = max(v['frac'] for v in r['rules'].values())
            if best < T['HALT_match']:
                out['HALT'].append(f"{r['sym']} g={r['g']} {r.get('snapshot')}: best rule matches {best:.0%}")
    e0, o3 = {}, {}
    for r in records:
        # house verdicts are only trustworthy where some rule reproduced every covered run
        if family(r.get('sym', '')) == 'vol' or r.get('mode') != 'primary' or not r.get('rules') \
                or not any(v['full'] for v in r['rules'].values()):
            continue
        for ev in r.get('E0_events', []):
            e0[(r['sym'], r['g'], ev['epoch'])] = ev['house']
        for ev in r.get('OR3_events', []):
            o3[(r['sym'], r['g'], ev['epoch'])] = ev['house']
    nb = sum(v == 'breach' for v in e0.values())
    if nb:
        dec, band = 'DEATH_BAND', 'core'
    elif len(e0) >= T['OR2_min_events']:
        dec, band = 'EXTEND', 'ext'
    else:
        dec, band = 'KEEP', 'core'
    out['OR2'] = {'n_events': len(e0), 'n_breach': nb, 'decision': dec, 'band': band,
                  'events': [{'sym': k[0], 'g': k[1], 'epoch': k[2], 'house': v} for k, v in sorted(e0.items())]}
    nb3 = sum(v != 'breach' for v in o3.values())
    out['OR3'] = {'n_events': len(o3), 'n_survive': nb3,
                  'status': 'NO_EVENTS' if not o3 else ('PASS' if nb3 == 0 else 'FAIL')}
    return out


# ------------------------------------------------------------------ series preparation
def prep_series(sym, ep, px, pipdec, t0=None, t1=None):
    ep = np.asarray(ep, np.int64)
    px = np.asarray(px, float)
    o = np.argsort(ep, kind='stable')
    ep, px = ep[o], px[o]
    u = np.concatenate([[True], np.diff(ep) > 0])
    ep, px = ep[u], px[u]
    if t0 is not None:
        ep, px = ep[ep >= t0], px[ep >= t0]
    if t1 is not None:
        ep, px = ep[ep <= t1], px[ep <= t1]
    P = to_pips(px, pipdec)
    iv = native_interval(ep) if len(ep) > 1 else 1
    ok = np.diff(ep) == iv
    D = np.diff(P)
    sp = spike_mask(D, sym)
    return {'sym': sym, 'ep': ep, 'P': P, 'pipdec': int(pipdec), 'interval': int(iv), 'ok': ok, 'D': D,
            'Pp': P[:-1], 'blk': ep[:-1] // 3600, 'spike': sp & ok, 'small': (~sp) & ok, 'n_ok': int(ok.sum())}


def sub_bins(K, ph, nsub=1024):
    """Group (K, phase) into phase sub-bins for fast model evaluation; returns (inverse, Kc, phc)."""
    K0 = int(K.min()) if len(K) else 0
    key = (K - K0) * nsub + np.minimum((ph * nsub).astype(np.int64), nsub - 1)
    uk, inv = np.unique(key, return_inverse=True)
    return inv, uk // nsub + K0, (uk % nsub + 0.5) / nsub


def model_per_tick(K, ph, kappa, lam, law, kernel):
    if len(K) == 0:
        return np.zeros(0)
    inv, Kc, phc = sub_bins(K, ph)
    return pstay(Kc, phc, kappa, lam, law, kernel)[inv]


def jitter_y(a, Pp, m, rng):
    """Small steps (|D| in pips at P_prev) -> y-values under V1 (rect) and V2 (internal) kernels."""
    a = a.astype(float)
    u = rng.random(len(a))
    x1 = np.where(a == 0, 0.5 * u, a + u - 0.5)
    x2 = np.abs(a + rng.random(len(a)) - rng.random(len(a)))
    s = m * Pp.astype(float)
    return x1 / s, x2 / s


def fit_law(y1, y2):
    return {'V1': np.quantile(y1, PROBS), 'V2': np.quantile(y2, PROBS), 'n': int(len(y1))}


# ------------------------------------------------------------------ S1, S2
def step_law(S, law, reps, seed):
    sym = S['sym']
    N = sym_N(sym)
    sm = S['small']
    a = np.abs(S['D'][sm]).astype(float)
    Pp = S['Pp'][sm].astype(float)
    r = a / Pp
    mh = boot_generic(block_sums(S['blk'][sm], r * N * 1e3, np.ones(len(r))), lambda X: X[:, 0] / X[:, 1], reps, seed)
    k, n = int(S['spike'].sum()), S['n_ok']
    lo, hi = poisson_ci(k)
    mp = model_params(sym, mh['point'], k, n)
    y1, _ = jitter_y(a, Pp, mp['m'], np.random.default_rng(seed))
    ks = stats.kstest(y1, lambda t: F(law['V1'], t)) if len(y1) else None
    yy = r / r.mean() if len(r) else r
    pip = 10.0 ** -S['pipdec']
    return {'n_small': int(len(a)), 'n_spikes': k, 'n_contiguous': n,
            'mhat_N_1e3': mh, 'cv': float(yy.std()) if len(yy) else None, 'cv_law': 0.689,
            'zero_step_frac': float(np.mean(a == 0)) if len(a) else None,
            'lam_hat_N': k / n * N if n else None, 'lam_hat_N_ci99': [lo / n * N, hi / n * N] if n else None,
            'ks_V1_frozen': {'D': float(ks.statistic), 'p': float(ks.pvalue)} if ks else None,
            'model': mp, 'spot_last': float(S['P'][-1] * pip),
            'u_now_pips': float(mh['point'] * 1e-3 / N * S['P'][-1]) if np.isfinite(mh['point']) else None}


def spike_timing(S, reps, seed):
    """S2: ST-H2 ride-along. (b) renewal: spike-gap CV vs a geometric band and 20-tick spike-free
    survival by age; (a) clock: spike share in UTC seconds [50, 60) vs exposure, per UTC day."""
    T = THRESH
    N = sym_N(S['sym'])
    ok, sp = S['ok'], S['spike']
    n = len(ok)
    idx = np.arange(n)
    seg = np.concatenate([[0], np.cumsum(~ok)[:-1]])
    sidx = np.flatnonzero(sp)
    same = seg[sidx[1:]] == seg[sidx[:-1]] if len(sidx) > 1 else np.zeros(0, bool)
    gaps = np.diff(sidx)[same] if len(sidx) > 1 else np.zeros(0)
    lam = len(sidx) / max(S['n_ok'], 1)
    out = {'n_spikes': int(len(sidx)), 'n_gaps': int(len(gaps))}
    rng = np.random.default_rng(seed)
    if len(gaps) >= 20 and lam > 0:
        cv = float(gaps.std() / gaps.mean())
        sims = rng.geometric(lam, size=(2000, len(gaps))).astype(float)
        scv = sims.std(1) / sims.mean(1)
        band = [float(np.quantile(scv, 0.005)), float(np.quantile(scv, 0.995))]
        out.update(gap_cv=cv, gap_cv_band99=band, gap_cv_inside=bool(band[0] <= cv <= band[1]))
    # ages and 20-tick spike-free outcome
    last = np.maximum.accumulate(np.where(sp, idx, -1))
    lb = np.concatenate([[-1], last[:-1]])
    valid_age = ok & (lb >= 0)
    valid_age[valid_age] &= seg[lb[valid_age]] == seg[valid_age]
    age = idx - lb
    csp = np.concatenate([[0], np.cumsum(sp)])
    cbad = np.concatenate([[0], np.cumsum(~ok)])
    h = 20
    fut = np.zeros(n, bool)
    fut_ok = np.zeros(n, bool)
    if n > h:
        fut_ok[:n - h] = (cbad[h:n] - cbad[:n - h]) == 0
        fut[:n - h] = (csp[h:n] - csp[:n - h]) == 0
    expect = (1 - lam) ** h
    rows = []
    edges = [0, .25, .5, 1, 2, np.inf]
    for lo_, hi_ in zip(edges[:-1], edges[1:]):
        m = valid_age & fut_ok & (age >= lo_ * N) & (age < hi_ * N)
        if m.sum() < 200:
            continue
        bm = boot_mean(fut[m].astype(float), S['blk'][m], reps, seed)
        z = (bm['point'] - expect) / bm['se'] if bm['se'] and bm['se'] > 0 else float('nan')
        rows.append({'age_N': [lo_, hi_ if np.isfinite(hi_) else None], 'n': int(m.sum()),
                     'p_free20': bm['point'], 'expected': expect, 'z': float(z), 'n_blocks': bm['n_blocks']})
    out['age_bins'] = rows
    zs = [abs(r['z']) for r in rows if np.isfinite(r['z'])]
    if 'gap_cv' in out:
        out['renewal_killed'] = bool(out['gap_cv_inside'] and all(z < T['S2_z'] for z in zs))
    # clock
    sec = S['ep'][1:] % 60
    day = S['ep'][1:] // 86400
    win = sec >= 50
    p0 = float(win[ok].mean()) if ok.any() else float('nan')
    k = int((sp & win).sum())
    ns = int(sp.sum())
    days = []
    for d in np.unique(day[sp]):
        md = day == d
        nd, kd = int((sp & md).sum()), int((sp & md & win).sum())
        e = float(win[ok & md].mean())
        days.append({'day_utc': int(d), 'n_spikes': nd, 'share': kd / nd if nd else None, 'exposure': e,
                     'p_one_sided': float(stats.binomtest(kd, nd, e, 'greater').pvalue) if nd else None})
    out['clock'] = {'n_spikes': ns, 'share_50_60': k / ns if ns else None, 'exposure': p0,
                    'ratio': (k / ns) / p0 if ns and p0 else None,
                    'p_one_sided': float(stats.binomtest(k, ns, p0, 'greater').pvalue) if ns else None, 'days': days}
    return out


# ------------------------------------------------------------------ S3, S4
def survival_map(lv, ok, g, blk, law=None, kappa=None, lam=None, reps=2000, seed=3):
    K, ph, st = lv['K'][ok], lv['ph'][ok], lv['stay'][ok]
    b_ = blk[ok]
    cells = []
    if len(K) == 0:
        return {'cells': cells, 'bands': {}}
    e = np.minimum((ph * 8).astype(np.int64), 7)
    K0 = int(K.min())
    key = (K - K0) * 8 + e
    nk = np.bincount(key)
    sk = np.bincount(key, st)
    mods = {}
    if law is not None:
        for kern in ('V1', 'V2'):
            mods[kern] = np.bincount(key, model_per_tick(K, ph, kappa, lam, law, kern), minlength=len(nk))
    for j in np.flatnonzero(nk):
        n = int(nk[j])
        p = sk[j] / n
        lo, hi = wilson(int(sk[j]), n)
        c = {'K': int(j // 8 + K0), 'eighth': int(j % 8), 'n': n, 'P': float(p), 'G': (1 + g) * p,
             'G_lo': (1 + g) * lo, 'G_hi': (1 + g) * hi}
        for kern, v in mods.items():
            c['G_' + kern] = float((1 + g) * v[j] / n)
        cells.append(c)
    bands = {}
    for name, (lo_, hi_) in S3_BANDS.items():
        m = (ph >= lo_) & (ph < hi_)
        n = int(m.sum())
        if n == 0:
            continue
        k = int(st[m].sum())
        wl, wh = wilson(k, n)
        bb = boot_mean(st[m] * (1 + g), b_[m], reps, seed)
        bands[name] = {'n': n, 'P': k / n, 'G': (1 + g) * k / n, 'G_wilson99': [(1 + g) * wl, (1 + g) * wh],
                       'G_boot99': [bb['lo'], bb['hi']], 'n_blocks': bb['n_blocks'], 'valid': bb['valid'],
                       'K_levels': sorted(set(int(x) for x in np.unique(K[m])))}
    return {'cells': cells, 'bands': bands}


def chi2_cells(K, ph, st, p, n_fit, min_n):
    """Chi-square of observed stays against model probabilities over (K, phase eighth) cells with
    n >= min_n. Variance adds the model's own sampling error (fit on n_fit small steps)."""
    if len(K) == 0:
        return 0.0, 0
    e = np.minimum((ph * 8).astype(np.int64), 7)
    key = (K - int(K.min())) * 8 + e
    n = np.bincount(key)
    o = np.bincount(key, st)
    x = np.bincount(key, p)
    v = np.bincount(key, p * (1 - p))
    use = n >= min_n
    pb = np.where(n > 0, x / np.maximum(n, 1), 0)
    var = v + n ** 2 * pb * (1 - pb) / max(n_fit, 1)
    chi = float(((o - x) ** 2 / np.maximum(var, 1e-12))[use].sum())
    return chi, int(use.sum())


def model_check(S, lvs, mp, law, seed):
    """S4: frozen-law and cross-fitted (even hours -> odd hours and back) chi-square per growth rate.
    C5 (per kernel) = Bonferroni over rates of the cross-fit p-values."""
    T = THRESH
    ok = S['ok']
    hour_even = (S['blk'] % 2) == 0
    sm = S['small']
    a = np.abs(S['D']).astype(float)
    rng = np.random.default_rng(seed)
    y1, y2 = jitter_y(a[sm], S['Pp'][sm], mp['m'], rng)
    sm_even = hour_even[sm]
    fits = {}
    for half, fm in (('even', sm_even), ('odd', ~sm_even)):
        if not fm.any():                        # all data in hours of one parity (under ~1 h of ticks)
            fits[half] = None
            continue
        mfit = ok & (hour_even if half == 'even' else ~hour_even)
        lam_fit = S['spike'][mfit].sum() / max(mfit.sum(), 1)
        fits[half] = (fit_law(y1[fm], y2[fm]), lam_fit)
    per_g = {}
    for g, lv in lvs.items():
        kappa = lv['b'] / mp['m']
        K, ph, st = lv['K'], lv['ph'], lv['stay']
        row = {}
        for kern in ('V1', 'V2'):
            p = model_per_tick(K[ok], ph[ok], kappa, mp['lam'], law, kern)
            chi, dof = chi2_cells(K[ok], ph[ok], st[ok], p, law['n'], T['chi2_min_n'])
            row['frozen_' + kern] = {'chi2': chi, 'dof': dof, 'p': float(stats.chi2.sf(chi, dof)) if dof else None}
            chi_t, dof_t = 0.0, 0
            for test_half, fit_half in (('odd', 'even'), ('even', 'odd')):
                if fits[fit_half] is None:
                    continue
                mt = ok & (~hour_even if test_half == 'odd' else hour_even)
                lawf, lamf = fits[fit_half]
                p = model_per_tick(K[mt], ph[mt], kappa, lamf, lawf, kern)
                c, d = chi2_cells(K[mt], ph[mt], st[mt], p, lawf['n'], T['chi2_min_n'])
                chi_t += c
                dof_t += d
            row['crossfit_' + kern] = {'chi2': chi_t, 'dof': dof_t,
                                       'p': float(stats.chi2.sf(chi_t, dof_t)) if dof_t else None}
        per_g[str(g)] = row
    c5 = {}
    for kern in ('V1', 'V2'):
        ps = [r['crossfit_' + kern]['p'] for r in per_g.values() if r['crossfit_' + kern]['p'] is not None]
        c5['p_' + kern] = min(1.0, len(ps) * min(ps)) if ps else None
    c5['n_rates'] = len(per_g)
    return {'per_rate': per_g, 'C5': c5}


# ------------------------------------------------------------------ eligibility, D, M
def eligibility(bs, kranges, mp, law):
    """cells[g][K] = G_min over band_core (frozen law, quoted b) and the eligibility flag."""
    out = {}
    for g, b in bs.items():
        kappa = b / mp['m']
        cells = {}
        for K in kranges[g]:
            if K < 1:
                continue
            gm, v1, v2 = g_min(g, K, kappa, mp['lam'], law)
            cells[int(K)] = {'gmin': gm, 'V1': v1, 'V2': v2, 'eligible': bool(gm >= THRESH['eligible_gmin'])}
        out[g] = cells
    return out


def choice_scores(lvs, elig, band, sel=None):
    """Per growth rate, the G_min of the transition's cell when it is eligible and its phase is in
    `band` (else -inf). Rows follow sorted(lvs). `sel` restricts to a subset of transitions."""
    gs = sorted(lvs)
    n = len(lvs[gs[0]]['K']) if sel is None else int(np.sum(sel)) if sel.dtype == bool else len(sel)
    score = np.full((len(gs), n), -np.inf)
    for i, g in enumerate(gs):
        K = lvs[g]['K'] if sel is None else lvs[g]['K'][sel]
        ph = lvs[g]['ph'] if sel is None else lvs[g]['ph'][sel]
        cells = elig.get(g, {})
        if not cells:
            continue
        k0, k1 = min(cells), max(cells)
        gm = np.full(k1 - k0 + 1, -np.inf)
        for K_, c in cells.items():
            if c['eligible']:
                gm[K_ - k0] = c['gmin']
        j = K - k0
        inr = (j >= 0) & (j <= k1 - k0) & (ph >= band[0]) & (ph < band[1])
        score[i, inr] = gm[j[inr]]
    return gs, score


def pick(gs, score):
    best = score.argmax(0)
    inb = np.isfinite(score.max(0)) if score.size else np.zeros(0, bool)
    return best, inb


def ecdf_blocks(y, yblk, nb, grid):
    """cum[b, j] = number of y in block b with y <= grid[j]; n[b] = count in block b."""
    nG = len(grid)
    k = np.searchsorted(grid, y, side='left')
    H = np.bincount(yblk * (nG + 1) + k, minlength=nb * (nG + 1)).reshape(nb, nG + 1).astype(float)
    return np.cumsum(H, axis=1)[:, :nG], H.sum(1)


def interp_weights(thr, wgt, grid, tblk=None, nb=None):
    """Spread each threshold's weight onto its two neighbouring grid points (linear interpolation of the
    ECDF). Per-block matrix when tblk is given, else one vector."""
    nG = len(grid)
    j = np.clip(np.searchsorted(grid, thr, side='right') - 1, 0, nG - 2)
    a = np.clip((thr - grid[j]) / (grid[j + 1] - grid[j]), 0, 1)
    if tblk is None:
        return (np.bincount(j, wgt * (1 - a), minlength=nG) + np.bincount(j + 1, wgt * a, minlength=nG))
    base = tblk * nG
    W = (np.bincount(base + j, wgt * (1 - a), minlength=nb * nG) +
         np.bincount(base + j + 1, wgt * a, minlength=nb * nG))
    return W.reshape(nb, nG)


def thresholds(K, ph, kappa, kernel):
    """Model thresholds in y units (kappa may be per tick); V2 returns 16 per tick, weight 1/16."""
    K = np.asarray(K, float)
    ph = np.asarray(ph, float)
    kappa = np.asarray(kappa, float)
    if kernel == 'V1':
        return kappa * (K + 0.5) / (K + ph), 1.0
    kap = kappa[:, None] if kappa.ndim else kappa
    return (kap * (K[:, None] + 0.5 - FS16) / (K + ph)[:, None]).ravel(), 1.0 / 16


def model_M(symdata, cells, reps, seed, nG=2001):
    """M: refit V1/V2 on the fresh small steps per symbol and evaluate at the in-band ticks (pooled) and
    at each tradable cell's band (32 phases); hour-block bootstrap resamples steps, spikes and in-band
    ticks together. symdata[s]: y1, y2, yblk, spk_b, trans_b, nb, inband dict(K, ph, kappa, wgt, blk).
    cells: [(s, g, K, kappa, band)]."""
    out = {}
    syms = list(symdata)
    offs = np.cumsum([0] + [symdata[s]['nb'] for s in syms])
    nbt = int(offs[-1])
    for kern in ('V1', 'V2'):
        prep = {}
        for s in syms:
            d = symdata[s]
            ib = d['inband']
            t_in, u = thresholds(ib['K'], ib['ph'], ib['kappa'], kern)
            w_in = np.repeat(ib['wgt'], 1 if kern == 'V1' else 16) * u
            b_in = np.repeat(ib['blk'], 1 if kern == 'V1' else 16)
            ctr = []
            for (cs, g, K, kappa, band) in cells:
                if cs != s:
                    continue
                phs = band_phases(band)
                t, uu = thresholds(np.full(len(phs), K), phs, kappa, kern)
                ctr.append(((cs, g, K), t, np.full(len(t), (1 + g) * uu / len(phs))))
            allt = np.concatenate([t_in] + [c[1] for c in ctr]) if (len(t_in) or ctr) else np.zeros(0)
            if len(allt) == 0:
                continue
            lo, hi = float(allt.min()), float(allt.max())
            if hi - lo < 1e-9:
                lo, hi = lo * 0.999, hi * 1.001
            grid = np.linspace(lo, hi, nG)
            y = d['y1'] if kern == 'V1' else d['y2']
            cum, ny = ecdf_blocks(y, d['yblk'], d['nb'], grid)
            TW = interp_weights(t_in, w_in, grid, b_in, d['nb']) if len(t_in) else np.zeros((d['nb'], nG))
            nin = np.bincount(ib['blk'], minlength=d['nb']).astype(float) if len(ib['blk']) else np.zeros(d['nb'])
            prep[s] = dict(cum=cum, ny=ny, TW=TW, nin=nin, spk=d['spk_b'], trn=d['trans_b'],
                           cells=[(c[0], interp_weights(c[1], c[2], grid)) for c in ctr])

        def evaluate(C):
            num = np.zeros(len(C))
            den = np.zeros(len(C))
            cv = {}
            for i, s in enumerate(syms):
                if s not in prep:
                    continue
                p = prep[s]
                Cs = C[:, offs[i]:offs[i + 1]]
                with np.errstate(divide='ignore', invalid='ignore'):
                    Fr = (Cs @ p['cum']) / (Cs @ p['ny'])[:, None]
                    lam = (Cs @ p['spk']) / (Cs @ p['trn'])
                num += (1 - lam) * ((Cs @ p['TW']) * Fr).sum(1)
                den += Cs @ p['nin']
                for key, tw in p['cells']:
                    cv[key] = (1 - lam) * (Fr @ tw)
            with np.errstate(divide='ignore', invalid='ignore'):
                return num / den, cv

        pt_pool, pt_cells = evaluate(np.ones((1, nbt)))
        boots_pool, boots_cells = [], {}
        for C in boot_matrices(nbt, reps, seed):
            bp, bc = evaluate(C)
            boots_pool.append(bp)
            for k, v in bc.items():
                boots_cells.setdefault(k, []).append(v)

        def summarize(point, vals, nbk):
            vals = np.concatenate(vals) if vals else np.zeros(0)
            vals = vals[np.isfinite(vals)]
            q = THRESH['ci_q']
            return {'point': float(point), 'lo': float(np.quantile(vals, q[0])) if len(vals) else float('nan'),
                    'hi': float(np.quantile(vals, q[1])) if len(vals) else float('nan'),
                    'se': float(vals.std(ddof=1)) if len(vals) > 1 else float('nan'),
                    'n_blocks': int(nbk), 'valid': bool(nbk >= THRESH['min_blocks'])}
        nb_in = sum(int((prep[s]['nin'] > 0).sum()) for s in prep)
        out[kern] = {'pooled': summarize(pt_pool[0], boots_pool, nb_in), 'cells': {}}
        for k, v in pt_cells.items():
            s = k[0]
            out[kern]['cells'][k] = summarize(v[0], boots_cells.get(k, []), symdata[s]['nb'])
    return out


# ------------------------------------------------------------------ C4 sawtooth contrast
def sawtooth(S, b, mp, law, deltas=None):
    """C4 per symbol: virtual barriers b_v = b(1 + delta/w_bar) for gate AND knockout. Returns per-block
    sums (obs low/high, model V1/V2 low/high, counts), per-delta rows and the real-barrier contrast."""
    deltas = np.arange(-8, 8) / 16 if deltas is None else deltas
    ok = S['ok']
    Pp = S['Pp'][ok]
    Pn = Pp + S['D'][ok]
    blk = S['blk'][ok]
    wbar = float((b * Pp.astype(float)).mean())
    ub, inv = np.unique(blk, return_inverse=True)
    nb = len(ub)
    acc = np.zeros((nb, 8))          # stay_lo, n_lo, stay_hi, n_hi, V1_lo, V1_hi, V2_lo, V2_hi
    rows = []
    for dl in deltas:
        bv = b * (1 + dl / wbar)
        lv = knockout(Pp, Pn, bv)
        lo = lv['ph'] < 0.25
        hi = lv['ph'] >= 0.75
        kappa = bv / mp['m']
        m1 = model_per_tick(lv['K'], lv['ph'], kappa, mp['lam'], law, 'V1')
        m2 = model_per_tick(lv['K'], lv['ph'], kappa, mp['lam'], law, 'V2')
        st = lv['stay'].astype(float)
        cols = [st * lo, lo, st * hi, hi, m1 * lo, m1 * hi, m2 * lo, m2 * hi]
        for c, v in enumerate(cols):
            acc[:, c] += np.bincount(inv, weights=np.asarray(v, float), minlength=nb)
        nl, nh = int(lo.sum()), int(hi.sum())
        row = {'delta': float(dl), 'n_lo': nl, 'n_hi': nh}
        if nl > 100 and nh > 100:
            pl, ph_ = st[lo].mean(), st[hi].mean()
            se = math.sqrt(pl * (1 - pl) / nl + ph_ * (1 - ph_) / nh)
            row.update(contrast=float(pl - ph_), z_two_sample=float((pl - ph_) / se) if se > 0 else None,
                       model_V1=float(m1[lo].mean() - m1[hi].mean()), model_V2=float(m2[lo].mean() - m2[hi].mean()))
        rows.append(row)
    # real barrier (delta = 0 exactly): the S3 sawtooth, two-sample z
    lv = knockout(Pp, Pn, b)
    lo, hi = lv['ph'] < 0.25, lv['ph'] >= 0.75
    real = {'n_lo': int(lo.sum()), 'n_hi': int(hi.sum())}
    if lo.sum() > 100 and hi.sum() > 100:
        st = lv['stay']
        pl, ph_ = st[lo].mean(), st[hi].mean()
        se = math.sqrt(pl * (1 - pl) / lo.sum() + ph_ * (1 - ph_) / hi.sum())
        real.update(contrast=float(pl - ph_), z_two_sample=float((pl - ph_) / se))
    return {'acc': acc, 'blocks': ub, 'rows': rows, 'real_barrier': real, 'w_bar': wbar, 'n_ticks': int(ok.sum())}


def contrast_stats(acc, reps, seed):
    fn = lambda X: X[:, 0] / X[:, 1] - X[:, 2] / X[:, 3]
    bs = boot_generic(acc[:, :4], fn, reps, seed)
    tot = acc.sum(0)
    obs = tot[0] / tot[1] - tot[2] / tot[3] if tot[1] and tot[3] else float('nan')
    m1 = tot[4] / tot[1] - tot[5] / tot[3] if tot[1] and tot[3] else float('nan')
    m2 = tot[6] / tot[1] - tot[7] / tot[3] if tot[1] and tot[3] else float('nan')
    z = obs / bs['se'] if bs['se'] and np.isfinite(bs['se']) and bs['se'] > 0 else float('nan')
    return {'contrast': float(obs), 'se_boot': bs['se'], 'z': float(z), 'n_blocks': bs['n_blocks'],
            'valid': bs['valid'], 'model_V1': float(m1), 'model_V2': float(m2),
            'ratio_V1': float(obs / m1) if m1 else float('nan'), 'ratio_V2': float(obs / m2) if m2 else float('nan')}


# ------------------------------------------------------------------ S6 replay
def replay20(stay_rows, ok, choose, hold=20):
    """Non-overlapping 20-tick replays. Decide at tick T (row choose[T] >= 0), enter at T+1, moves
    T+1->T+2 .. T+20->T+21 must all survive (touching on the 20th move is a loss). Transitions T..T+20
    must be contiguous. Returns entry index, row, win flag per trade."""
    n = len(ok)
    cbad = np.concatenate([[0], np.cumsum(~ok)])
    cko = [np.concatenate([[0], np.cumsum(~s)]) for s in stay_rows]
    cand = np.flatnonzero(choose >= 0)
    ent, row, win = [], [], []
    t = 0
    while True:
        i = int(np.searchsorted(cand, t))
        if i >= len(cand):
            break
        T = int(cand[i])
        e = T + 1 + hold
        if e > n:
            break
        if cbad[e] - cbad[T] > 0:
            t = T + 1
            continue
        r = int(choose[T])
        if cko[r][e] - cko[r][T + 1] > 0:
            m = T + 1 + int(np.argmax(~stay_rows[r][T + 1:e]))
            win.append(False)
            t = m + 2
        else:
            win.append(True)
            t = e + 1
        ent.append(T)
        row.append(r)
    return np.array(ent, np.int64), np.array(row, np.int64), np.array(win, bool)


def replay_summary(ent, row, win, gs, blk, reps, seed):
    if len(ent) == 0:
        return {'n': 0}
    hu = np.array([PAYOUT20[gs[r]][0] for r in row])
    fl = np.array([PAYOUT20[gs[r]][1] for r in row])
    ret_hu = np.where(win, hu - 1, -1.0)
    ret_fl = np.where(win, fl - 1, -1.0)
    b = boot_mean(ret_hu, blk[ent], reps, seed)
    return {'n': int(len(ent)), 'win_rate': float(win.mean()), 'mean_ret_halfup': float(ret_hu.mean()),
            'mean_ret_floor': float(ret_fl.mean()), 'ci99_halfup': [b['lo'], b['hi']], 'n_blocks': b['n_blocks'],
            'valid': b['valid'], 'rates_used': sorted(set(float(gs[r]) for r in row))}


# ------------------------------------------------------------------ S7
def next_entry(sym, g, b, spot, pipdec, cells):
    """Current (w, K, phase) and the next spot at which the core band is entered in the drift direction
    (Crash drifts up, Boom down), plus the next eligible entry within 4 levels."""
    pip = 10.0 ** -pipdec
    lo, hi = THRESH['band_core']
    w = b * spot / pip
    K = math.floor(w)
    ph = w - K
    c = cells.get(K, {})
    cur = {'w': w, 'K': K, 'phase': ph, 'in_band': bool(lo <= ph < hi), 'gmin': c.get('gmin'),
           'eligible_now': bool(c.get('eligible') and lo <= ph < hi)}
    fam = family(sym)
    if fam == 'boom':
        start = K if ph >= hi else K - 1
        ks = [start - i for i in range(5)]
        spots = [(k + hi) * pip / b for k in ks]
    else:
        start = K if ph < lo else K + 1
        ks = [start + i for i in range(5)]
        spots = [(k + lo) * pip / b for k in ks]
    cur['next_entry'] = {'K': ks[0], 'spot': spots[0], 'eligible': bool(cells.get(ks[0], {}).get('eligible'))}
    ne = next(((k, s) for k, s in zip(ks, spots) if cells.get(k, {}).get('eligible')), None)
    cur['next_eligible_entry'] = {'K': ne[0], 'spot': ne[1]} if ne else None
    return cur


# ------------------------------------------------------------------ verdict
def verdict(st):
    """PASS-B / PASS-A / KILL / INCONCLUSIVE from the statistics dict (spec 4.2; thresholds in THRESH).
    Missing inputs count as 'not satisfied' for PASS criteria and never trigger a KILL."""
    T = THRESH
    ok = {}
    why = {}
    c4 = st.get('C4') or {}
    z = c4.get('z')
    rin = [k for k in ('ratio_V1', 'ratio_V2')
           if c4.get(k) is not None and T['A1_ratio'][0] <= c4[k] <= T['A1_ratio'][1]]
    ok['A1'] = bool(z is not None and np.isfinite(z) and z >= T['A1_z_min'] and rin)
    why['A1'] = f"C4 z={_f(z, 2)} ratio V1 {_f(c4.get('ratio_V1'), 2)} / V2 {_f(c4.get('ratio_V2'), 2)}"
    c5 = st.get('C5') or {}
    a2 = []
    for s in T['decision_symbols']:
        r = c5.get(s) or {}
        pv = [r.get('p_V1'), r.get('p_V2')]
        a2.append(any(p is not None and p >= T['A2_p_min'] for p in pv))
    ok['A2'] = bool(a2 and all(a2))
    why['A2'] = 'C5 p ' + ', '.join(f"{s} V1 {_f((c5.get(s) or {}).get('p_V1'), 3)} V2 {_f((c5.get(s) or {}).get('p_V2'), 3)}"
                                     for s in T['decision_symbols'])
    mc = st.get('M_cells') or []
    lows = [min(c['lo_V1'], c['lo_V2']) if c.get('valid') else float('nan') for c in mc]
    ok['A3'] = bool(mc and all(np.isfinite(v) and v >= T['A3_M_lo_min'] for v in lows))
    worst = min((v for v in lows if np.isfinite(v)), default=None)
    why['A3'] = f"{len(mc)} tradable cells, worst M lower bound {_f(worst, 5)}"
    D = st.get('D') or {}
    M = st.get('M') or {}
    mv = [M.get(k, {}).get('point') for k in ('V1', 'V2') if M.get(k)]
    a4 = None
    if D.get('point') is not None and mv and D.get('se') and np.isfinite(D['se']) and D['se'] > 0:
        a4 = (D['point'] - min(mv)) / D['se']
    ok['A4'] = bool(a4 is not None and a4 >= T['A4_z_min'])
    why['A4'] = f"(D-M)/SE_D = {_f(a4, 2)}"
    c3 = [v for v in (st.get('C3') or {}).values() if v is not None]
    c1 = st.get('C1')
    ok['A5'] = bool(c3 and all(np.isfinite(v) and v < T['A5_C3_max'] for v in c3) and c1 is not None and c1 < T['A5_C1_max'])
    why['A5'] = f"C3 {', '.join(_f(v, 5) for v in c3) or 'n/a'}; C1 {_f(c1, 5)}"
    ok['A6'] = st.get('OR1') == 'PASS'
    why['A6'] = f"OR-1 {st.get('OR1')}"
    ok['A7'] = st.get('A7') == 'PASS'
    why['A7'] = f"barriers {st.get('A7')}"
    pass_a = all(ok[k] for k in ('A1', 'A2', 'A3', 'A4', 'A5', 'A6', 'A7'))
    pass_b = bool(pass_a and D.get('valid') and D.get('lo') is not None and D['lo'] >= T['B_D_lo_min']
                  and (D.get('n_blocks') or 0) >= T['B_min_blocks'])
    kills = []
    if D.get('valid') and D.get('hi') is not None and D['hi'] < T['K1_D_hi_max'] and (D.get('n') or 0) >= T['K1_min_inband']:
        kills.append(f"K1 D upper {D['hi']:.5f} < 1 on {D['n']} in-band ticks")
    mh = [M[k]['hi'] for k in ('V1', 'V2') if M.get(k) and M[k].get('valid')]
    if len(mh) == 2 and all(h < T['K2_M_hi_max'] for h in mh):
        kills.append(f"K2 M upper {mh[0]:.5f}/{mh[1]:.5f} < 1 under both kernels")
    if z is not None and np.isfinite(z) and z < T['K3_z_max'] and (c4.get('n_ticks') or 0) >= T['K3_min_ticks']:
        kills.append(f"K3 C4 z {z:.2f} < 1 on {c4.get('n_ticks')} Crash ticks")
    if st.get('OR1') == 'FAIL':
        kills.append('K4 OR-1 failed: raw_incl does not reproduce the house runs')
    if st.get('HALT'):
        # no knockout rule reproduces the house runs on some Boom/Crash snapshot: the lattice model is unfounded
        kills.append(f"K4 HALT: {len(st['HALT'])} snapshot(s) with no rule matching {T['HALT_match']:.0%}")
    if kills:
        label, reasons = 'KILL', kills
    elif pass_b:
        label, reasons = 'PASS-B', [f"all A1-A7; D lower {D['lo']:.5f} >= 1 over {D['n_blocks']} blocks"]
    elif pass_a:
        label, reasons = 'PASS-A', ['all A1-A7', f"D lower {_f(D.get('lo'), 5)} over {D.get('n_blocks')} blocks (PASS-B needs >= 1 and >= 16 blocks)"]
    else:
        label = 'INCONCLUSIVE'
        reasons = [f"{k} not met ({why[k]})" for k in ('A1', 'A2', 'A3', 'A4', 'A5', 'A6', 'A7') if not ok[k]]
    return {'label': label, 'reasons': reasons, 'checks': ok, 'detail': why, 'A4_z': a4}


def _f(x, nd):
    if x is None:
        return 'n/a'
    try:
        if not np.isfinite(x):
            return 'nan'
    except TypeError:
        return str(x)
    return f'{x:.{nd}f}'


# ------------------------------------------------------------------ full analysis
def run_analysis(series, barriers, spots=None, oracle_records=None, a7_info=None, reps_ticks=None,
                 reps_model=None, seed=20260924, log=print):
    """series: {sym: (ep, px, pipdec)}; barriers: {sym: {g: b}}; spots: {sym: {g: (spot, pipdec)}} for S7.
    Returns (analysis dict, summary lines). Pure: no files, no network."""
    T = THRESH
    rt = reps_ticks or T['boot_ticks']
    rm = reps_model or T['boot_model']
    law = load_frozen()
    A = {'thresholds': T, 'symbols': {}, 'reps': {'ticks': rt, 'model': rm}}
    lines = []

    def say(s=''):
        lines.append(s)
        log(s)

    # oracle outcomes decide the band used by D and M
    orc = oracle_outcomes(oracle_records or [])
    A['oracle'] = {'outcomes': orc, 'n_records': len(oracle_records or []),
                   'n_primary': sum(r.get('mode') == 'primary' for r in oracle_records or [])}
    band = T['band_ext'] if orc['OR2']['band'] == 'ext' else T['band_core']
    A['band_used'] = list(band)
    say(f"ACCU phase-lattice analysis  band for D/M = [{band[0]}, {band[1]})  (OR-2 {orc['OR2']['decision']}, "
        f"{orc['OR2']['n_events']} E0 events)")
    if orc['HALT']:
        say('HALT: some Boom/Crash snapshot has no rule matching 95% -> the lattice program would be dead:')
        for h in orc['HALT']:
            say('   ' + h)

    dec_parts = {}      # decision-symbol pieces pooled for D, M, C1-C4
    symdata_M = {}
    M_cells = []
    for sym in sorted(series):
        ep, px, pipdec = series[sym]
        S = prep_series(sym, ep, px, pipdec)
        R = {'n_ticks': int(len(S['ep'])), 'pip_decimals': S['pipdec'], 'interval_s': S['interval'],
             'n_contiguous': S['n_ok'], 'first_epoch': int(S['ep'][0]), 'last_epoch': int(S['ep'][-1])}
        A['symbols'][sym] = R
        bs = {float(g): float(b) for g, b in (barriers.get(sym) or {}).items()}
        say('')
        say(f"=== {sym}: {R['n_ticks']} ticks, {S['n_ok']} contiguous pairs, spot {S['P'].min() * 10.0 ** -pipdec:.3f}-"
            f"{S['P'].max() * 10.0 ** -pipdec:.3f}, barriers {', '.join(f'{g:.2f}:{b:.6g}' for g, b in sorted(bs.items())) or 'none'}")
        lvs = {}
        for g, b in sorted(bs.items()):
            lv = knockout(S['Pp'], S['P'][1:], b)
            lv['b'] = b
            lvs[g] = lv
        fam = family(sym)
        mp = None
        if fam != 'vol':
            s1 = step_law(S, law, rt, seed)
            R['S1'] = s1
            mp = s1['model']
            say(f"S1 m_hat*N*1e3 {s1['mhat_N_1e3']['point']:.4f} [{_f(s1['mhat_N_1e3']['lo'], 4)}, {_f(s1['mhat_N_1e3']['hi'], 4)}]"
                f"  CV {_f(s1['cv'], 3)}  zero-step {_f(s1['zero_step_frac'], 4)}  lam_hat*N {_f(s1['lam_hat_N'], 3)} "
                f"[{_f(s1['lam_hat_N_ci99'][0], 3)}, {_f(s1['lam_hat_N_ci99'][1], 3)}]  KS(V1) p {_f(s1['ks_V1_frozen']['p'], 4)}"
                f"  u_now {_f(s1['u_now_pips'], 2)} pips  model m: {mp['m_source']}, lam: {mp['lam_source']}")
            s2 = spike_timing(S, rt, seed)
            R['S2'] = s2
            band2 = s2.get('gap_cv_band99') or [None, None]
            say(f"S2 spikes {s2['n_spikes']}  gap CV {_f(s2.get('gap_cv'), 3)} (geometric 99% [{_f(band2[0], 3)}, {_f(band2[1], 3)}])  "
                f"max|z| age {_f(max([abs(r['z']) for r in s2['age_bins']] or [float('nan')]), 2)}  "
                f"clock ratio {_f(s2['clock']['ratio'], 3)} p {_f(s2['clock']['p_one_sided'], 4)}")
        if not lvs:
            continue
        # eligibility (frozen law, quoted b) over the K levels present, plus a margin for S7
        elig = {}
        if mp is not None:
            kr = {}
            for g, lv in lvs.items():
                Ks = lv['K'][S['ok']]
                k0, k1 = int(Ks.min()), int(Ks.max())
                if spots and sym in spots and g in spots[sym]:
                    sp_, pd_ = spots[sym][g]
                    kc = int(math.floor(lvs[g]['b'] * sp_ * 10.0 ** pd_))
                    k0, k1 = min(k0, kc), max(k1, kc)
                kr[g] = range(max(1, k0 - 5), k1 + 6)
            elig = eligibility({g: lv['b'] for g, lv in lvs.items()}, kr, mp, law)
            R['eligibility'] = {str(g): {str(K): c for K, c in cells.items()} for g, cells in elig.items()}
            ecells = [(g, K, c['gmin']) for g, cells in elig.items() for K, c in cells.items() if c['eligible']]
            by_g = {}
            for g, K, v in sorted(ecells):
                by_g.setdefault(g, []).append((K, v))
            say('eligible cells (G_min >= %.4f on [.05,.125)): ' % T['eligible_gmin'] +
                ('; '.join(f'{g:.2f} K{c[0][0]}' + (f'..K{c[-1][0]} ({len(c)} levels)' if len(c) > 1 else '') +
                           f' G_min {max(v for _, v in c):.5f}..{min(v for _, v in c):.5f}'
                           for g, c in by_g.items()) or 'none in range'))
        # S3 survival map
        R['S3'] = {}
        for g, lv in lvs.items():
            kappa = lv['b'] / mp['m'] if mp else None
            sm3 = survival_map(lv, S['ok'], g, S['blk'], law if mp else None, kappa, mp['lam'] if mp else None, rt, seed)
            R['S3'][str(g)] = sm3
            bb = sm3['bands']
            say(f"S3 g={g:.2f} " + ('  '.join(f"{k} n={v['n']} G={v['G']:.5f}" for k, v in bb.items())
                                    or 'no ticks in the listed bands'))
            if sym in T['decision_symbols']:
                for K in sorted(set(c['K'] for c in sm3['cells'])):
                    cs = {c['eighth']: c for c in sm3['cells'] if c['K'] == K and c['n'] >= 200}
                    if cs:
                        say(f"     K={K} G by phase eighth, obs (V1 model): " + '  '.join(
                            f"{e}/8:{cs[e]['G']:.4f}({cs[e].get('G_V1', float('nan')):.4f})" for e in sorted(cs)))
        if mp is None:
            continue
        # S4 model check (+ C5)
        s4 = model_check(S, lvs, mp, law, seed)
        R['S4'] = s4
        fz = {k: [r['frozen_' + k]['p'] for r in s4['per_rate'].values() if r['frozen_' + k]['p'] is not None]
              for k in ('V1', 'V2')}
        say(f"S4/C5 cross-fit chi2 p (Bonferroni over {s4['C5']['n_rates']} rates): V1 {_f(s4['C5']['p_V1'], 4)}"
            f"  V2 {_f(s4['C5']['p_V2'], 4)};  frozen-law min p: V1 {_f(min(fz['V1'], default=None), 4)}"
            f"  V2 {_f(min(fz['V2'], default=None), 4)}")
        # D and C3 inputs
        okidx = np.flatnonzero(S['ok'])
        gs, score = choice_scores(lvs, elig, band, okidx)
        best, inb = pick(gs, score)
        stay_ok = np.vstack([lvs[g]['stay'][okidx] for g in gs])
        x = (1 + np.array(gs))[best] * stay_ok[best, np.arange(len(okidx))]
        blk_ok = S['blk'][okidx]
        Dsym = boot_mean(x[inb], blk_ok[inb], rt, seed)
        R['D'] = Dsym
        # per-cell direct G
        cellD = {}
        for i, g in enumerate(gs):
            sel = inb & (best == i)
            if not sel.any():
                continue
            Kc = lvs[g]['K'][okidx][sel]
            for K in np.unique(Kc):
                m = np.zeros(len(okidx), bool)
                m[np.flatnonzero(sel)[Kc == K]] = True
                cellD[f'{g:.2f}/K{int(K)}'] = boot_mean(x[m], blk_ok[m], rt, seed)
        R['D_cells'] = cellD
        say(f"S5 D ({sym}) = {_f(Dsym['point'], 5)} [{_f(Dsym['lo'], 5)}, {_f(Dsym['hi'], 5)}] n_inband={Dsym['n']} "
            f"blocks={Dsym['n_blocks']}{'' if Dsym['valid'] else ' INVALID'}")
        # S6 replay
        R['S6'] = {}
        stay_all = [lvs[g]['stay'] for g in gs]
        _, score_all = choice_scores(lvs, elig, band)
        ball, iall = pick(gs, score_all)
        choose = np.where(iall, ball, -1)
        e_, r_, w_ = replay20(stay_all, S['ok'], choose)
        R['S6']['choice'] = replay_summary(e_, r_, w_, gs, S['blk'], rt, seed)
        n_all = len(S['ok'])
        for i, g in enumerate(gs):
            lv = lvs[g]
            cells = elig.get(g, {})
            ek = np.array([cells.get(int(k), {}).get('eligible', False) for k in range(int(lv['K'].min()), int(lv['K'].max()) + 1)])
            eK = ek[lv['K'] - int(lv['K'].min())] if len(ek) else np.zeros(n_all, bool)
            gates = {'[.05,.125)': eK & (lv['ph'] >= .05) & (lv['ph'] < .125),
                     '[0,.125)': eK & (lv['ph'] < .125)}
            if abs(g - 0.04) < 1e-9:
                gates['frozen [14,14.25)'] = (lv['w'] >= 14) & (lv['w'] < 14.25)
            gates['placebo T/2'] = np.roll(gates['[.05,.125)'], -(n_all // 2))
            for name, gate in gates.items():
                ch = np.where(gate, 0, -1)
                e_, r_, w_ = replay20([lv['stay']], S['ok'], ch)
                R['S6'][f'{g:.2f} {name}'] = replay_summary(e_, r_, w_, [g], S['blk'], rt, seed)
        for name, rc in R['S6'].items():
            if rc['n'] and (name == 'choice' or sym in T['decision_symbols']):
                say(f"S6 replay {name}: n={rc['n']} win {rc['win_rate']:.4f} mean/trade {rc['mean_ret_halfup']:+.4f}"
                    f" (floor {rc['mean_ret_floor']:+.4f}) 99% [{_f(rc['ci99_halfup'][0], 4)}, {_f(rc['ci99_halfup'][1], 4)}]"
                    f"{'' if rc['valid'] else ' INVALID'}")
        # S7 current state (quoted spot; the last tick when no quote is available)
        R['S7'] = {}
        for g in gs:
            src7 = 'quote'
            sp_, pd_ = (spots or {}).get(sym, {}).get(g) or (None, None)
            if sp_ is None:
                sp_, pd_, src7 = round(float(S['P'][-1]) * 10.0 ** -S['pipdec'], S['pipdec']), S['pipdec'], 'last tick'
            c7 = next_entry(sym, g, lvs[g]['b'], sp_, pd_, elig.get(g, {}))
            c7['spot'], c7['spot_source'] = sp_, src7
            R['S7'][f'{g:.2f}'] = c7
            ne = c7['next_eligible_entry']
            say(f"S7 g={g:.2f} spot {sp_} ({src7}) w {c7['w']:.4f} K {c7['K']} phase {c7['phase']:.4f} "
                f"{'ELIGIBLE NOW' if c7['eligible_now'] else 'not in an eligible band'}; next band entry "
                f"K{c7['next_entry']['K']} at {c7['next_entry']['spot']:.{pd_}f}"
                f"{' (eligible)' if c7['next_entry']['eligible'] else ''}"
                + (f"; next eligible K{ne['K']} at {ne['spot']:.{pd_}f}" if ne else ''))
        # pieces for the pooled decision statistics
        if sym in T['decision_symbols']:
            part = {'x': x[inb], 'blk': blk_ok[inb], 'n_ok': len(okidx)}
            # C1 / C2 at 4%
            if 0.04 in lvs:
                lv4 = lvs[0.04]
                st4 = lv4['stay'][okidx].astype(float) * 1.04
                part['c1'] = (st4, blk_ok)
                hb = (lv4['ph'][okidx] >= .75)
                part['c2'] = (st4[hb], blk_ok[hb])
                part['c4'] = sawtooth(S, lv4['b'], mp, law)
            # C3 placebo (circular time shift of the gate)
            part['c3'] = {}
            for lab, sh in (('T/2', len(okidx) // 2), ('T/3', len(okidx) // 3)):
                sc = np.roll(score, -sh, axis=1)
                bp, ip = pick(gs, sc)
                xp = (1 + np.array(gs))[bp] * stay_ok[bp, np.arange(len(okidx))]
                part['c3'][lab] = (xp[ip], blk_ok[ip])
            dec_parts[sym] = part
            # M inputs
            sm = S['small']
            y1, y2 = jitter_y(np.abs(S['D'][sm]), S['Pp'][sm], mp['m'], np.random.default_rng(seed + 1))
            ub = np.unique(S['blk'][S['ok']])
            bidx = lambda bl: np.searchsorted(ub, bl)
            kap = np.array([lvs[g]['b'] / mp['m'] for g in gs])
            it = np.flatnonzero(inb)
            Kst = np.vstack([lvs[g]['K'][okidx][it] for g in gs])
            Pst = np.vstack([lvs[g]['ph'][okidx][it] for g in gs])
            symdata_M[sym] = {
                'y1': y1, 'y2': y2, 'yblk': bidx(S['blk'][sm]), 'nb': len(ub),
                'spk_b': np.bincount(bidx(S['blk'][S['spike']]), minlength=len(ub)).astype(float),
                'trans_b': np.bincount(bidx(S['blk'][S['ok']]), minlength=len(ub)).astype(float),
                'inband': {'K': Kst[best[inb], np.arange(len(it))], 'ph': Pst[best[inb], np.arange(len(it))],
                           'kappa': kap[best[inb]], 'wgt': (1 + np.array(gs))[best[inb]], 'blk': bidx(blk_ok[inb])}}
            for key in cellD:
                g_, K_ = key.split('/K')
                M_cells.append((sym, float(g_), int(K_), lvs[float(g_)]['b'] / mp['m'], band))

    # ---------------- ST-H2 kill lines (S2), pooled over the Boom/Crash symbols
    s2s = {s: r['S2'] for s, r in A['symbols'].items() if 'S2' in r}
    if s2s:
        ren = {s: v.get('renewal_killed') for s, v in s2s.items()}
        ks = sum((v['clock']['share_50_60'] or 0) * v['clock']['n_spikes'] for v in s2s.values())
        ns = sum(v['clock']['n_spikes'] for v in s2s.values())
        ex = sum((v['clock']['exposure'] or 0) * v['clock']['n_spikes'] for v in s2s.values()) / max(ns, 1)
        ratio = (ks / ns) / ex if ns and ex else float('nan')
        clock = ('INSUFFICIENT (< %d spikes)' % T['S2_clock_min_spikes'] if ns < T['S2_clock_min_spikes'] else
                 'KILLED' if ratio < T['S2_clock_ratio'] else 'NOT KILLED')
        A['ST_H2'] = {'renewal_killed': ren, 'clock_pooled': {'n_spikes': ns, 'ratio': ratio, 'status': clock}}
        say('')
        say(f"ST-H2(b) renewal killed (CV in band, all |z| < 3): " +
            ', '.join(f'{s} {"yes" if v else ("no" if v is not None else "n/a")}' for s, v in sorted(ren.items())))
        say(f"ST-H2(a) clock [50,60) pooled ratio {_f(ratio, 3)} over {ns} spikes -> {clock}")

    # ---------------- pooled decision statistics over CRASH1000 + CRASH500
    st = {}
    say('')
    say(f"=== decision statistics (pooled {', '.join(dec_parts) or 'no decision symbols'})")
    if dec_parts:
        def pool(key):
            xs = [p[key][0] for p in dec_parts.values() if key in p]
            bl = [p[key][1] + (i + 1) * 10 ** 7 for i, p in enumerate(dec_parts.values()) if key in p]
            return (np.concatenate(xs), np.concatenate(bl)) if xs else (np.zeros(0), np.zeros(0, np.int64))
        xs = np.concatenate([p['x'] for p in dec_parts.values()])
        bl = np.concatenate([p['blk'] + (i + 1) * 10 ** 7 for i, p in enumerate(dec_parts.values())])
        D = boot_mean(xs, bl, rt, seed)
        st['D'] = D
        say(f"D  = {_f(D['point'], 5)}  99% [{_f(D['lo'], 5)}, {_f(D['hi'], 5)}]  SE {_f(D['se'], 5)}  in-band ticks {D['n']}"
            f"  blocks {D['n_blocks']}{'' if D['valid'] else '  INVALID (< 8 blocks)'}")
        if symdata_M and any(len(d['inband']['K']) for d in symdata_M.values()):
            M = model_M(symdata_M, M_cells, rm, seed)
            st['M'] = {k: M[k]['pooled'] for k in M}
            st['M_cells'] = []
            for key in sorted(M['V1']['cells']):
                c1, c2 = M['V1']['cells'][key], M['V2']['cells'][key]
                st['M_cells'].append({'cell': f'{key[0]} {key[1]:.2f}/K{key[2]}', 'point_V1': c1['point'], 'lo_V1': c1['lo'],
                                      'hi_V1': c1['hi'], 'point_V2': c2['point'], 'lo_V2': c2['lo'], 'hi_V2': c2['hi'],
                                      'valid': bool(c1['valid'] and c2['valid'])})
            for k in ('V1', 'V2'):
                m = st['M'][k]
                say(f"M {k} = {m['point']:.5f}  99% [{_f(m['lo'], 5)}, {_f(m['hi'], 5)}]")
            for c in st['M_cells']:
                say(f"   cell {c['cell']}: M V1 {c['point_V1']:.5f} [{_f(c['lo_V1'], 5)}, {_f(c['hi_V1'], 5)}]  "
                    f"V2 {c['point_V2']:.5f} [{_f(c['lo_V2'], 5)}, {_f(c['hi_V2'], 5)}]")
        else:
            say('M  = n/a (no in-band ticks on the decision symbols)')
        x1, b1 = pool('c1')
        if len(x1):
            c1 = boot_mean(x1, b1, rt, seed)
            st['C1'] = c1['point']
            st['C1_ci'] = c1
            x2, b2 = pool('c2')
            c2 = boot_mean(x2, b2, rt, seed) if len(x2) else {'point': None}
            st['C2'] = c2
            say(f"C1 ungated G at 4% = {c1['point']:.5f} [{_f(c1['lo'], 5)}, {_f(c1['hi'], 5)}]   "
                f"C2 G in [.75,1) = {_f(c2.get('point'), 5)}")
        c3 = {}
        for lab in ('T/2', 'T/3'):
            xs3 = np.concatenate([p['c3'][lab][0] for p in dec_parts.values()])
            c3[lab] = float(xs3.mean()) if len(xs3) else None
        st['C3'] = c3
        say(f"C3 time-shift placebo G: T/2 {_f(c3['T/2'], 5)}  T/3 {_f(c3['T/3'], 5)}")
        saws = [p['c4'] for p in dec_parts.values() if 'c4' in p]
        if saws:
            acc = np.vstack([s['acc'] for s in saws])
            c4 = contrast_stats(acc, rt, seed)
            c4['n_ticks'] = int(sum(s['n_ticks'] for s in saws))
            c4['per_symbol'] = {}
            for sym, p in dec_parts.items():
                if 'c4' in p:
                    cs = contrast_stats(p['c4']['acc'], rt, seed)
                    cs.update(rows=p['c4']['rows'], real_barrier=p['c4']['real_barrier'], w_bar=p['c4']['w_bar'],
                              n_positive=sum(1 for r in p['c4']['rows'] if (r.get('contrast') or 0) > 0),
                              n_measured=sum(1 for r in p['c4']['rows'] if 'contrast' in r))
                    c4['per_symbol'][sym] = cs
            st['C4'] = c4
            say(f"C4 virtual-barrier sawtooth at 4%: P(stay|[0,.25)) - P(stay|[.75,1)) = {c4['contrast']:+.5f}  z {c4['z']:.2f}"
                f"  model V1 {c4['model_V1']:+.5f} V2 {c4['model_V2']:+.5f}  obs/model {c4['ratio_V1']:.3f} / {c4['ratio_V2']:.3f}"
                f"  ({c4['n_ticks']} ticks)")
            for sym, cs in c4['per_symbol'].items():
                rb = cs['real_barrier']
                say(f"   {sym}: {cs['contrast']:+.5f} z {cs['z']:.2f}, positive in {cs['n_positive']}/{cs['n_measured']} deltas;"
                    f" real barrier {_f(rb.get('contrast'), 5)} (two-sample z {_f(rb.get('z_two_sample'), 2)}, n_hi {rb['n_hi']})")
        c5 = {s: A['symbols'][s]['S4']['C5'] for s in dec_parts if 'S4' in A['symbols'][s]}
        st['C5'] = c5
    st['OR1'] = orc['OR1']['status']
    st['HALT'] = list(orc['HALT'])
    st['A7'] =(a7_info or {}).get('status', 'NOT_EVALUATED')
    A['a7'] = a7_info
    V = verdict(st)
    A['decision'] = st
    A['verdict'] = V
    say('')
    say(f"oracle: OR-1 {orc['OR1']['status']}  OR-2 {orc['OR2']['decision']} ({orc['OR2']['n_events']} events, "
        f"{orc['OR2']['n_breach']} breaches)  OR-3 {orc['OR3']['status']}  HALT {'YES' if orc['HALT'] else 'no'}")
    say(f"A7 barriers: {st['A7']}  {(a7_info or {}).get('detail', '')}")
    for k in ('A1', 'A2', 'A3', 'A4', 'A5', 'A6', 'A7'):
        say(f"  {k} {'ok  ' if V['checks'][k] else 'FAIL'} {V['detail'][k]}")
    say(f"VERDICT: {V['label']} {'; '.join(V['reasons'])}")
    return A, lines


def jsonable(o):
    """Recursively convert numpy types / tuple keys for json.dump (nan -> None)."""
    if isinstance(o, dict):
        return {(k if isinstance(k, str) else (' '.join(map(str, k)) if isinstance(k, tuple) else str(k))): jsonable(v)
                for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [jsonable(v) for v in o]
    if isinstance(o, np.ndarray):
        return jsonable(o.tolist())
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (float, np.floating)):
        f = float(o)
        return f if math.isfinite(f) else None
    if isinstance(o, range):
        return [o.start, o.stop]
    return o
