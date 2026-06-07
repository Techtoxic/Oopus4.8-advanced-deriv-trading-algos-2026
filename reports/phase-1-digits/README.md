# Phase 1 — Digit Contracts (Over/Under, Even/Odd, Matches, Differs)

**Markets tested:** R_10, R_25, R_50, R_75, R_100 and 1HZ10V/25V/50V/75V/100V — **10 volatility indices**.
**Data:** 5,000 real ticks per market (50,000 total) via `ticks_history`, plus live demo execution.
**Contracts:** `DIGITOVER`, `DIGITUNDER`, `DIGITEVEN`, `DIGITODD`, `DIGITMATCH`, `DIGITDIFF` (1–10 tick).

## Headline

Every market's last-digit stream is **statistically indistinguishable from IID Uniform(0–9)**. No
distribution skew, no serial dependence, no periodicity, no conditional structure. Therefore **no digit
strategy can have +EV** — the outcome is independent of everything observable, and only the house edge
remains. The closest-to-fair digit contract is the `p=0.9` family (Over-0 / Under-9 / Differ) at
**1.36% edge on 1HZ100V**, still a guaranteed long-run loss.

## 1. Randomness battery (5,000 ticks/market)

| Test | Result across 10 markets | Verdict |
|------|--------------------------|---------|
| Chi-square uniformity | p = 0.13 – 0.98 (all > 0.05) | uniform ✓ |
| Shannon entropy | 99.94% – 99.99% of max (log₂10 = 3.3219 b) | maximal ✓ |
| Digit autocorrelation lags 1–20 | all within ±0.028 white-noise band | no memory ✓ |
| Lag-1 transition χ² (N→N+1, 10×10) | 9/10 p > 0.05 (R_75 0.027, lone) | independent ✓ |
| P(next = current) repeat rate | 0.089 – 0.108 (≈ 0.10) | memoryless ✓ |
| Even/odd runs test | all p > 0.21, splits ~50/50 | random ✓ |
| FFT periodogram (PRNG cycles) | peak power 7.4–9.5× mean ≈ ln(N/2); period differs every market | white noise, **no cycle** ✓ |

The single sub-0.05 transition flag (R_75, p = 0.027) is exactly what 10 independent tests produce by
chance at α = 0.05 and does not replicate in direction or across lags.

## 2. House edge (exact, from live proposal pricing, $10 stake)

The edge follows a **favourite–longshot ladder** — it scales with payout, lowest on high-probability bets:

| Contract family | p_win | edge range (across R_10 / R_100 / 1HZ100V) |
|-----------------|------:|--------------------------------------------|
| Over-0 / Under-9 / **Differ** | 0.9 | **1.36% – 2.17%** (lowest available) |
| Even / Odd | 0.5 | 2.35% – 3.85% |
| Over-4 / Under-5 | 0.5 | 2.35% – 3.85% |
| Over-7 / Under-2 | 0.2 | 5.66% – 9.10% |
| Over-8 / Under-1 | 0.1 | 10.71% – 16.67% |
| **Matches** (any barrier) | 0.1 | **10.71% – 16.67%** (steepest) |

1-second indices (1HZ*) are consistently cheaper than the 2-second R_* indices. Full table:
[`../../digits/CONTRACT_HOUSE_EDGE.md`](../../digits/CONTRACT_HOUSE_EDGE.md).

## 3. Every proposed strategy, tested on real ticks (thousands of conditioned events)

| Strategy (mission step) | Baseline | Observed | Verdict |
|-------------------------|---------:|---------:|---------|
| **Gambler's fallacy** — P(under \| k consecutive overs), k=3,5,7,10 (1A.3) | 0.500 | 0.41–0.57, scattered | **no edge** |
| **Frequency window** — fade side >60% in last 20 (1A.4) | 0.500 | 0.489–0.521, p>0.16 | **no edge** |
| **Momentum** — P(over \| k overs) (mirror) | 0.500 | 0.42–0.54, scattered | **no edge** |
| **Overdue digit MATCH** — coldest of last 50 (1C.1) | 0.100 | **0.094–0.101**, p>0.14 | **no edge** (need 0.141 to profit) |
| **Even/odd alternation** (1B) | random | runs p>0.21 all markets | **no edge** |

Sub-0.05 flags appear at exactly the multiple-comparisons rate (~3 in 40 tests), inconsistent in sign and
non-replicating. **No conditioning variable shifts the next-digit distribution.**

## 4. N / N+1 / N+2 tick mechanics (mapped live, ground truth)

From each contract's own `entry_tick_time` / `exit_tick_time` vs the tick visible at purchase (N):

| duration | entry tick | **settlement (payout) tick** |
|---------:|-----------:|-----------------------------:|
| 1 tick | N+1 | **N+1** |
| 2 ticks | N+1 | **N+2** |
| 5 ticks | N+1 | **N+5** |

**Rule: entry = N+1, settlement = N + duration.** For the standard 1-tick digit contract you predict
**N+1** — the first tick after the one you saw. There is **no free "server-processing" tick**; the premise
that "N+1 doesn't count and you predict N+2" only holds if you always trade 2-tick contracts.

**Does N+1 carry information about N+2?** No. The lag-2 transition χ² is non-significant in all 10 markets
(p = 0.11–0.94), digit autocorrelation at lag 2 is within the white-noise band, and the conditional
transition matrix is flat. Knowing any earlier tick's digit does not change the distribution of any later
tick's digit. (Full N+2 treatment in the master report.)

## 5. Live demo confirmation (600 real trades)

Live 1-tick batches on 1HZ100V (settled deterministically from the exit tick; reconciled to balance):

| Contract | n | realized win rate | theory | binomial p | account P/L |
|----------|--:|------------------:|-------:|-----------:|------------:|
| DIGITEVEN | 300 | 50.67% | 50% | 0.86 | −4.08 USD |
| DIGITOVER-0 | 300 | 91.33% | 90% | 0.50 | −5.84 USD |

Realized win rates match theory exactly, and **both lost real money** — DIGITOVER-0 won **91%** of trades
and still lost, because its payout is below fair. **Stake-size caveat:** at the $0.50 minimum, payout
rounding inflates the edge to **4.0–4.6%** (vs the 1.36–2.35% headline at $10 stake) — small stakes are
strictly worse. Raw data: [`../../digits/data/`](../../digits/data/).

## Verdict

**No +EV on any digit contract.** The RNG passes every randomness test; all six contract families carry a
1.36%–16.67% house edge; and no streak, window, overdue, alternation, momentum, or N+1→N+2 signal exists
to overcome it. Matches (10.7–16.7% edge, 10% hit rate) is the worst; the `p=0.9` family (1.36%) is the
least-bad but still strictly negative.
