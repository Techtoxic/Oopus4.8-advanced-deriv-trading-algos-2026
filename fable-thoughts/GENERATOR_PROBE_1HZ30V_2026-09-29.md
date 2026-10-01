# generator_probe_v2 on 1HZ30V: 9M ticks, 104 days (2026-06-17 to 2026-09-29)

Run on the user's machine from a cached 9,000,000-tick fetch. Read-only.

## Data and model

- **Block 0 (integrity): OK.**
  - 9,000,000 unique epochs, none out of order.
  - 15 gaps, the largest 94 s.
  - No prices off the pip lattice, no replayed 1000-tick paths.
- **Block J (model fit): exactly the published model.**

  | quantity | value |
  |---|---|
  | measured / nominal per-tick sigma | 0.99998 (5.3421e-5 vs 5.3422e-5) |
  | price drift t | -1.15 (p 0.25) |
  | excess kurtosis of log-returns | -0.0015 |

## Dependence tests

**Battery (25 tests, calibrated against the spot-scaled null, Holm 1%): nothing flags.**

- The v1 run flagged volatility clustering (abs and sq LB20, p = 0) in both halves. Against the
  spot-scaled null those tests give p = 0.78 and 0.79. The flag was step size in pips following spot
  over 104 days, not memory in the generator.
- The smallest p-values are nist_frequency 0.017, digit_marginal 0.022 and VR2 0.029. Across 25 tests,
  that is what chance produces.
- Block G's "diffusion vs rounded normal" (p = 3e-16) is the same spot-mixture artefact. That block is
  not null-calibrated; block J's kurtosis on log-returns is the correct check.

## Information and prediction

- **Block K (information): 0.000000 bits above the shuffled 99th percentile at every order.**
  - This holds for moves k = 1-4 and digits k = 1-3.
  - The break-even for a 1.923 payout is 0.001157 bits.
- **Block L (out of sample, 2.7M trades each, T+1 timing):**
  - Hit rates are 0.4991-0.5002.
  - EVEN/ODD EV is -3.8%, the house margin at 1.923.
  - RISE/FALL EV is -2.4% to -2.5%, at the placeholder payout 1.953.
  - No rule has a positive 99% lower bound.
- **Power: both planted controls were found.**
  - K: +621% of break-even.
  - L: +7.2% EV, 99% interval [+6.9%, +7.5%].

## Verdict

1HZ30V behaves as an iid Gaussian random walk at exactly its stated volatility. That is what a
correctly implemented generator (CSPRNG or not) produces, observed through a scaled, rounded price.
There is no statistical edge in its history. Any edge on this symbol would have to come from pricing,
not prediction.

## Addendum: generator_probe_v3 on the same cache (32 tests)

All five added tests are null against the spot-scaled simulated null:

| test | calibrated p |
|---|---|
| A2 digit Markov, 1st order | 0.145 |
| A2 digit Markov, 2nd order | 0.328 |
| D2 leverage | 0.646 |
| E2 block variance | 0.93-0.94 |
| F2 run lengths | 0.689 |

- **E2 is the clearest calibration lesson in the run.** Against the textbook formula all three block
  sizes give p = 0, because block variances in pips follow spot over 104 days. Against the
  spot-scaled null they give p = 0.93-0.94.
- **The planted control flags E2 (block 128) and F2,** so both have power.
- **Caveat: D2 and A2 have no positive control in this run.** The planted GARCH is symmetric, so it
  has no leverage effect, and it carries no digit structure. For those two tests, a null result shows
  there is nothing large, not that the tests could see something small.

**Verdict unchanged.** No test survives Holm correction, the information bound is zero, and no rule
has positive EV out of sample.

## Addendum: boundary_probe on the same ticks plus 366 daily candles

- **M (macro).**
  - The API returned only 366 daily candles (2025-09-29 to 2026-09-29), so "lifetime" here means one year.
  - No rebase: the largest overnight jump is 3.1 tick-sigmas.
  - Lifetime simple drift t = 0.07.
  - The reflection test cannot run: spot never sat in the bottom 5% of its running range, and the 20
    top-5% days give a next-day t of -1.35.
  - M4 (volatility at extremes) calibrated p = 0.18.
- **N (number format).** Last digit and last two digits are uniform in every spot decile. The float32
  test does not apply: at 7 significant digits one float32 step is 0.49 pips.
- **O1 (feed gaps).** There are 15 gaps, 12 of them between 00:00 and 09:00 UTC. The price moved
  through them like a walk that kept running (mean z^2 = 1.01), not one that paused (12.4). The 94 s
  gap on 2026-07-28 moved 11 tick-sigmas, against sqrt(94) = 9.7 expected. So the outages are delivery
  gaps: the generator does not stop.
- **O2 (time-of-day volatility).** The profile is flat: dispersion z = 0.40, p = 0.35. The quietest
  5-minute bin is 1.1% below average, against the 5.7% an accumulator at g = 1% would need.
- **O3 (midnight window).** Variance ratio 1.0116 (z = 2.99, p = 0.0028). This is not significant after
  Holm (threshold about 0.0004 for 26 tests). If real, it is 0.6% more volatility near midnight: no
  direction, and the wrong sign for accumulators. It is noted for replication on the next fresh sample
  and not acted on.
- **Verdict.** No boundary test survives correction.
