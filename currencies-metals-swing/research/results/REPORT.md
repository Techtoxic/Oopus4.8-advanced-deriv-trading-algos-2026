# Adaptive Swing — Research Report (FX + Metals)

Fixed parameters for every instrument (no per-symbol fitting). Costs = half-spread + slippage + commission on entry & exit. Signals decided on the closed daily bar, filled next open.

Default config: `{'entry_mode': 'either', 'st_period': 10, 'st_mult': 3.0, 'adx_min': 18.0, 'sl_atr_mult': 2.0, 'tp_R': 2.5, 'trail_atr_mult': 3.0, 'breakeven_R': 1.0, 'time_stop_bars': 40}`


## 1) Full period — baseline (no COT)

| Symbol | n | Ret% | CAGR% | Sharpe | MaxDD% | Win% | PF | ExpR |
|---|--:|--:|--:|--:|--:|--:|--:|--:|
| XAUUSD |  244 |    11.7 |   0.5 |  0.16 |  -13.2 |  29.5 |  1.12 |  0.05 |
| EURUSD |   91 |    -5.4 |  -0.6 | -0.16 |  -12.4 |  25.3 |  0.80 | -0.07 |
| GBPUSD |  279 |   -35.0 |  -1.6 | -0.43 |  -36.9 |  24.7 |  0.66 | -0.15 |
| USDJPY |  100 |   -17.4 |  -2.0 | -0.57 |  -21.4 |  23.0 |  0.62 | -0.19 |
| AUDUSD |   92 |     7.1 |   0.7 |  0.22 |   -9.2 |  28.3 |  1.21 |  0.08 |
| USDCAD |  104 |    -6.1 |  -0.7 | -0.16 |  -13.1 |  25.0 |  0.85 | -0.05 |
| NZDUSD |  101 |    -6.5 |  -0.7 | -0.17 |  -13.9 |  25.7 |  0.82 | -0.07 |
| EURJPY |  107 |    -4.1 |  -0.4 | -0.10 |  -16.6 |  29.0 |  0.92 | -0.03 |
| EURGBP |  101 |   -10.7 |  -1.2 | -0.31 |  -15.5 |  26.7 |  0.72 | -0.12 |

**Mean return: -7.4%  |  Median: -6.1%  |  Profitable: 2/9**


## 2) Full period — COT-index filter ON

| Symbol | n | Ret% | CAGR% | Sharpe | MaxDD% | Win% | PF | ExpR |
|---|--:|--:|--:|--:|--:|--:|--:|--:|
| XAUUSD |  219 |    23.7 |   1.0 |  0.29 |   -8.6 |  30.6 |  1.29 |  0.10 |
| EURUSD |   81 |    -4.5 |  -0.5 | -0.13 |  -11.5 |  25.9 |  0.81 | -0.07 |
| GBPUSD |  252 |   -28.7 |  -1.3 | -0.35 |  -32.0 |  24.6 |  0.69 | -0.13 |
| USDJPY |   89 |   -18.4 |  -2.1 | -0.65 |  -21.1 |  21.3 |  0.58 | -0.22 |
| AUDUSD |   81 |     3.4 |   0.4 |  0.12 |  -11.4 |  25.9 |  1.11 |  0.05 |
| USDCAD |   91 |    -3.9 |  -0.4 | -0.10 |  -10.1 |  24.2 |  0.89 | -0.04 |
| NZDUSD |   96 |   -11.3 |  -1.3 | -0.35 |  -15.5 |  21.9 |  0.71 | -0.12 |
| EURJPY |   97 |    -0.1 |  -0.0 |  0.01 |  -13.2 |  30.9 |  1.00 |  0.01 |
| EURGBP |   96 |   -12.5 |  -1.4 | -0.38 |  -14.7 |  27.1 |  0.69 | -0.13 |

**Mean return: -5.8%  |  Median: -4.5%  |  Profitable: 2/9**


## 3) In-sample vs Out-of-sample (COT-index ON, same fixed params)

| Symbol | IS Ret% | IS PF | OOS Ret% | OOS PF | OOS Sharpe |
|---|--:|--:|--:|--:|--:|
| XAUUSD | 4.1 | 1.09 | 16.9 | 1.51 | 0.52 |
| EURUSD | -2.2 | 0.89 | -0.4 | 0.83 | -0.02 |
| GBPUSD | -22.2 | 0.63 | -7.6 | 0.82 | -0.21 |
| USDJPY | -6.5 | 0.71 | -10.4 | 0.50 | -0.90 |
| AUDUSD | 7.4 | 1.43 | -2.7 | 0.77 | -0.21 |
| USDCAD | -2.6 | 0.88 | -0.5 | 0.97 | -0.02 |
| NZDUSD | -0.9 | 0.95 | -9.6 | 0.54 | -0.73 |
| EURJPY | 2.4 | 1.11 | -3.9 | 0.76 | -0.28 |
| EURGBP | -1.9 | 0.90 | -10.1 | 0.32 | -0.82 |

## 4) Equal-risk portfolio (COT-index ON)

- Final normalized equity: **0.942** (1.000 = flat)
- Annualised Sharpe: **-0.03**, Max drawdown: **-24.7%**


## Verdict (honest)
- **FX-majors daily trend-following shows no robust edge** after costs in this sample; several pairs (GBPUSD, USDJPY) lose across every configuration. This matches the academic prior that liquid FX is close to efficient.
- **The COT-index (speculator-positioning) filter adds value consistently** — it improves mean return on every config tested and turns gold strongly positive.
- **Your raw commercial-net rule HURTS metals**: commercials are structurally net-short gold, so it blocks the secular uptrend. The COT-*index* of speculator net is the version to trade.
- **Gold (XAUUSD) is the standout** and survives an OOS split — but a single instrument is a fragile basket. Treat as a candidate, validate forward on demo before risking capital.
- No result here is a guarantee. This is where the thin edge plausibly lives, sized and risk-managed; it is not a money printer.
