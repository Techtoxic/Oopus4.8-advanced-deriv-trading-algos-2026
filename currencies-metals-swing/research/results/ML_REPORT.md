# ML Trade Filter — Purged Walk-Forward

- OOS signals scored: **4030**
- OOS AUC: **0.486** (0.50 = no skill)

| Selectivity (take top %) | Trades | Win% | Mean R | vs take-all |
|---|--:|--:|--:|--:|
| top 100% | 4030 | 34.9 | 0.008 | +0.000R |
| top 70% | 2821 | 34.2 | -0.015 | -0.023R |
| top 50% | 2015 | 34.1 | -0.024 | -0.031R |
| top 30% | 1209 | 33.3 | -0.055 | -0.063R |
| top 20% | 806 | 33.6 | -0.039 | -0.047R |

**Gold only:** take-all mean R=0.138 (n=762); top-50% mean R=0.096 (n=381)

## Read (this run)
**Honest result: no usable ML edge here.** OOS AUC is 0.486 (~0.50 = coin flip) and being more selective did **not** raise expectancy — the classifier is fitting noise, not signal. This is the same lesson as the Deriv work: most 'AI trade filter' claims evaporate under purged walk-forward. **Do not ship the ML filter as-is.** The durable signal is the simple, transparent one — trend regime + COT-index positioning, concentrated in gold. More data, richer features (intermarket, rates, real order flow) or a different label might change this; raw price+COT features did not.
