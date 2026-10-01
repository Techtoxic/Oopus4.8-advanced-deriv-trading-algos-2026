# Failed-ideas ledger: everything tried on this repo (June to October 2026)

This ledger records every trading idea tried across all 15 branches: `main`, `deriv-edge-research-2026-06-07`, `capy-fable5-session-2026-06-09`, `fable-session-2026-06-10`, `opus-thoughts`, `lattice-microstructure-validation`, `captain-adaptive`, `metrics-engine-2026-06-16`, `fable-thoughts`, the four `capy/*` branches, `forward-test/hma-crypto`, and `claude/intelligent-hamilton-0a2cwm`. It also includes the chat-only ideas from Sep 29 to Oct 1, and two documents that were deleted from history on 2026-06-16 (`live_session.md` and `oos_validation.md`).

**How to use it.** Before you build a new idea:

1. Run it past **Part 0**, the closing arguments. Most new proposals die there, and checking costs nothing.
2. Search this file for the contract type and the mechanism.
3. If it survives both, write the pass/kill rules down **before** you look at data (see Part 16).

**Status key**

| Status | Meaning |
|---|---|
| DEAD | Tested. No signal. |
| PRICED | A real structure exists, but the payout absorbs it, or Deriv repriced it away. |
| WORKED-THEN-DIED | Made money (backtest or live), then Deriv changed the terms. |
| CLOSED | Ruled out by a general argument, so no specific test is needed. |
| UNTESTABLE | No contract is offered, or the data cannot answer the question. |
| OPEN | Proposed or partly built. No verdict yet. |
| SURVIVED | Passed validation. No live-money proof yet. |

---

## Bottom line

- **Only two edges ever existed on Deriv synthetics.** Both were "stale parameter" edges: a payout grid or barrier that Deriv had not updated for the physics. Both were removed by Deriv, not by the market.
  1. **JD100 last-digit clustering at low σ.** Live demo: 4,620 trades, 59.35% win rate, +6.53% per trade at a 1.794 payout. The payout was then cut to 1.56, then to **1.3429** for logged-in accounts.
  2. **CRASH/BOOM accumulator phase lattice.** Passed on the **public** barrier quotes (D = 1.00327). Logged-in accounts are sold barriers 2.5% tighter, exactly on the positive cells. On those barriers no cell is tradable.
- **Everything else on synthetics is DEAD, PRICED or CLOSED**, including any idea that predicts the next tick from past ticks. On 1HZ30V, mutual information measured 0.000000 bits against 0.001157 bits needed (Part 10).
- **Real markets** have a few survivors and open leads: gold with the COT index, the HMA crypto paper test, EURUSD barrier quantisation, and BTC lead-lag (Parts 11–13).

---

## Part 0. Closing arguments: check every new idea against these first

| # | Argument | What it kills | Evidence |
|---|---|---|---|
| A1 | **Information bound** (data-processing inequality): no function of the past can carry more information about the next tick than the past itself. | Every predictor built from past ticks: neural nets, Markov chains of any order, CTW, wavelets, TDA, Koopman, chaos reconstruction, RMT, p-adic, "AI pattern" tools, technical analysis on synthetics. | JD100: +0.0022 bits, flat over k=1–4, vs 0.008924 needed at 1.80. 1HZ30V: 0.000000 bits vs 0.001157 needed. 28 symbols checked. |
| A2 | **Linearity**: E[X+Y] = E[X]+E[Y] under any dependence. | Hedges, strangles and straddles, multi-leg bundles, cross-duration and cross-symbol combinations, "portfolio" optimisation. Each leg pays its own house margin. | Step mixed durations give exactly (EV_a+EV_b)/2. The friend's two-sided Higher/Lower lost −10% and −15.5% (Part 15). |
| A3 | **No Dutch book**: you can only go long, and every contract has E[payoff] < 1. | Arbitrage locks, partition bets, "cover every digit". | Cheapest lock across all digits costs 1.0138 per $1 (LP over 20 symbols). |
| A4 | **Volatility drag is not tradable**: the median log return falls, but E[price] does not. | Shorting decaying indices; "BOOM300N is built to fall". | JD100 log −8.42% vs mean price +0.12% over 200k paths. BOOM300N drift t = −1.50. |
| A5 | **Entry boundary at T+1**: a buy placed at tick t enters at T+1. Any information you have is already in the past. | Latency arbitrage, feed-lead racing, "buy faster", waiting T+k ticks. | 661/663 and 1281/1282 contracts settled at T+1. Loss rate is 0.098–0.102 at every delay from T+1 to T+10. |
| A6 | **Contract-availability policy**: Deriv offers a contract only where the outcome carries no information. | Digits on Step indices; Rise/Fall and digits on Boom/Crash; intraday barriers on forex session volatility; digits on new launches. | Step indices: 1.0–2.3 bits, no digits offered. Boom/Crash: 0.007–0.06 bits, only ACCU and MULT offered. |
| A7 | **Duration collapse**: any conditioning edge lives at lag 1 only. | Multi-tick digit variants; "skip a tick". | Lift: +24.8% at N=1, +4.5% at N=2, +1.0% at N=3. JD100 at lag 2: −1.5%. |
| A8 | **σ is observed, not hidden**: σ = c·spot at 99.8% of the noise ceiling. | Kalman/particle filters for σ, "predict the volatility", side channels. | Residual/noise ratio 1.01. |
| A9 | **Edge lifetime = min(time for σ to drift, time for Deriv to react)**. Deriv now reacts in days. | Waiting for spot or σ to drift into an edge. | Four cuts in eight days in August, plus pre-emptive cuts on 1HZ100V, 1HZ10V and R_100. |
| A10 | **Accumulator calibration**: barriers are re-quoted to hit a target survival rate, P(stay) ≈ 0.985. | "Stale ACCU barrier" on volatility indices. | P(stay) 0.9843–0.9857 on 19 symbols. |
| A11 | **Tier split**: logged-in accounts get worse terms than the public quote. | Any edge measured on public `proposal` quotes. | JD100 OVER4: 1.953 public vs 1.3429 logged-in. ACCU barriers tighter on 45 of 85 cells. Same-session logged-in quote vs fill: 8/8 pairs, ratio 1.0000. |
| A12 | **Martingale only reshapes outcomes; EV is unchanged.** | All staking-plan "fixes": martingale, anti-martingale, Kelly on −EV, a bigger bankroll. | Ruin 97.4% within 30 days. A $50 bankroll "loses 4× more dollars". Differs martingale: a 10-loss streak needs $1.3e10. |

---

## Part 1. Synthetic digits: generic randomness and pattern strategies

| # | Idea | What was tried | Result | Status |
|---|---|---|---|---|
| 1.1 | Randomness battery (chi², entropy, ACF, runs, FFT, transitions) | 10 vol indices, 5k ticks each | chi² p 0.13–0.98; entropy 99.94–99.99%; ACF within ±0.028; FFT white | DEAD |
| 1.2 | Digit frequency bias / "hot digits" | χ² on 1HZ100V, R_100, 1HZ10V, JD100 | p 0.938 / 0.821 / 0.078 / 0.450 | DEAD |
| 1.3 | Last digit predicts the next (Markov) | 10×10 χ², df 81 | p 0.86 / 0.51 / 0.39 / 0.91 | DEAD |
| 1.4 | Even/odd streaks and alternation | Parity ACF, Ljung-Box(20), runs test | All inside the noise bands; flip rate 0.500 at streaks 1–10 | DEAD |
| 1.5 | "N+1 is a throwaway tick; predict N+2" | Mapped entry and exit ticks live | The premise is false: entry is N+1. Lag-2 χ² p 0.11–0.94 | DEAD |
| 1.6 | Gambler's fallacy ("under is due after k overs") | k = 3, 5, 7, 10 | 0.41–0.57, scattered | DEAD |
| 1.7 | Frequency-window fade (side above 60% of last 20) | Thousands of events | 0.489–0.521, p > 0.16 | DEAD |
| 1.8 | Momentum on overs | Mirror of 1.6 | 0.42–0.54 | DEAD |
| 1.9 | Overdue digit MATCH (coldest of last 50) | Conditional hit rate | 0.094–0.101 vs 0.141 needed | DEAD |
| 1.10 | Best conditional DIGITDIFF cell | Wilson-99 lower bound vs break-even 0.917 | Best 0.9065 (lower bound 0.898) | DEAD |
| 1.11 | Prior repo's 1,320-cell sweep and neural net | Re-derived on 86k ticks | No Bonferroni-significant cell | DEAD |
| 1.12 | Time-of-day / reseed digit artifacts | 600 second-of-minute cells + 240 hour cells, JD100 1.2M ticks | Worst z 3.36 / 3.68, the expected noise maximum | DEAD |
| 1.13 | Fable5 conditional DIGITDIFF re-audit | 150k fresh ticks, 5 symbols | Best lower bound 0.9075 vs 0.9126; 1 hit at p = 0.009 in 25 tests | DEAD |
| 1.14 | 1,380-cell conditional next-step scan | 15 symbols, 8.5M ticks | 0/1,380 with a positive Wilson-99 lower bound | DEAD |
| 1.15 | Live demo confirmation | 600 trades on 1HZ100V | EVEN 50.67% (−$4.08); OVER0 91.33% (−$5.84) | DEAD |
| 1.16 | Small-stake grinding | $0.35–$0.50 stakes | Payouts round down to cents: effective margin 4.0–4.6% vs 1.36–2.35% headline | DEAD (worse than headline) |
| 1.17 | "Closest to fair" digit play | Live proposal pricing | 1.36% margin (DIFF / OVER0), still −EV | PRICED |
| 1.18 | Popular strategies backtested: Differs, Even, Over0, gambler, momentum, mean reversion | $10 start, $0.35 base, real payouts | All ruined. ROI −0.7% to −24% (Differs 90.5% win, −$9.73) | DEAD |
| 1.19 | Short-run paper-trader "profit" | 40 trades | +$0.50 at 95% win rate: variance | DEAD |
| 1.20 | Telegram "matches tool" | Rebuilt; 400 live trades | "23% confidence" gave 10.7% wins; −$42.80 | DEAD |
| 1.21 | Omnibot `digits_flow` | Flat stakes | −1.6% of turnover (built as a calibration bot) | DEAD |
| 1.22 | Out-of-sample digit prediction on 1HZ30V | 2.7M trades per contract at T+1 | Hit 0.4991–0.5002. EVEN/ODD −3.8%, RISE/FALL −2.5%. A planted signal was detected (+7.2%), so the test had power | DEAD |

## Part 2. The friend's 6-layer "lattice microstructure" digit system

| # | Idea | Result | Status |
|---|---|---|---|
| 2.1 | "About 90% of untapped digits fill on revisit" | 0.9027 vs geometric law 0.9015: it restates the geometric law | DEAD |
| 2.2 | An untapped digit predicts the next tick | P = 0.1002 / 0.1005 / 0.1008 at w = 5 / 10 / 20 | DEAD |
| 2.3 | Higher-order cluster memory | Max z ≈ 2.75 in 21 comparisons vs a shuffle | DEAD |
| 2.4 | Layer 2 skew-follow / mode-match / least-frequent | 0.4986 vs 0.512 break-even; 0.0998 and 0.1013 vs 0.112 | DEAD |
| 2.5 | Layer 3 adaptive regression for the next cluster | 0.8207, worse than "next = last" (0.8762) | DEAD |
| 2.6 | Layer 4 game-theory gate at 0.99 | Never fires (convergence peaks around 0.6) | DEAD |
| 2.7 | Layer 6 drift detector | 3 trades, then halted 80,657 times | DEAD |
| 2.8 | Forced to trade | −5.60% / −4.67% / −0.63% / −7.92% on 4 symbols | DEAD |
| 2.9 | Planted-edge control | +5.01% on an artificial stream: the pipeline works; real data has no edge | Control |
| 2.10 | $10 account with Kelly | 2% cap = $0.20, below the $0.35 minimum stake | DEAD |

## Part 3. Digit bundles, payout surface, "Deriv steers the digit"

| # | Idea | Result | Status |
|---|---|---|---|
| 3.1 | 5-digit MATCH bundle on the same tick | P(hit) 0.5; EV −$0.535 per round (−10.7%). You buy the most expensive contract five times | DEAD |
| 3.2 | 5-digit bundle spaced across ticks | Same EV. 41% of rounds have ≥1 hit: "spacing is a feelings dial" | DEAD |
| 3.3 | Replicate a digit set more cheaply (OVER4/UNDER5/EVEN/ODD/DIFF) | −1.36% to −2.35% vs −10.7%. Cheaper, still −EV (`coverage_optimizer.py`) | PRICED (tool) |
| 3.4 | "MATCH pays 6.04" | A third-party app's markup; the real payout is 8.929 | DEAD |
| 3.5 | "The digit lands where Deriv pays least" (512-combo liability steering) | 5% steering would show as P(hottest) 0.0944. Measured 0.0999–0.1010; hazard flat | DEAD |
| 3.6 | Dutch book / partition arbitrage | Cheapest lock 1.01379 | DEAD (A3) |
| 3.7 | Duration spreads | Payouts do not depend on duration | DEAD |
| 3.8 | "Use the slower 2-second index" / venue choice | R_100 and R_10 carry 2–6pp **more** margin (MATCH 8.333 / 8.696) | DEAD (avoid them) |
| 3.9 | House margin small enough to beat | Margin "smile" 1.36% (DIFF/OVER0) to 16.67% (R_100 MATCH); never zero | PRICED |

## Part 4. JD100 σ / step-physics digit edge (the main lineage)

**Mechanism.** The next digit = (current digit + step in pips) mod 10. The step size scales with spot (σ = c·spot, c ≈ 0.0178). Below σ ≈ 4.45 pips the next digit clusters near the current one, while the payout grid assumes uniform digits.

| # | Stage | Result | Status |
|---|---|---|---|
| 4.1 | Discovery: step mod 10 is non-uniform | z = 5.59 on 1.21M ticks. Of 92 symbols, only JD100 is near the boundary | Real |
| 4.2 | 14-day replay | +0.99% per trade, t = 1.89. Almost all profit came from one low-spot day | Superseded |
| 4.3 | Lag-2 / missed ticks | Lag 2: −1.5%. With 30% missed ticks: −$123 | Constraint (A7) |
| 4.4 | Deep-gate in-sample vs out-of-sample | +3.22% in-sample → −1.67% out-of-sample (winner's curse) | Corrected |
| 4.5 | Walk-forward | No gate −0.12%; σ ≤ 4.5: +1.52% | Gate added |
| 4.6 | First live hours | 1,282 trades, −1.09% (t = −0.3). **These files were deleted later** | Inconclusive |
| 4.7 | Metrics engine "no edge" | Wrong: fixed barriers averaged the signal away | Superseded |
| 4.8 | Exact-rule replay | +6.24% per trade, t = 7.04; placebos −3.2% / −3.9% | SURVIVED |
| 4.9 | Max-EV picker over all 17 OVER/UNDER contracts | Live −8.1% vs model +2.1% (optimiser's curse on narrow contracts) | DEAD |
| 4.10 | Quarter-Kelly on a small balance | 52.8% win rate yet −$37; flat stake +$39 | DEAD → flat stake |
| 4.11 | Captain v3 (σ = c·spot, zero fitted parameters) | Walk-forward +1.25% to +2.49%, t 1.7–3.5 | SURVIVED |
| 4.12 | Entry-digit lead (digits 2 and 7, "z = +47") | A looping fetcher repeated one day 18×. On clean data: ρ −0.19 | DEAD |
| 4.13 | Universal p(σ) curve | 4 symbols, 44× σ range; physics confirmed | Fact |
| 4.14 | Information bound at 1.80 | Maximum win rate 52.80% < 55.56% | PRICED (A1) |
| 4.15 | MI "intermittency" | corr(σ, MI) −0.87: it is just σ | DEAD |
| 4.16 | OVER5 rung (2.186) | −7.8% to −10.7% in every σ band | PRICED |
| 4.17 | "Volatility drag brings spot to the gate in ~12 days" | Overstated 3.6×; a median is not a date (65 days, P ≈ 49%) | Corrected |
| 4.18 | **Edge reopened at 1.794** after the socket-drain fix | **4,620 trades, 59.35% win, +6.53%, +$603.78 on $2 stakes**, 5 SE above break-even | **WORKED-THEN-DIED** |
| 4.19 | Repricing history | 1.953 → 1.818 → 1.810 → 1.794 → 1.800 (August) → 1.56 / 1.54 (Sep 10) → **1.3429** logged-in (Sep 27). OVER0/1, UNDER8/9 and DIFF0 delisted | PRICED |
| 4.20 | "Quantum lattice" Jacobi-theta policy | Old grid +41.5% out-of-sample; live grid −8.17% (t = −12.8). Deriv removed 48.5pp. Its lag-2 control was invalid | WORKED-THEN-DIED (on paper) |
| 4.21 | JD10/25/50/75 (still on the full grid) | σ ≈ 11 pips: −2.5% to −3.7% | DEAD |
| 4.22 | High-σ JD100 / other symbols | At σ = 5 the boost is +0.9pp vs a 2.35% margin | DEAD |
| 4.23 | Bots: `jd100_demo`, v4, continuous, `--real` | Infrastructure only; real mode has mocked tests only | No edge at the current grid |
| 4.24 | Watch for grid restoration / pip_size change | Restoration would reopen the edge the same day (σ 2.38). A pip_size change would end it permanently | OPEN (monitor) |

## Part 5. Other digit symbols and the symbol universe

| # | Idea | Result | Status |
|---|---|---|---|
| 5.1 | 17 symbols × 22 contracts × 10 entry digits | Only JD100 has clustering. 13 of 14 "positive" cells were negative at the executed price | DEAD |
| 5.2 | 1HZ15V / 1HZ30V / 1HZ90V | σ 350–3,031 pips; MI equals the shuffle | DEAD |
| 5.3 | Pre-emptive cuts on 1HZ100V / 1HZ10V / R_100 | Executed 1.9230 vs proposal 1.9530 | PRICED |
| 5.4 | Step-index digits (contain 1–2.3 bits of information) | Not offered. An earlier "100% win, +$1,260" backtest was a bug | UNTESTABLE (A6) |
| 5.5 | Reverse "proposal lie" (fill better than quote) | Not seen on 20 symbols | DEAD (watchdog OPEN) |
| 5.6 | New symbol launches (RB100/200, Boom/Crash 150N/300N) | No digits offered | OPEN (monitor) |

## Part 6. Rise/Fall, ties, reset indices, step indices, day direction

| # | Idea | Result | Status |
|---|---|---|---|
| 6.1 | Drift / Rise vs Fall bias | p 0.17–0.80; P(up) 0.47–0.50 | DEAD |
| 6.2 | Momentum rise(3) / mean-reversion rise(3), flat stakes | All ruined. ROI −2% to −24% | DEAD |
| 6.3 | The same with martingale | "Survived" only because the killing streak had not arrived. ROI −7% to −88% | DEAD |
| 6.4 | CALLE/PUTE tie pair on JD100 | Quote implied +5.6% / +7.9%. Executed −2.12% live | PRICED |
| 6.5 | CALLE/PUTE on all 17 symbols | −1.06% to −3.85% | PRICED |
| 6.6 | Step Index Rise+Fall hedge | 7 of 20 pairs tied: −20% per round. Exact binomial pricing | PRICED |
| 6.7 | T4: step-index barrier-offset parity | Every offset rejected; payout × P = 0.977 / 0.954–0.966 | DEAD, but **re-check**: on 2026-10-01 the volatility indices also rejected every free-form Higher/Lower offset ("Invalid barrier"), which points to server-defined barrier choices rather than "no barriers offered" |
| 6.8 | RDBULL/RDBEAR drift (+9.24% / −5.65% per day, real) | CALL/PUT priced at a uniform −2.2% to −2.5% | PRICED |
| 6.9 | Midnight reset snipe (00:00 = 1000.0000 exactly) | Buys in the final seconds are rejected; the reset tick belongs to the new session | DEAD |
| 6.10 | Day-shape memory on reset indices | 0/364 duplicate days | DEAD |
| 6.11 | RDBULL per-hour shape | Called real on Aug 18; later F 1.24, p 0.20 | DEAD |
| 6.12 | **"The day's direction is decided early"** (friend) | 13 symbols × 364 days; controls detected (t +20.1 / −11.9). Variance ratio 0.93–1.13; half-day r −0.10 to +0.07; hour F 0.59–1.53 | DEAD |
| 6.13 | BOOM300N 1004 → 400 decline as "built-in bearishness" | Drift t = −1.50: volatility drag on a zero-drift index | DEAD (A4) |
| 6.14 | Barrier lock on reset indices | Costs 1.030–1.033; RANGE capped at 40× | PRICED |

## Part 7. Accumulators (ACCU)

| # | Idea | Result | Status |
|---|---|---|---|
| 7.1 | Survival-curve exit / take-profit timing on vol indices | EV(k) = 0.995^k; memoryless | PRICED |
| 7.2 | Exact RTP across rates | G = 0.9922–0.9980, all below 1 | PRICED |
| 7.3 | Omnibot ACCU take-profit at +70% | Busts logged | DEAD |
| 7.4 | Boom/Crash ACCU "G > 1 on 70/70 cells" | Units error | DEAD |
| 7.5 | BOOM1000 g = 3% anomaly (+3.8% / +8.7%) | Rounded longcode barrier ≠ actual barrier. Live: 9/9 died at ticks 13–54 | DEAD |
| 7.6 | Full Boom/Crash ACCU table | Every cell −2.4% to −89% | PRICED |
| 7.7 | 1HZ75V G = 1.00075 (t = 3.5) | Spot-drift bias | DEAD |
| 7.8 | **Phase lattice**: knockout uses whole pips vs a continuous barrier → sawtooth survival in frac(w) | Public barriers: D = 1.00327, +6.3% per trade, PASS-B. Logged-in barriers: 0 tradable cells; replay −8.78% [−11.0, −6.6] | **WORKED-THEN-DIED** (A11) |
| 7.9 | CRASH1000 4% live demo (12 Sep) | 20 contracts, −$6.86 (6 wins) | DEAD |
| 7.10 | CRASH500 level-14 accumulator | +4.02% on public barriers; dead on logged-in | PRICED |
| 7.11 | N-series / N = 50 symbols | Model predicted G up to 1.04; 0/99 house runs matched: wrong knockout rule | DEAD |
| 7.12 | BOOM300N "coarsest lattice, ~3× edge" | Step is 8.73 pips, not 3.35 | DEAD |
| 7.13 | BOOM300N on logged-in barriers | 3% ≈ −11.5%. Needs spot ≈ 170–256 (now ≈ 400) | OPEN (spot-dependent) |
| 7.14 | Vol-index ACCU (JD100-style failure) | Positive only at σ < 9.6 (R_100 < 381, 1HZ100V < 539) | DEAD (watch) |
| 7.15 | Hidden good band at phase (0.9, 1) | All 3 exact touches knocked out | DEAD |
| 7.16 | Cent-rounding 1-tick take-profit | −0.23% to +0.03% | DEAD |
| 7.17 | Spikes cluster in UTC seconds 50–59 | p 0.0004, then failed on the 4th symbol (p 0.38) | DEAD |
| 7.18 | Spike renewal / "spike is due" | Gap CV 1.026: geometric | DEAD |
| 7.19 | T3: are tighter barriers specific to this account? | No: market-wide; a second account matches; no stake dependence | DEAD |
| 7.20 | **T2: phase map + re-tuning lag watcher** | 65 armed cells; none near a window (needs 35–70% spot moves) | OPEN (expected KILL at day 14) |
| 7.21 | CRASH900 4% / BOOM600 3% on logged-in terms | Eligible on 2026-09-27; never back-tested or pre-registered | OPEN (untested) |

## Part 8. Multipliers, Boom/Crash direction, early close, deal cancellation

| # | Idea | Result | Status |
|---|---|---|---|
| 8.1 | Multiplier direction on vol indices | ACF within ±0.028; commission 0.036% of notional | DEAD |
| 8.2 | "Crash ticks up every tick, so buy Rise" | Rise/Fall and digits are not offered on Boom/Crash (0/10 buys) | UNTESTABLE (A6) |
| 8.3 | Boom/Crash drift | Σup 491 vs Σcrash 641 on CRASH1000: the spike repays the small ticks | DEAD |
| 8.4 | Spike timing / "overdue crash" / safe window / spike detectors | χ² p 0.32–0.42; gap CV 0.93–1.00. BOOM1000 p = 0.045 on 79 spikes = artifact | DEAD |
| 8.5 | Post-spike next tick | P(up after up-spike) = 0.000 | DEAD |
| 8.6 | Paired long + short multiplier | Pays exactly −2 × commission | DEAD (A2) |
| 8.7 | Deal-cancellation pair | Paper +18.69 EV; live −1.61 mean (double-knockout geometry) | DEAD |
| 8.8 | Single-leg deal cancellation | Fee 7.62 vs a 10.00 ceiling | PRICED |
| 8.9 | Early close / sell-back | Bid/fair 0.8219 (17.8% spread); worse near expiry and deep out of the money | PRICED |
| 8.10 | **T1: multiplier "gap put"** (Crash cannot tick down between spikes) | CRASH500 ×400: −5.2%. CRASH1000 ×500: −0.5% [−7.1, +6.0]; needs drift +5e-8 per tick | DEAD / PARKED |
| 8.11 | Short JD100 volatility drag | E[price] unchanged (Jensen) | DEAD (A4) |

## Part 9. Other contract families

| # | Family | Result | Status |
|---|---|---|---|
| 9.1 | Touch / No-Touch | Every cell −13% or worse. An earlier +44% / +80% came from a two-sided touch bug | PRICED |
| 9.2 | Vanillas | 10–12% median margin; −10% to −44% | PRICED |
| 9.3 | Turbos | Median margin 5.2–5.7%; **18 of 60 cells unmeasured** | PRICED (incomplete) |
| 9.4 | Runs | −9% to −19% | PRICED |
| 9.5 | Asians | −2.6% to −3.1% | PRICED |
| 9.6 | Tick high/low | Within ±0.4% of fair, still −EV | PRICED |
| 9.7 | Reset call/put | −3.3% / −2.7% | PRICED |
| 9.8 | Expiry range / miss | −3.9% / −1.7% | PRICED |
| 9.9 | Range (stays in) | −97%: payout capped at 40× vs ~1,400× fair | TRAP |
| 9.10 | Logged-in exotic sweep (24 families) | Step index: only CALL/PUT accepted | Mapping |

## Part 10. Generator / RNG / "hack the PRNG"

| # | Idea | Result | Status |
|---|---|---|---|
| 10.1 | PRNG cycles / weak seeds | FFT white; Lempel-Ziv ratio 0.99975 | DEAD |
| 10.2 | Cross-symbol seed correlation | 21 pairs, max \|z\| 2.5 | DEAD |
| 10.3 | Volatility clustering | \|step\| ACF max 0.0057. Earlier "p = 0" flags were spot-scaling artifacts | DEAD |
| 10.4 | JD100 jump timing | CV 1.17–1.39; conditioning moves win rate by 0.018pp | DEAD |
| 10.5 | Kalman / particle filter for σ | σ is spot × constant | DEAD (A8) |
| 10.6 | Feed race between two sockets (93.8 ms lead) | Client artifact; the T+1 boundary kills it anyway | DEAD (A5) |
| 10.7 | MT5 vs API feed lead-lag | Ruled out by A5 | CLOSED |
| 10.8 | Algebraic PRNG recovery (LCG lattice, p-adic) | Rounded cumulative sums destroy the raw output | UNTESTABLE |
| 10.9 | **generator_probe v1/v2/v3 on 1HZ30V (9M ticks) + R_75**: 32 tests | No flag at Holm 1%. σ ratio 0.99998. MI 0.000000 bits | DEAD |
| 10.10 | "The generator is a CSPRNG, so it can be broken" (articles) | Irrelevant: a CSPRNG is designed so its output leaks nothing, and the measured MI is zero | CLOSED |
| 10.11 | Macro reflection / hidden range management / rebasing | No rebase (max overnight jump 3.3 σ); lifetime drift t 0.07. Two-sided test impossible: the API serves only 366 daily candles | NO EVIDENCE |
| 10.12 | IEEE-754 float32 digit skew | Quotes are double precision: all 39 grid residues occupied (p 0.99) | DEAD |
| 10.13 | Reseed / maintenance / midnight anomalies | Generator keeps running through feed gaps. Midnight excess p 0.003 on 1HZ30V failed to replicate on R_75 (p 0.79) | DEAD |
| 10.14 | Time-of-day volatility | p 0.35 / 0.32 | DEAD |
| 10.15 | **MT5 feed = API feed?** Volatility 90 Index exported from Deriv MT5 (9.94M ticks, 230 days, 2 s, mid price) through `mt5_ticks_to_npz.py` + `generator_probe_v3.py --no-digits` | σ measured/nominal **0.99988**, drift t 0.38, 28 tests no Holm flag, MI 0.000000 bits, out-of-sample rise/fall hit 0.4997–0.4999. Same generator on MT5. Median MT5 spread 47.4 ≈ 3.4 bp ≈ 1.5 tick-σ, paid on every round trip | DEAD (and the MT5 pipeline is validated) |
| 10.16 | **Drift Switch Index 20** (MT5 export, 31M ticks, 365 days, 1 s) through `generator_probe_v3 --no-digits` | Real, designed structure, replicated in both halves: VR16 1.013, VR64 1.054, VR256 1.197, giving regime drift ~0.028 tick-σ per tick; vol clustering (abs lag-1 ACF 0.13); 1-minute breakouts continue 61–66%. But next-tick information 0.000005 bits, and breakouts continue only 0.57–1.51 price units against a median spread of 3.15 (~23 tick-σ). Tradable only if it pays the spread: `tools/drift_regime_test.py` (rules and pass/kill fixed in its docstring before the real run) | **PRICED by the spread** (2026-10-01). Pre-registered test, 128 causal momentum rules, ask/bid fills one tick late: best rule chosen on the first 70% (L 60, θ 1.0, H 60, cheapest-quarter spread only) made **+0.12 bp gross, −3.89 bp net** on the last 30% (2,004 trades, 99% bound −4.12); the placebo lost −3.64 bp, i.e. just the spread. The spread is proportional to price (~9.8 bp, flat 3.0–3.3 at every UTC hour), so there is no cheap time of day; the p5 dips are scattered. Even an oracle knowing the regime sign needs ~800 ticks of drift to pay one round trip |

## Part 11. Technical analysis, money management, execution

| # | Idea | Result | Status |
|---|---|---|---|
| 11.1 | `ta_multiscan`: S/R reversal, FVG fill, order block, breakout, momentum, mean revert (112 cells) | t-stats are standard normal (max 3.4 vs 3.1 expected) | DEAD |
| 11.2 | "Support/resistance works" | A pure random walk "rejects" swing highs 77.7% of the time | DEAD |
| 11.3 | Scalping vs spread | 1-minute break-even needs a 63.3% win rate | DEAD |
| 11.4 | Win-rate targeting via TP/SL ratio | 1:10 → 90.7% win, 10:1 → 9.7%; both lose about the spread | CLOSED |
| 11.5 | "Guaranteed $1/day" target martingale | Ruin 62.9% in 7 days and 97.4% in 30 days; E −$9.56 per month | DEAD |
| 11.6 | Bigger bankroll ($50) | Ruin 70.9% in 30 days; E −$39.41 | DEAD |
| 11.7 | Anti-martingale | ROI −15% to −40% | DEAD |
| 11.8 | Kelly as the fix | Kelly on a −EV bet says stake $0 | CLOSED |
| 11.9 | Multiple accounts on one signal | 5× the same correlated position; not diversification (and ToS risk) | CLOSED |
| 11.10 | "$50 → $20k in 6 hours" | Regime tail plus compounding, not the expectation | DEAD |

## Part 12. Real markets on Deriv, MT5, FX, metals, COT

| # | Idea | Result | Status |
|---|---|---|---|
| 12.1 | USDJPY 1-minute mean reversion | ac1 −0.112 (real, z −10.6), but edge 0.5 bps vs cost 6.4 bps | PRICED by costs |
| 12.2 | Session volatility (2.3–4× ratio, real) | Touch contracts are daily-only; EURUSD CALL carries a 16.6% margin | UNTESTABLE |
| 12.3 | OTC indices while the exchange is closed | They follow exchange hours | DEAD |
| 12.4 | Adaptive trend following (SuperTrend + ADX + ATR), no COT | 2 of 9 instruments profitable; mean −7.4% | DEAD (FX majors) |
| 12.5 | + COT-index filter | Gold +23.7% (PF 1.29); FX still negative | Filter SURVIVED |
| 12.6 | In-sample vs out-of-sample with the COT index | Gold out-of-sample +16.9%, PF 1.51, Sharpe 0.52. AUDUSD and EURJPY died out-of-sample. **Audit 2026-10-01** (`currencies-metals-swing/research/gold_cot_audit.py`, verdict rules fixed before the run): out-of-sample CAGR 1.88%, Sharpe 0.52, vs buy-and-hold gold Sharpe 0.94; buy-and-hold scaled to the same volatility makes 3.45%/yr with the same −5.7% drawdown. Swap at −6%/−2% a year takes 45% of the profit (71% over the full period). Entry timing vs 2,000 random-date placements of the same trades: 87.6th percentile (83rd full period), below the 95th required. The COT filter does add over the bare system (+356 out-of-sample, +1,198 full) | **DEAD: beaten by holding gold at the same risk; timing not significant** |
| 12.6b | COT signal as a tilt on an always-long gold position (0.5x / 1x / 1.5x), `currencies-metals-swing/research/gold_cot_tilt.py`, pre-registered | Out-of-sample +0.62%/yr over buy-and-hold at the same average weight, 73.7th percentile of 2,000 shifted-signal nulls (full period +0.01%/yr, 56.8th); Sharpe 0.90 vs 0.94 for plain holding. Speculators' 3-year index: +0.07%/yr, 68th | **DEAD: the COT signal does not time gold** |
| 12.7 | Equal-risk 9-instrument portfolio | Sharpe −0.03, DD −24.7% | DEAD |
| 12.8 | COT gate on a Donchian/EMA/ADX core | Mean PF 0.98 → 1.28 over 9 pairs | SURVIVED (FX data ends 2020) |
| 12.9 | Commercials-net COT rule | Blocks gold's secular uptrend; mean PF 0.84 | DEAD |
| 12.10 | Contrarian at gold spec extremes | Extremes **confirm** momentum (t = 10.98) | DEAD (contrarian) |
| 12.11 | London / opening-range breakout | PF 0.89–0.93; best of 36 configs 1.17 in-sample → 0.95 out-of-sample | DEAD |
| 12.12 | Asian-range fade | PF 0.60–0.91 | DEAD |
| 12.13 | XAU/XAG z-score pairs | PF 0.62; ratio not stationary | DEAD |
| 12.14 | ML trade filter (purged walk-forward) | AUC 0.486; being more selective lowered expectancy | DEAD |
| 12.15 | Next-bar ML on H1 | "AUC 0.66" was a flat-bar artifact; daily 0.49 | DEAD |
| 12.16 | Gold seasonality | Only January stable | DEAD (too thin) |
| 12.17 | USD-momentum veto on TSMOM | Sharpe 0.06 → −0.11 | DEAD |
| 12.18 | Swap arbitrage on MT5 | Swap-free accounts | DEAD |
| 12.19 | Legacy EAs: martingale/grid (≥7), fixed pip stops (83/97), M1 HFT scalpers, repainting signals, empty stubs | Structural flaws | DEAD |
| 12.20 | EliteGold_TSMOM (gold momentum, vol target, COT tilt) | Sharpe 0.39 in-sample / 0.40 out-of-sample; fails on FX | SURVIVED (needs ≥4 weeks forward demo) |
| 12.21 | HybridScalper_CostGate, HybridML_ShadowGate, TailGuard, RegimeAllocator | Built, unvalidated | OPEN |
| 12.22 | AdaptiveSwingTrader_v2, FFZ_v3, XU_SEMA_v2 and 9 F5 EAs | Compile clean; **no backtests run** (fixes: MSNR never traded; QuantumGoldSilver delivered RR 1.33 instead of 2.0) | UNTESTED |
| 12.23 | SMC / indicator confluence (ZULU SMC, SelfAwareTrend, MSnR-GAPS, LVRB) | Assessed as well built; never backtested | UNTESTED |
| 12.24 | EURUSD barrier quantisation (0.86 bp minimum offset; same shape as JD100) | Never tested | **OPEN** |
| 12.25 | BTC lead-lag (Binance → cryBTCUSD multipliers) | Tool built 2026-10-01: `tools/btc_leadlag.py` (Binance 1 s candles vs Deriv ticks, 2 s latency, multiplier commission from a live quote; first half chooses, second half judges, rules in its docstring). Self-test: planted 3 s lag PASS, no lag KILL, 40 bp cost KILL | **OPEN: ready to run locally** |
| 12.26 | Cross-market lead (real → synthetic) | Never tested | OPEN |

## Part 13. Crypto

| # | Idea | Result | Status |
|---|---|---|---|
| 13.1 | HMA 16/64 + RSI > 52 + close above LinReg(50), long-only, 4h | BTC beat exposure-matched random timing (96th / 99.8th percentile); ETH/BNB/SOL mixed. Paper forward test started 2026-09-27; PASS ≥ 95th percentile, FAIL < 50th, minimum 6 months | FORWARD-TEST RUNNING (code now also on this branch: `fable-thoughts/forward_test/hma_crypto/`) |

## Part 14. External reviewer rounds

| Round | Proposals | Outcome |
|---|---|---|
| Reviewer A (cross-disciplinary physics/maths: Koopman, RMT, p-adic, RG, spin glass, path integrals, percolation, Malliavin, quasicrystals, multifractals, ETAS, Wasserstein, information geometry …) | 26 | 16 died to the information bound (A1). Others were already done or were metaphors with no testable prediction. One was real (intermittency) and is closed. |
| Reviewer B ("15 gaps") | 15 | About two-thirds already done; the rest closed by A1/A2/A5/A8. |
| Reviewer C (frontiers) | 7 | Produced `watchdog.py` (launch monitor, reverse-lie scan, pip_size transition). |
| Capy independent audit (Aug 31) | — | "Core arguments all survive audit." |
| Sep "advanced physics" brief (T1–T4) | 4 | T1 dead/parked, T2 running, T3 and T4 closed (Parts 7, 8). |

## Part 15. Ideas from chat (Sep 29 – Oct 1)

| # | Idea | Result | Status |
|---|---|---|---|
| 15.1 | **Two-sided Higher/Lower "strangle"** (friend): buy Higher +B and Lower −B; a scanner predicts big moves | 1HZ25V ±71.69, 10 ticks, $6.52: P(clear) 55.2% vs 61.3% needed → **−10% per round**. ±81.69, 5 ticks, $10.00: 33.8% vs 40% → **−15.5%**. Breaks even only if each leg is fairly priced (A2). Past volatility does not predict move size: P(big) 0.547–0.555 in every quintile | DEAD on vol indices. Only open as a payout-grid audit (is any barrier paid above fair?) |
| 15.2 | **Differs + martingale**, switching duration 1–10 ticks to cap losing streaks | Loss probability is 10% at every duration. EV −1.36% per bet. Stakes grow ~11× per loss: $541 survives 2 losses; bust about 1 in 1,000 cycles; a 10-loss streak needs ~$1.3e10. Local data shows 4–6 loss streaks per ~80k bets | DEAD (A12) |
| 15.3 | Vary entry latency T+2 … T+10 | Loss rate 0.098–0.102 at every delay: each future digit is a fresh 1-in-10 draw | DEAD (A5) |

---

## Part 16. Methodology errors that made fake winners (don't repeat these)

**Data errors**
1. **A looping history fetcher served one day ~18 times** (86,401 unique of 1.6M epochs). This produced fake "z = +47" and wrong intervals. `count+end` paging also clamps to the last day: always use explicit `start`/`end` epochs and de-duplicate.
2. **CSV P&L vs real balance**: 897 contracts bought, 435 logged. Reconcile against the account balance.
3. **Deleting losing sessions** biases the record upward (`live_session.md` and `oos_validation.md` were deleted on 2026-06-16). Never delete negative results.
4. **Socket `recv()` returned the oldest message**: 51% of entries were late (+1.03% vs −2.31%). Drain the socket.

**Pricing errors**

5. **Public quote ≠ what you can buy** (the tier split). This produced at least 6 false positives on digits, CALLE/PUTE, OVER5 and the ACCU PASS-B. Only executed buys, or same-session logged-in quotes, count.
6. **Payout matched by contract type only**: Over 0's win rate was paired with Over 8's payout, giving a fake +7 EV.
7. **Stake resolution**: a $0.35 stake produced a false "whole book cut". Audit at $10 or more.
8. **"+1.46 profit" read as payout 2.46.**
9. **Omitted multiplier commission**: a fake +21 EV that grew with the multiplier.
10. **ACCU barrier errors**: absolute barrier vs relative move; spot drift; rounded longcode barrier vs actual `tick_size_barrier`.
11. **Third-party app payouts** (6.04 vs 8.929). Trust only the API.

**Modelling errors**

12. **Contract not offered**: the Step digit "100% win" backtest.
13. **Two-sided touch scored for one-sided ONETOUCH**: fake +44% / +80%.
14. **Wrong functional form** at least 4 times: through-origin σ fit, plug-in MI, linear intermittency, naive sawtooth.
15. **Wrong target tick**: conditioning at n but settling at n+2.
16. **Look-ahead from a centred smoother**: −2.4% looked like +10%.
17. **Fixed-barrier test of a conditional rule**: a false "no edge".
18. **Invalid null**: shuffling steps preserves the step-size distribution that creates the effect. A textbook null instead of a spot-scaled GBM null gave the generator_probe "p = 0" flags. Calibrate nulls by simulation.

**Statistical errors**

19. **Optimiser's curse / winner's curse**: max-EV picker; deep gates; best-of-36 configs.
20. **Overlapping windows**: 4,000 starts held only 222 independent windows.
21. **Small samples**: BOOM1000 p 0.045 on 79 spikes; RDBULL hour shape; one σ bin; 40-trade "profit"; "survived" martingale rows.
22. **A median presented as a date** ("~12 days").
23. **Selection by 1/√n**: the entry-digit spread shrank 2.42 → 0.36pp as data grew.
24. **Rules changed after seeing results** (ACCU oracle scoring changed twice after KILL verdicts). Pre-register and hash the rules first.
25. **Kelly sequencing on a small balance**: −$37 vs +$39 flat.
26. **Promised safety features not implemented** (the Opus sentinel docstring promised guards that did not exist).

**The protocol that came out of all this:**
- a shuffled or permutation null **and** a planted known-answer control in every scan;
- payouts from executed buys or logged-in same-session quotes;
- functional-form checks;
- Holm/Bonferroni across every cell tested;
- pass/kill rules written and hashed before the data is seen;
- out-of-sample data the design never touched.

---

## Part 17. What is genuinely still open

| Item | Why it might matter | Cost to test |
|---|---|---|
| T2 ACCU phase-map watcher | Catches Deriv re-tuning lag if spot moves 35–70% | Running; verdict at day 14 |
| CRASH900 4% / BOOM600 3% logged-in cells | Eligible on 2026-09-27; never tested | Low: pre-register, then replay |
| BOOM300N at spot ≈ 170–256 | Only if spot gets there | Watcher |
| JD100 grid restoration / pip_size change / new launches / executed > proposal (`watchdog.py`) | "Stale parameter" is the only edge shape that has ever worked | Needs a cron job |
| Payout-grid audit of Higher/Lower barriers vs fair value (`tools/hl_grid_audit.py`, built 2026-10-01, verdict rules pre-registered in its docstring) | Any barrier paid above fair is the only way 15.1 lives. Never audited before: `surface_scan.py` skipped HIGHER/LOWER with an offset | Low: quotes only, run locally with the token |
| 18 unmeasured turbo cells; ACCU barriers with g > 0.01 | Incomplete audits | Low |
| EURUSD barrier quantisation | Same shape as JD100, on a real market | Medium |
| BTC lead-lag; cross-market real → synthetic | Untested | Medium |
| Gold + COT index; EliteGold_TSMOM | SURVIVED out-of-sample; need ≥4 weeks forward demo | Time |
| HMA crypto paper test | Running; ≥6 months | Time |
| 12 MT5 EAs that compile but were never backtested | Unknown | Strategy Tester time |

**Pattern across 4 months.** Every idea that tried to **predict** a synthetic index died, because the generator leaks nothing (A1). Every idea that tried to **combine** contracts died to linearity (A2) or the house margin. The only things that made money were **pricing mistakes**: parameters Deriv had not updated for the physics. Deriv now fixes those within days and shows the public better terms than it sells. New ideas worth time either:
- **find a stale parameter**, measured on logged-in terms with executed fills; or
- **leave synthetics** for real markets, where the price is not set by the counterparty.
