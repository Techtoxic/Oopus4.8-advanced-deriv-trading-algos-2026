"""
Statistical test suite for randomness / edge detection on tick & digit sequences.
All functions return plain dicts so results serialize cleanly to JSON/CSV.
"""
import numpy as np
from scipy import stats
from collections import Counter


def last_digit(price, symbol_decimals=None):
    """Last decimal digit of a price string. Deriv digit contracts use the
    last digit of the displayed price (pip-precision aware)."""
    s = repr(price)
    # use string form of price as Deriv presents fixed decimals; fall back to int
    if "." in s:
        return int(s.replace("-", "").split(".")[-1][-1])
    return int(str(abs(int(price)))[-1])


def digits_from_prices(prices, decimals):
    """Format each price to `decimals` places and take the last digit —
    this matches how Deriv computes the last digit for DIGIT contracts."""
    out = []
    for p in prices:
        out.append(int(f"{p:.{decimals}f}"[-1]))
    return np.array(out, dtype=int)


def chi_square_uniform(digits, k=10):
    counts = np.array([np.sum(digits == d) for d in range(k)], dtype=float)
    n = counts.sum()
    expected = np.full(k, n / k)
    chi2, p = stats.chisquare(counts, expected)
    return {
        "test": "chi_square_uniform",
        "n": int(n),
        "counts": counts.astype(int).tolist(),
        "expected_each": float(n / k),
        "chi2": float(chi2),
        "dof": k - 1,
        "p_value": float(p),
        "reject_uniform_at_0.05": bool(p < 0.05),
    }


def runs_test(binary):
    """Wald–Wolfowitz runs test on a binary (0/1) sequence."""
    x = np.asarray(binary)
    n1 = int(np.sum(x == 1))
    n0 = int(np.sum(x == 0))
    n = n0 + n1
    if n1 == 0 or n0 == 0:
        return {"test": "runs", "runs": None, "p_value": None, "note": "degenerate"}
    runs = 1 + int(np.sum(x[1:] != x[:-1]))
    mu = 1 + (2.0 * n0 * n1) / n
    var = (2.0 * n0 * n1 * (2.0 * n0 * n1 - n)) / (n * n * (n - 1))
    z = (runs - mu) / np.sqrt(var) if var > 0 else 0.0
    p = 2 * (1 - stats.norm.cdf(abs(z)))
    return {
        "test": "runs",
        "n": n, "n0": n0, "n1": n1,
        "runs": runs, "expected_runs": float(mu),
        "z": float(z), "p_value": float(p),
        "reject_random_at_0.05": bool(p < 0.05),
    }


def autocorrelation(series, lags):
    x = np.asarray(series, dtype=float)
    x = x - x.mean()
    denom = np.sum(x * x)
    out = {}
    for lag in lags:
        if lag >= len(x) or denom == 0:
            out[lag] = None
        else:
            out[lag] = float(np.sum(x[:-lag] * x[lag:]) / denom)
    # approximate 95% CI for white noise: +-1.96/sqrt(N)
    ci = 1.96 / np.sqrt(len(x))
    return {"test": "autocorrelation", "lags": {str(k): v for k, v in out.items()},
            "white_noise_95ci": float(ci)}


def serial_correlation_digits(digits):
    """Lag-1 serial correlation of the digit stream (treated numerically)."""
    d = np.asarray(digits, dtype=float)
    if len(d) < 3:
        return {"test": "serial_corr", "r": None}
    r = float(np.corrcoef(d[:-1], d[1:])[0, 1])
    n = len(d) - 1
    # Fisher z test for r != 0
    if abs(r) < 1:
        z = 0.5 * np.log((1 + r) / (1 - r)) * np.sqrt(n - 3)
        p = 2 * (1 - stats.norm.cdf(abs(z)))
    else:
        p = 0.0
    return {"test": "serial_corr_lag1", "r": r, "n": n, "p_value": float(p),
            "reject_zero_at_0.05": bool(p < 0.05)}


def shannon_entropy(digits, k=10):
    counts = Counter(int(x) for x in digits)
    n = sum(counts.values())
    h = 0.0
    for d in range(k):
        p = counts.get(d, 0) / n
        if p > 0:
            h -= p * np.log2(p)
    return {"test": "shannon_entropy", "entropy_bits": float(h),
            "max_bits": float(np.log2(k)),
            "fraction_of_max": float(h / np.log2(k))}


def fft_periodicity(series, top=8):
    """FFT power spectrum on a de-meaned series; report dominant periods."""
    x = np.asarray(series, dtype=float)
    x = x - x.mean()
    n = len(x)
    if n < 16:
        return {"test": "fft", "note": "too short"}
    freqs = np.fft.rfftfreq(n)
    power = np.abs(np.fft.rfft(x)) ** 2
    # ignore DC (index 0)
    idx = np.argsort(power[1:])[::-1][:top] + 1
    peaks = []
    mean_pow = float(np.mean(power[1:]))
    for i in idx:
        period = float(1.0 / freqs[i]) if freqs[i] > 0 else None
        peaks.append({"period_ticks": period,
                      "power_ratio_vs_mean": float(power[i] / mean_pow)})
    return {"test": "fft", "n": n, "dominant_peaks": peaks,
            "max_power_ratio_vs_mean": peaks[0]["power_ratio_vs_mean"] if peaks else None}


def conditional_digit_table(digits):
    """P(next digit | current digit) — the N -> N+1 (and by extension the
    'does the previous tick carry info about the next') transition matrix.
    Tests independence with a chi-square on the 10x10 contingency table."""
    d = np.asarray(digits, dtype=int)
    table = np.zeros((10, 10), dtype=float)
    for a, b in zip(d[:-1], d[1:]):
        table[a, b] += 1
    chi2, p, dof, _ = stats.chi2_contingency(table + 1e-9)
    # also the simpler 'repeat' rate: P(next == current)
    repeats = int(np.sum(d[:-1] == d[1:]))
    n = len(d) - 1
    repeat_rate = repeats / n
    # binomial test vs 0.1
    bt = stats.binomtest(repeats, n, 0.1)
    return {
        "test": "conditional_transition_chi2",
        "n_pairs": n,
        "chi2": float(chi2), "dof": int(dof), "p_value": float(p),
        "reject_independence_at_0.05": bool(p < 0.05),
        "repeat_rate_next_eq_current": float(repeat_rate),
        "repeat_expected": 0.1,
        "repeat_binom_p": float(bt.pvalue),
    }


def conditional_predict_lag(digits, lag):
    """Does the digit `lag` steps back predict the current digit?
    Returns chi-square independence p-value for that lag's contingency table."""
    d = np.asarray(digits, dtype=int)
    if len(d) <= lag + 1:
        return {"lag": lag, "p_value": None}
    table = np.zeros((10, 10), dtype=float)
    for a, b in zip(d[:-lag], d[lag:]):
        table[a, b] += 1
    chi2, p, dof, _ = stats.chi2_contingency(table + 1e-9)
    return {"lag": lag, "chi2": float(chi2), "dof": int(dof), "p_value": float(p),
            "reject_independence_at_0.05": bool(p < 0.05)}
