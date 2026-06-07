# MASTER REPORT — Does any +EV exist on Deriv synthetic indices?

**Account:** `VRTC10502381` (demo, 10,000 USD virtual). **Method:** official Deriv WebSocket API only —
real tick data (`ticks_history`), real pricing (`proposal`), real live demo execution (`buy`).
**Data volume:** 50,000+ ticks for the digit battery, 30,000-tick paginated samples for Boom/Crash,
100 real accumulator survival records per market, plus live demo trade batches.

> **Bottom line up front:** No positive expected value was found in any contract family. The synthetic
> RNG is statistically indistinguishable from IID randomness, and every contract carries a structural
> house edge that no observable signal can overcome.

## 1. Verdict table

| Contract type | Edge found? | Expected value (house edge) | Confidence | Why |
|---------------|:-----------:|-----------------------------|:----------:|-----|
| Digit Over/Under | ❌ | −1.36% … −16.67% (favourite–longshot) | Very high | Uniform digits, zero memory |
| Digit Even/Odd | ❌ | −2.35% … −3.85% | Very high | 50/50, runs-test clean |
| Digit Matches | ❌ | −10.71% … −16.67% | Very high | 10% hit, "overdue" is a myth |
| Digit Differ | ❌ | −1.36% … −2.17% | Very high | Mirror of Matches |
| Accumulators | ❌ | −0.50% … −0.60% **per tick** (compounds) | Very high | (1+g)·p < 1, memoryless breach |
| Multipliers | ❌ | −commission − spread (≈ −0.01%…−0.04% notional) | Very high | Direction unpredictable |
| Boom/Crash | ❌ | via multipliers; spikes unpredictable | High | Memoryless geometric spike timing |
| Higher/Lower (Rise/Fall) | ❌ | −3.85% ATM | Very high | Return sign is a fair coin |
| Touch/No-Touch | ❌ | −edge (barrier priced below fair) | High (modelled) | Driftless, stationary vol |
| Vanillas | ❌ | −margin (IV = RV + margin) | High (modelled) | Issuer knows/sets RV; long-only |

## 2. Best opportunity found

**None is +EV.** The single **least-negative** play is the high-probability digit family —
**`DIGITDIFF` / `DIGITOVER` barrier 0 / `DIGITUNDER` barrier 9 on 1HZ100V**, house edge **1.36%**.
It is still a guaranteed long-run loss: outcomes are memoryless, so there is no condition under which its
EV becomes positive. It is "best" only in the sense of losing slowest.

## 3. Recommended bot

**No +EV edge exists, so no profit-seeking bot is justified.** Shipping one would be selling a guaranteed
loss. What *is* included is an **edge-monitor** (`shared/utils/conditional_strategies.py` +
`phase1_digits.py`): it re-runs the full randomness battery on fresh live ticks and would flag if the RNG
ever deviated from uniform/independent (chi-square, transition, autocorrelation, FFT). If — and only if —
those tests ever showed a large, consistent, replicating deviation, the EV math in each phase report shows
exactly which contract and barrier would become profitable and by how much. Until then, the correct bot is
the one that doesn't trade.

## 4. N+2 TICK MASTER FINDING

**Mechanics (mapped live from each contract's `entry_tick_time`/`exit_tick_time`):** entry tick = **N+1**;
settlement tick = **N + duration**. So a 1-tick contract settles on **N+1**, a 2-tick on **N+2**, a 5-tick
on **N+5**. The belief that "N+1 is a throwaway server-processing tick and you really predict N+2" is
**false** for the standard 1-tick contract — you predict the very first tick after the one you saw.

**Does N+1 carry ANY information about N+2?** **No — across every test, every market:**

- **Lag-1 transition χ²** (does digit N predict digit N+1): p = 0.43–0.96 in 9/10 markets (one lone 0.027).
- **Lag-2 transition χ²** (does N+1 predict N+2, i.e. the digit one further on): p = 0.11–0.94, **all non-significant**.
- **Digit autocorrelation** at lags 1 and 2: every value inside ±0.028 (white-noise band), no consistent sign.
- **Predict-lag χ²** at lags 1,2,3,5,10,20 across 10 markets = 60 tests: only 3 below 0.05 (R_10 lag10,
  R_25 lag5, R_75 lag1), scattered and non-replicating — exactly the false-positive rate.
- **P(N+2 digit = N+1 digit)** = 0.089–0.108 ≈ 0.10, indistinguishable from independent uniform.

**Conclusion:** consecutive ticks are statistically independent. Knowing the value of N, N+1, or any prior
tick provides **zero** information about the payout-determining tick. This is the single most important
finding: it is the mathematical reason no digit/Rise-Fall/Touch strategy can be +EV.

## 5. Honest conclusion

- **Closest to fair:** the `p=0.9` digit family on 1HZ100V at **1.36%** — the smallest edge offered anywhere.
- **Minimum house edge overall:** ~**1.36%** (1HZ100V Over-0/Under-9/Differ). Accumulators are nominally
  ~0.5%/tick but compound, making them worse over any realistic hold.
- **Came closest to a signal:** nothing genuinely did. The most "interesting" structure is Boom/Crash spike
  timing — but it is provably memoryless (KS-vs-geometric p > 0.05, CV ≈ 1, interval autocorrelation ≈ 0),
  so it is interesting, not exploitable.
- **Is continued research worth it?** Only one avenue is even theoretically open: the synthetic RNG is a
  seeded CSPRNG; if a seed/sequence weakness existed it would show as deviation from uniform/independent in
  exactly the tests run here. 50,000+ ticks show none. To detect an *extremely* faint bias (edge < ~0.3%)
  you would need **millions** of ticks and it still wouldn't beat a 1.36%+ edge. **Not worth pursuing for
  profit.** The honest expected outcome of more data is a tighter confirmation of "no edge."
- **Hypotheses needing more data to fully close:** (a) ultra-low-frequency PRNG cycles beyond a few-thousand-
  tick FFT window; (b) cross-market seed correlation (V10/V25/V50 simultaneous streams) — preliminary checks
  show independent streams; (c) micro-timing/latency artifacts in live execution. None showed a hint of edge
  in the data collected; all would need to overcome a ≥1.36% edge even if a faint signal existed.

## Per-phase detail

[Phase 1 — Digits](../phase-1-digits/README.md) ·
[Phase 2 — Accumulators](../phase-2-accumulators/README.md) ·
[Phase 3 — Multipliers](../phase-3-multipliers/README.md) ·
[Phase 4 — Boom/Crash](../phase-4-boom-crash/README.md) ·
[Phase 5 — Higher/Lower](../phase-5-higher-lower/README.md) ·
[Phase 6 — Touch/No-Touch](../phase-6-touch-no-touch/README.md) ·
[Phase 7 — Vanillas](../phase-7-vanillas/README.md)

## 6. Live demo execution confirmation (real trades, 1HZ100V, 1-tick)

600 live demo trades, settled deterministically from the payout-determining exit tick and reconciled
against the account balance.

| Contract | n | realized win rate | theory | z | binomial p | account P/L | verdict |
|----------|--:|------------------:|-------:|--:|-----------:|------------:|---------|
| DIGITEVEN | 300 | **50.67%** | 50% | +0.23 | 0.86 | **−4.08 USD** | fair coin, account loses |
| DIGITOVER-0 | 300 | **91.33%** | 90% | +0.77 | 0.50 | **−5.84 USD** | 91% wins, **still loses** |

Realized win rates are statistically indistinguishable from the theoretical probabilities (both binomial
p ≫ 0.05). **Both contracts lost real money**, and critically the DIGITOVER-0 contract **won 91% of its
trades yet still lost** — the textbook demonstration that a high hit-rate is not an edge when the payout is
set below fair. Net account change across the session: ~10,000 → ~9,985 USD over ~670 trades.

### Practical finding — minimum-stake payout rounding *increases* the edge

Deriv rounds contract payouts **down to 2 decimals**, which is punitive at small stakes:

| Contract | payout @ $0.50 stake | effective edge | payout @ $10 stake | headline edge |
|----------|---------------------:|---------------:|-------------------:|--------------:|
| DIGITOVER-0 | 0.53 (×1.06) | **4.60%** | 10.96 (×1.096) | 1.36% |
| DIGITEVEN | 0.96 (×1.92) | **4.00%** | 19.53 (×1.953) | 2.35% |

So the live batches (run at the $0.50 minimum) actually faced a **~4% edge**, which is exactly why the
account lost ~3% of turnover. To even approach the headline 1.36–2.35% edge you must stake larger amounts
(~$10+). This makes small-stake grinding strictly worse than the already-negative headline numbers — a
real, actionable result for anyone considering these contracts.

*Raw data: [`../../digits/data/live_even_1HZ100V.csv`](../../digits/data/live_even_1HZ100V.csv),
[`../../digits/data/live_over0_1HZ100V.csv`](../../digits/data/live_over0_1HZ100V.csv).*
