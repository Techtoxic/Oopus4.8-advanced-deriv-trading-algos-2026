# Boundary probe: three generator-boundary vectors, 1HZ30V and R_75 (2026-09-29)

Tool: `tools/boundary_probe.py`. Read-only, public data. Planted controls (rebase, reflecting drift,
midnight burst, float32-computed quotes) were all detected on synthetic data, and a clean GBM passed.

## Vector 1: macro reflection or rebase

- **Data limit.** The candle endpoint returns 366 days for both symbols, so "lifetime" means one year.
- **No rebase.** The largest overnight jump is 3.1 tick-sigmas on 1HZ30V and 3.3 on R_75.
- **No lifetime drift.** Simple drift t is 0.07 on 1HZ30V and -0.10 on R_75.
- **The reflection test is one-sided on both.** In one year, 1HZ30V never sat in the bottom 5% of its
  running range and R_75 never sat in the top 5%.
  - 1HZ30V: 20 top days, next-day t = -1.35.
  - R_75: 15 bottom days, next-day t = -0.61. That is the opposite sign to reflection.
- **Status.** No evidence of range management. A two-sided test needs a multi-year history the API
  does not serve.

## Vector 2: floating-point digit skew

- **Why only R_75 can test it.** A float64 step is 7e-8 pips even at R_75's 9 significant digits, so
  the only possible mechanism is float32. At R_75's spot one float32 step is about 39 pips.
- **Result: float32 is ruled out.**
  - All 39 residues of a 39-pip grid are occupied (chi2 p = 0.99).
  - In every spot decile, each last-digit share lies between 0.0983 and 0.1032, and the smallest
    p-value is 0.053.
- **Synthetic contrast.** Quotes computed in float32 at this magnitude leave some last digits at a
  0.0000 share.
- **1HZ30V agrees.** All ten deciles are uniform.
- **Status.** Closed. Quotes are computed and rounded in double precision.

## Vector 3: maintenance and time-of-day resets

- **The generator keeps running through feed gaps.**
  - 1HZ30V: 15 gaps, mean z^2 = 1.01 under "running" against 12.4 under "paused".
  - R_75: 1 gap.
  - Both symbols have a gap at exactly 2026-09-11 00:06:40 UTC, so the outage is shared
    infrastructure, not a per-index reseed.
- **No time-of-day volatility structure.** O2 p = 0.35 on 1HZ30V and 0.32 on R_75.
- **The midnight excess did not replicate.**
  - 1HZ30V: variance ratio 1.0116 (p = 0.003), which never passed Holm.
  - R_75: 1.0045 (p = 0.79).
- **Hurst proxy.** It is 0.50 inside and outside the midnight window on both symbols.
- **Accumulator bridge.** The quietest bin is 1.1% (1HZ30V) and 3.2% (R_75) below average volatility,
  against the 5.7% needed. That minimum is also noise-biased.
- **Status.** Closed.

## Verdict

No boundary leaks: no rebase, no reflecting drift in the testable direction, no float32 digit grid, no
maintenance or midnight anomaly. The generator keeps running during feed outages. Together with the
generator_probe_v3 results, the price stream cannot be told apart from an ideal driftless log-normal
walk at its stated volatility, on every axis tested.
