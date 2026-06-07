# Methodology & Statistical Test Suite

All tests operate on **real data pulled live from the Deriv API** (demo account `VRTC10502381`).

## Data sources

- **`ticks_history`** — up to 5,000 historical ticks per call, paginated (via `end`) for larger windows.
  Returns the same tick stream the contracts settle on. Used for distribution/randomness/conditional tests.
- **`proposal`** — live contract pricing (payout, commission, barrier band, `ticks_stayed_in`). Used for
  exact house-edge and accumulator survival data.
- **`buy` + `proposal_open_contract`** — live demo execution; records entry/exit ticks, settlement, P/L.
- **`balance`** — ground-truth P/L cross-check (every live batch's net matches balance delta exactly).

## Last-digit computation

Deriv DIGIT contracts use the last digit of the spot **formatted to the symbol's pip precision**
(decimals = `-log10(pip)` from `active_symbols`). E.g. R_100 → 2 dp, R_10/R_25 → 3 dp, R_50/R_75 → 4 dp,
1HZ* → 2 dp. Using the wrong precision silently corrupts every digit test, so precision is derived
per-symbol, not assumed.

## Tests (`shared/stats/tests.py`)

| Test | What it detects | Pass = random |
|------|-----------------|---------------|
| Chi-square uniformity | non-uniform digit frequencies | p > 0.05 |
| Shannon entropy | sub-maximal information content | ≈ log2(10) = 3.3219 bits |
| Wald–Wolfowitz runs | clustering / alternation in a binary stream | p > 0.05 |
| Autocorrelation (lags 1–20) | serial dependence | within ±1.96/√N |
| Lag-1 serial correlation (Fisher z) | linear digit dependence | p > 0.05 |
| Transition chi-square (10×10) | "previous digit predicts next" (N→N+1) | p > 0.05 |
| Predict-lag chi-square (lags 1–20) | any earlier digit predicts current | p > 0.05 |
| FFT periodogram | PRNG cycles / periodicity | peak power ≈ ln(N/2)×mean |
| Binomial tests (conditional strategies) | streak/window/overdue edges | p > 0.05 vs correct baseline |
| KS vs geometric (Boom/Crash, accumulators) | memoryless vs structured timing | p > 0.05 |

## Interpreting multiple comparisons

Dozens of tests run across 10 markets × 6 lags. At α = 0.05 roughly 1-in-20 will flag by pure chance.
A result is treated as **real edge only if it is large, consistent across markets, consistent in sign,
and replicates on fresh data** — not if a lone p-value dips below 0.05 amid many tests. Every borderline
flag in this study failed at least one of those conditions.

## N / N+1 / N+2 settlement mechanics (mapped empirically)

From each contract's own `entry_tick_time` / `exit_tick_time` vs the tick visible at purchase (N):

| duration | entry tick | settlement (payout) tick |
|---------:|-----------:|-------------------------:|
| 1 tick  | N+1 | **N+1** |
| 2 ticks | N+1 | **N+2** |
| 5 ticks | N+1 | **N+5** |

Rule: **entry = N+1, settlement = N + duration.** For the standard 1-tick digit contract you predict
**N+1** (the first tick after the one you saw) — there is no "free" intermediate processing tick.
