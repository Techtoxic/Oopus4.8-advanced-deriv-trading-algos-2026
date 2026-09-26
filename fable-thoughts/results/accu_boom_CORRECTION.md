# H5b STATUS: UNKNOWN (was prematurely marked dead)

`accu_boom.py` returned **G > 1 in 70/70 cells**, best BOOM900 @ g=0.05 with G=1.04909.
An earlier version of this note declared the hypothesis dead based on Deriv's
`contract_details.ticks_stayed_in`. **That conclusion was withdrawn.** It does not survive
scrutiny. Status is UNKNOWN pending direct measurement via `accu_holdtest.py`.

## Why the kill was wrong

### 1. The "first-passage" explanation was fitted, not tested

The claim was that the accumulator dies when *cumulative* drift exits an absolute band, so
mean survival ~ (band/step)^2. The step sd was back-solved from Deriv's mean at g=0.05, and
the resulting match (19.1 vs 19.16) was presented as confirmation. That is circular.

Tested across the full growth-rate range, it fails:

| g | tsb | Deriv mean | first-passage predicts | error |
|---|---:|---:|---:|---:|
| 0.01 | 4.331e-05 | 69.51 | 30.41 | **2.29x** |
| 0.02 | 4.048e-05 | 37.50 | 26.56 | 1.41x |
| 0.03 | 3.797e-05 | 29.95 | 23.37 | 1.28x |
| 0.04 | 3.612e-05 | 22.70 | 21.15 | 1.07x |
| 0.05 | 3.438e-05 | 19.16 | 19.16 | 1.00 (fitted) |

Mean should scale as tsb^2 -> 1.59x spread from g=.01 to g=.05. Observed: 3.63x.

### 2. The per-tick model also fails

Predicts mean 1031-1149 ticks vs Deriv's 19-70, and a g=.01/.05 ratio of 1.11 vs
observed 3.63.

**Neither model fits.** That is evidence about the field, not the barrier.

### 3. `ticks_stayed_in` is not clean barrier-breach data

- `maximum_ticks` = 50 on both symbols, but the samples contain **104, 98** (BOOM900) and
  **99** (R_100). Contracts cannot survive past the cap.
- R_100's sample contains a **0**. A zero-tick contract did not breach a barrier.

Likely legacy statistics, or a mix that includes voluntary early sells. Either way it cannot
serve as ground truth for P(survive one tick).

## What IS established

| finding | status |
|---|---|
| `tick_size_barrier` is the relative barrier | CONFIRMED (rel_hi/tsb = 1.0001 BOOM900, 1.0029 R_100) |
| barrier static intraday | CONFIRMED (range 0.000e+00 over 6x20s) |
| spike decomposition accurate | CONFIRMED (recovered 50/159/345/462/638/1111/937 vs named 50/150/300/500/600/900/1000) |
| survival mechanic | **UNKNOWN** |
| G < 1 | **NOT ESTABLISHED** |

## The decisive test

`accu_holdtest.py` — buy on demo, never sell, record actual survival. Decision metric is the
hold-to-N EV factor from the empirical survival curve:

    EV_factor(N) = (1+g)^N * S(N)

The hypotheses are far apart, so ~25 contracts suffices:

| hypothesis | S(50) | EV factor at N=50 |
|---|---:|---:|
| accu_boom (p=0.999) | 0.9526 | **10.92 (+992%)** |
| ticks_stayed_in (mean 19.16) | 0.0686 | 0.79 (-21%) |

~14x apart at N=50.

## Process note

The premature kill came from anchoring on the prior that eight of nine hypotheses in this
repo came back correctly priced, then accepting the first piece of confirming evidence
without testing the explanation that supported it. The correct order is: test the model
across its full parameter range BEFORE using it to close a question. A prior that strong
should raise the bar for confirming evidence, not lower it.
