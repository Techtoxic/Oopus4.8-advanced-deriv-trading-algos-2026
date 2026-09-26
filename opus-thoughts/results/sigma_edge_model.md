# JD100 sigma -> conditional edge model

sample: 1000000 ticks, spot 233.00..264.79 (last 262.03), sigma_nojump = 4.44 pips, jump frac = 0.0007

Contract: UNDER5 bought when current digit = 2 (window {0..4} = d±2), or OVER4 when d = 7.
Break-even P = 1/1.953 = 0.5120. EV = P×1.953−1.

| sigma_pips | spot(JD100) | P_theory(normal) | P_bootstrap | EV/trade |
|---:|---:|---:|---:|---:|
| 2.0 | 112 | 0.7889 | 0.7896 | +54.21% |
| 2.5 | 140 | 0.6854 | 0.6877 | +34.31% |
| 3.0 | 168 | 0.6077 | 0.6095 | +19.03% |
| 3.5 | 196 | 0.5567 | 0.5567 | +8.71% |
| 4.0 | 224 | 0.5271 | 0.5273 | +2.97% |
| 4.3 | 241 | 0.5166 | 0.5178 | +1.13% |
| 4.6 | 258 | 0.5098 | 0.5109 | -0.22% |
| 5.0 | 281 | 0.5046 | 0.5036 | -1.65% |
| 5.5 | 309 | 0.5016 | 0.5018 | -2.00% |
| 6.0 | 337 | 0.5005 | 0.4990 | -2.54% |
| 7.0 | 393 | 0.5000 | 0.5005 | -2.26% |
| 8.0 | 449 | 0.5000 | 0.5006 | -2.24% |

Empirical calibration (chunked 200k sample): sigma 4.28 -> P(U5|d2)=0.532, sigma 4.54 -> 0.518; bootstrap column should bracket these.

**Regime gate**: trade only when sigma_nojump(rolling) <= ~4.3 pips (JD100 spot <= ~241). Today: sigma=4.44, spot=262.03.

lag-2 (missed tick) kills it: measured P(U5|d2)@lag2 = 0.504 -> EV −1.5%. Execution must land inside the 1s gap (single buy-with-parameters call, no proposal round-trip).
