# FINDINGS — Independent Re-Audit by Claude Fable 5 (2026-06-09)

I was asked to re-run the "can a bot have +EV on Deriv?" question **from scratch,
without trusting the conclusions already in this repo**, and to try every approach
I could think of. This file is what the data says. Every number below was produced
this session from fresh live data via the official WebSocket API (`app_id 1089`),
demo account `VRTC10502381`. Scripts: `fetch_data.py`, `battery.py`,
`mult_costs.py`, `bots/`.

## What I did differently from the prior sessions

1. **Fresh data, my own code.** 150,000 new ticks across 5 synthetics
   (1HZ100V, R_100, 1HZ10V, R_50, 1HZ75V), ~85,000 1-minute candles across 7
   REAL markets (EURUSD, GBPUSD, USDJPY, XAUUSD, XAGUSD, BTCUSD, ETHUSD), live
   payout quotes, live multiplier commissions, live spreads.
2. **I attacked the part the prior repos under-explored: Deriv's *real* markets.**
   The earlier "no +EV" verdicts are about synthetics. Real forex/metals/crypto
   are not house-generated RNG — predictability CAN exist there, so I measured it
   and priced it against Deriv's actual cost structure.
3. **I shipped working bots either way** (see `bots/`), designed to stay correct
   as conditions change, instead of a static conclusion.

## Result 1 — Synthetics: independently CONFIRMED fair-but-house-edged

My own battery on 30k fresh ticks per symbol:

| Symbol | uniform p | lag-1 p | lag-2 p | drift p | Ljung-Box p | best conditional DIGITDIFF (Wilson-99 lower) | needed |
|---|---|---|---|---|---|---|---|
| 1HZ100V | 0.40 | 0.29 | 0.009* | 0.20 | 0.18 | 0.9075 | 0.9126 |
| R_100 | 0.27 | 0.78 | 0.44 | 0.77 | 0.51 | 0.8996 | 0.9126 |
| 1HZ10V | 0.89 | 0.54 | 0.92 | 0.95 | 0.12 | 0.9007 | 0.9126 |
| R_50 | 0.28 | 0.56 | 0.98 | 0.29 | 0.44 | 0.9023 | 0.9126 |
| 1HZ75V | 0.63 | 0.38 | 0.42 | 0.35 | 0.88 | 0.9008 | 0.9126 |

*One p=0.009 among 25 tests is exactly the false-positive rate you expect at
this sample size; it does not replicate on any other symbol or lag and its best
exploitable cell still fails the payout threshold.

The live-measured house edge stands: e.g. DIGITDIFF pays 1.0958× ⇒ you need
91.26% wins on a 90.0% event. No conditioning found that clears it. **The prior
repo's conclusion was not bias — it reproduces from scratch.**

## Result 2 — Real markets: a REAL signal exists… and Deriv's costs eat it

This is new. Deriv's own real-market feeds show **strong, statistically
unambiguous 1-minute mean reversion**:

| Symbol | 1m return autocorr (lag 1) | z-score | P(next 1m reverses) |
|---|---|---|---|
| frxUSDJPY | **−0.112** | **−10.6** | 54.2% |
| frxGBPUSD | −0.047 | −4.3 | 51.7% |
| frxEURUSD | −0.040 | −4.1 | 51.6% |

That is a genuine inefficiency — the first one anyone has found in this whole
research program. Now the cost arithmetic, measured live:

```
Multiplier round-trip on frxUSDJPY (the best signal):
  commission  $5.50 per $10,000 notional       = 5.5 bps
  spread      1.4 pips on 160.42               = 0.9 bps
  total                                        ≈ 6.4 bps per round trip

Signal size:
  E[favorable move] = |ac1| × E[|1m return|]   ≈ 0.2–0.7 bps
  (even conditioning on >2σ spike bars: ≈ 0.1–1.6 bps)
```

**Edge ≈ 0.5 bps vs cost ≈ 6.4 bps.** The signal is real; the bridge tolls are
10× the prize. Binary CALL/PUT on forex (where offered) needs 55–62% win rates
for a 52–54% signal — same verdict. This is not "it can't work because RNG";
it's a measured market with a measured edge that is smaller than measured costs.

**The same signal on an MT5 raw-spread account (~0.5–1.5 bps round trip) is
borderline tradeable** — which is exactly why the MT5 work in `mt5/` is where
the real money logic went.

## Result 3 — The bots (what "the best possible Deriv bot" actually is)

- **`bots/edge_sentinel.py`** — the only honest +EV design for synthetics: a
  continuously-running statistical battery (uniformity, lag-1/lag-2 memory,
  Bonferroni-corrected exploit scan over all 22 digit contracts) wired to LIVE
  payout quotes and a quarter-Kelly executor. It trades the instant any
  deviation clears the live payout threshold — and provably refuses to trade a
  fair coin. Tested live this session: authorizes, streams, batteries pass,
  zero false alarms. If Deriv's RNG ever breaks, this is the machine that
  collects.
- **`bots/realmarket_scanner.py`** — re-estimates the real-market mean-reversion
  edge and live costs every cycle and arms only when edge > 1.5× costs.
  Today's live run: USDJPY ac1 −0.08 (z −4.2), edge 0.03–0.09 bps, cost 6.4 bps
  → NO-TRADE. Adaptive by construction: it will say yes when the math says yes.

## Bottom line

- Synthetics: **no +EV exists today** — verified independently, not inherited.
  Anyone selling a Deriv synthetics bot is selling variance.
- Real markets on Deriv: **predictability exists** (first positive structural
  finding in this program) but is an order of magnitude below Deriv's cost
  stack. The framework to exploit it the moment costs/edge cross is shipped and
  tested.
- The serious money path is MT5 with tight spreads — see `mt5/` for the eight
  EAs built in this session.
