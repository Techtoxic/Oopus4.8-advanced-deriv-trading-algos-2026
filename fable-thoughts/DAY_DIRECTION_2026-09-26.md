# Is a day's direction decided in advance? (2026-09-26)

## The question

A reader asked whether each day's net direction on Deriv synthetics is decided in advance, with the
randomness "veered" toward it, so that spikes or quiet days relate to time of day.

## Why the test looks for a tilt

A close decided in advance but drawn from the same distribution a free random walk would reach leaves
no trace: a Brownian bridge to a normal endpoint is exactly Brownian motion. The claim only matters if
the decision is biased. A bias shows up as one of four signatures:
- **H1:** a drift;
- **H2:** a variance ratio above 1 (hours within a day lean together);
- **H3:** early hours predicting the rest of the day;
- **H4:** direction or volatility depending on the hour of day.

## Method

`tools/day_direction.py`, run on 364 complete UTC days of hourly candles per symbol. Each statistic is
tested against 23,400 permutations of hours across days, with a Bonferroni threshold of 1.28e-4 over
78 tests. RDBULL and RDBEAR are the positive controls, because they have a built-in daily drift by design.

## Result

| | P(day up) | drift t | VR | half r | early r | hour F | vol F | flags |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| RDBULL (control) | 0.821 | +20.13 | 1.103 | −0.013 | −0.063 | 1.24 | 0.94 | H1 |
| RDBEAR (control) | 0.253 | −11.92 | 1.073 | +0.011 | +0.007 | 0.73 | 1.19 | H1 |
| 11 other indices | 0.453–0.536 | −1.50…+0.86 | 0.929–1.125 | −0.103…+0.074 | −0.023…+0.094 | 0.59–1.46 | 0.59–1.53 | none |

The 11 other indices are CRASH1000/500/300N, BOOM1000/500/300N, 1HZ100V, R_100, 1HZ10V, JD100 and stpRNG.

- **Both controls detect their drift**, so the tool can see a real lean.
- **Nothing else flags.** The smallest p outside the controls is 0.036 (stpRNG early r), well above the
  1.28e-4 threshold.
- **Hours within a day don't lean together.** VR stays within 1 ± 0.13, and a planted per-day lean
  gives more than 1.5.
- **The first half of a day doesn't predict the second.** Half-day correlations are within about
  2 SE (0.05) of zero.
- **No hour-of-day effect** on direction or on spikes/volatility on Boom/Crash.

## BOOM300N

BOOM300N fell from about 1004 in June to 402. Its drift t on simple returns is −1.50, not significant.
The decline is consistent with a zero-drift price plus volatility drag (P(day up) 0.453, the median
below the mean), not with a built-in bearish direction.

## Correction to SESSION_2026-08-18b

That session called RDBULL's per-hour drift shape "real". Here the hour-of-day F is 1.24 (p 0.20):
the shape across hours can't be told apart from noise. What is real is the drift itself, which is the
same in every hour.

## Verdict

Across 364 days and 11 indices, days behave like independent random walks with no decided direction.
The only built-in direction on the book, RDBULL/RDBEAR, is designed that way, is published, and is
priced into the contracts.
