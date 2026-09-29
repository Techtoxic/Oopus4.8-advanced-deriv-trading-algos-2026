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
