"""sigma_model.py — shared model pieces (wrapped-normal pmf, contract winsets, payout grid)."""
import math

def wrapped_normal_pmf(sigma, center):
    from math import erf
    def cdf(x): return 0.5 * (1 + erf(x / (sigma * math.sqrt(2))))
    pmf = [0.0] * 10
    for d in range(10):
        for wrap in (-30, -20, -10, 0, 10, 20, 30):
            off = (d - center) + wrap
            pmf[d] += cdf(off + 0.5) - cdf(off - 0.5)
    return pmf

CONTRACTS = ([("DIGITMATCH", d) for d in range(10)] + [("DIGITDIFF", d) for d in range(10)] +
             [("DIGITOVER", k) for k in range(9)] + [("DIGITUNDER", k) for k in range(1, 10)] +
             [("DIGITEVEN", None), ("DIGITODD", None)])

def winset(t, b):
    if t == "DIGITMATCH": return {b}
    if t == "DIGITDIFF": return set(range(10)) - {b}
    if t == "DIGITOVER": return set(range(b + 1, 10))
    if t == "DIGITUNDER": return set(range(0, b))
    if t == "DIGITEVEN": return {0, 2, 4, 6, 8}
    return {1, 3, 5, 7, 9}

# standard 18-symbol payout grid (measured live 2026-06-11, re-verified live 2026-06-12)
GRID = {}
_OU = {0: 1.096, 1: 1.232, 2: 1.404, 3: 1.634, 4: 1.953, 5: 2.427, 6: 3.205, 7: 4.717, 8: 8.929}
for d in range(10):
    GRID[("DIGITMATCH", d)] = 8.929
    GRID[("DIGITDIFF", d)] = 1.0958
for k in range(9): GRID[("DIGITOVER", k)] = _OU[k]
for k in range(1, 10): GRID[("DIGITUNDER", k)] = _OU[9 - k]
GRID[("DIGITEVEN", None)] = GRID[("DIGITODD", None)] = 1.953
