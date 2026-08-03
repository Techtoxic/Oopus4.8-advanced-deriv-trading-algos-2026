# H5b CORRECTION — BOOM/CRASH accumulator "G>1" was a modelling error

`accu_boom.py` returned **G > 1 in 70/70 cells**, best BOOM900 @ g=0.05 with G=1.04909.
That is **not an edge**. Verified dead via `accu_verify.py`.

## Ground truth: Deriv's own `ticks_stayed_in`

The ACCU proposal response carries `contract_details.ticks_stayed_in` — Deriv's published
distribution of contract survival. `mean/(1+mean)` implies their p.

| symbol | g | mean ticks | p_deriv | **G_deriv** |
|---|---:|---:|---:|---:|
| BOOM900 | 0.01 | 69.51 | 0.98582 | 0.99568 |
| BOOM900 | 0.02 | 37.50 | 0.97403 | 0.99351 |
| BOOM900 | 0.03 | 29.95 | 0.96769 | 0.99672 |
| BOOM900 | 0.04 | 22.70 | 0.95781 | 0.99612 |
| BOOM900 | 0.05 | 19.16 | 0.95040 | 0.99792 |
| R_100 | 0.01 | 61.50 | 0.98400 | 0.99384 |
| R_100 | 0.05 | 17.32 | 0.94541 | 0.99269 |

Mean house margin: **BOOM900 0.401%, R_100 0.400%** — identical to three decimals.
Same pricing engine, correctly calibrated. Matches opus's `accumulator_rtp.md` finding
on the Gaussian symbols.

## The error

`accu_boom.py` modelled survival as an **i.i.d. per-tick test**: survive iff
`|return_t| <= tick_size_barrier`. The real accumulator is a **first-passage problem** —
the band is absolute and the contract dies when *cumulative* drift exits it.

These diverge enormously when steps are small relative to the band. BOOM900 @ g=0.05:

- band = ±0.331 price units (rel 3.438e-05), spot 9626.205
- implied step sd = 0.331/sqrt(19.16) = **0.0756 units** (rel 7.86e-06)
- one step is 0.23 of the band -> P(single-step breach) = **1.2e-05** -> measured p = 0.99913
- cumulative exit time = (0.331/0.0756)^2 = **19.1 ticks** -> true p = 0.95040

Deriv's published mean is 19.16. **The reconciliation is exact.** Both measurements were
correct; they answered different questions.

## Units check (section B of accu_verify.py)

`tick_size_barrier` *is* the relative barrier — confirmed against `high_barrier`/`low_barrier`:
rel_hi/tsb = 1.0001 (BOOM900), 1.0029 (R_100). The units were never the problem.

Also: tsb is **not** uniform across all symbols as first suspected. BOOM900 3.44e-05 vs
R_100 4.86e-04 — 14x apart. It is constant within the BOOM/CRASH family only, which is
legitimate if their drift vol is similar and only spike frequency differs.

## Barrier stability

Static over 6x20s (range exactly 0.000e+00). Single-snapshot scans are valid; the barrier
is not vol-responsive intraday.

## What was sound in accu_boom.py

The tick fetch and the MAD spike decomposition. Recovered spike periods 50/159/345/462/638/
1111/937 against symbols named 50/150/300/500/600/900/1000 — accurate enough to reuse.

## Lesson

Cross-check against the counterparty's **own published statistics** before believing a model.
`ticks_stayed_in` was in the proposal response the entire time. Cost of the check: ~3 minutes,
zero dollars. Cost of skipping it: a month of paper trading a phantom edge.

## Scoreboard

Nine structural hypotheses tested across this repo; one real (JD100 digit window, killed by a
book-wide reprice within two months of being traded at size). H5b joins the correctly-priced
column. Reset-spanning contracts (H-C) confirmed disallowed by the exchange.
