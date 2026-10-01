# OPUS THOUGHTS — 2026-06-11 session

Your ideas, taken seriously, with exact math and live measurements. No markov/chi-square
theater as a conclusion — those are inputs at best. Every claim here is tied to a script in
`tools/` and a number in `results/`.

---

## 0. Your ideas, restated precisely

1. **5-digit matches bundle**: stake $1 on each of 5 different digits (DIGITMATCH), optionally
   latency-spaced so they settle on different ticks; one hit "pays for the round".
2. **Over/under version** of the same idea.
3. **The 512-combo liability surface**: at any tick, the public is holding every combination of
   over/under/match/diff/even-odd contracts. Deriv sees its total liability per possible digit
   (0–9) and — your hypothesis — *the digit lands where Deriv pays least*. Without volume data,
   crowd positioning is proxied by the digit-frequency percentages Deriv itself displays
   (trailing 1000-tick stats), because that's what the crowd is looking at when they click.
4. **n+1**: whatever we condition on must predict the *settlement* tick, which is at least one
   tick after the last tick we observed. Latency decides whether it's n+1 or n+2.

---

## 1. Exact math of the 5-digit matches bundle

Measured payouts (live proposal scan, re-verified this session — see `results/payout_surface.md`):
DIGITMATCH pays ≈ **8.93×** total return per 1 staked (NOT 6.04 — see §1.4).

### 1.1 Same-tick bundle (5 digits, $1 each)
- P(one of your 5 digits hits) = 5/10 = **0.50** (exactly one can hit; the digits are mutually exclusive).
- Round return: 0.5 × 8.93 = 4.465 on $5 staked → **EV = −$0.535 per round = −10.7%**.
- "Lose 5, gain (8.93−5)=3.93 on a hit, hit half the time" → still −10.7%. Bundling does not
  dilute the edge; you just bought the most expensive contract on the board five times.

### 1.2 Latency-spaced bundle (5 digits, 5 consecutive ticks)
Each leg is an independent Bernoulli(0.1):
- P(0 wins)=0.590, P(1)=0.328, P(2)=0.073, P(≥3)=0.009.
- EV identical: 5 × (0.1×8.93 − 1) = **−$0.535**. Latency spacing changes the *variance shape*
  (41% of rounds end with ≥1 hit instead of 50%, but multi-hit rounds appear), never the EV.
- Conclusion: **spacing is a feelings dial, not an edge dial.** Any stake/digit/tick-spacing
  combination of fixed-payout digit bets has EV = Σ stake_i × (p_i × M_i − 1). Linear. No
  structure of stakes can flip the sign if every term is negative.

### 1.3 THE REPLICATION INSIGHT (this is the upgrade to your idea)
The house edge is NOT flat across contracts — it's a smile (measured live):

| exposure you want | via MATCH×5 | via OVER/UNDER | via EVEN/ODD |
|---|---|---|---|
| any 5 contiguous digits e.g. {5..9} | −10.7% | **OVER 4: −2.35%** | — |
| {0..4} | −10.7% | **UNDER 5: −2.35%** | — |
| {1,3,5,7,9} | −10.7% | — | **ODD: −2.35%** |
| {0,2,4,6,8} | −10.7% | — | **EVEN: −2.35%** |
| 9 digits (all but one) | — | DIGITDIFF: −1.36% | — |

**Your exact trade ("cover 5 digits, one win pays the round") already exists as a single
contract — OVER 4 / UNDER 5 / EVEN / ODD — at 4.5× less bleed.** Deriv charges you 10.7% to
express digit-set exposure through matches and 2.35% to express the *same* set through
over/under/parity. The only time MATCH is the right instrument is when your information is
concentrated on ONE digit (and then you need P(that digit) > 11.2% true probability).

General rule shipped as a tool (`tools/coverage_optimizer.py`): give it any digit set S and the
live payout surface; it finds the cheapest replication using over/under/even/odd/diff/match
combinations (small LP). Stop paying matches prices for over/under exposure.

### 1.4 The 6.04 mystery — SOLVED
The full payout scan (`results/payout_surface.md`) finds **nothing paying ~6.04 on any of the 20
digit symbols at any barrier or duration** via the official `app_id 1089`. MATCH pays 8.929
(8.333 on R_100, 8.696 on R_10). If a tool you used displayed 6.04, it was a third-party app
skimming markup off your payouts — stop using it. The tools here quote live before every round.

### 1.5 What WOULD make the bundle +EV
A digit-set bet on S is +EV iff **P(settle digit ∈ S) × payout(S) > 1**. Thresholds (live payouts):
- OVER 4 / UNDER 5 / EVEN / ODD (M≈1.953): need P(S) > **51.2%** (base 50%) → +1.2pp of real edge.
- OVER 0 / UNDER 9 / DIGITDIFF (M≈1.096): need P > **91.26%** (base 90%) → +1.26pp.
- 5× MATCH (M≈8.93): need top-5 digit mass > **56.0%** (base 50%) → +6pp. Hopeless by comparison.
So: **any** real predictive signal should be spent on the 1.1–2.4%-edge contracts, never on
matches. This session hunts for that +1.2pp in places the previous sessions never looked (§2).

---

## 2. Hypotheses tested this session

### H1 — Your adversarial-digit theory ("the digit goes where Deriv pays least")
Formalized: if crowd stakes cluster on trailing-hot digits (the percentages Deriv shows in its
own UI are trailing-1000 frequencies — that's what people click on), a liability-minimizing
digit chooser would under-deliver hot digits and over-deliver cold ones. Signatures tested on
six-figure tick samples per symbol (`tools/adversarial_test.py`):
  - P(next digit = trailing-hottest) vs 0.10, P(= trailing-coldest) vs 0.10, full rank curve.
  - "Due digit" version (differs-martingale crowds chase overdue digits): distribution of gaps
    between digit recurrences vs Geometric(0.1); overdue-digit hazard curve.
  - Parity/over-under versions (crowds bet even/odd streaks): P(parity flip | streak length k).
If Deriv truly steered digits against the crowd, these curves CANNOT stay flat. Results in
`results/RESULTS.md`.

### H2 — Step physics (the structural attack nobody ran)
The last digit is not an abstract RNG draw — it's the last decimal of a *price* that moves by a
finite increment each tick: d(t+1) = (d(t) + Δpips) mod 10. If σ(Δ in pips) is small (≲5 pips),
(Δ mod 10) is concentrated → next digit is *structurally* predictable from current digit, and
over/under/even-odd conditional probabilities shift by more than the 1.2pp threshold.
σ_pips = price × vol × √(dt/year) / pipsize — it depends on price level and pip size, both of
which Deriv chose once and lets drift. `tools/universe_screen.py` measures σ_pips and the full
(Δ mod 10) distribution for EVERY symbol that offers digit contracts; `tools/edge_tests.py`
computes exact conditional next-digit tables with Wilson bounds priced against live payouts.

### H3 — Payout-surface mispricing / Dutch book
Full proposal sweep: every digit-enabled symbol × {MATCH,DIFF,OVER,UNDER,EVEN,ODD} × every
barrier × durations 1/5/10 ticks. Checks:
  - Partition arbitrage: any pair/triple of contracts covering all 10 digits with Σ(1/M) < 1
    (e.g. OVER k + UNDER k+1) → riskless. Also MATCH d + DIFF d.
  - Duration consistency: digit probabilities are duration-invariant, so payout must be too.
    Any duration paying more for the same event = strictly dominant (and a pricing bug).
  - Cross-symbol consistency: same event, different payout → always trade the best venue.

### H4 — The 512-combo liability simulation
Build the full contract universe; simulate Deriv's per-digit liability under realistic crowd
mixes; derive what a liability-minimizing chooser would do to observable digit statistics; show
which signatures H1 already covers (it covers all of them — rank curve + gap hazard are
sufficient statistics for "lands on the cheap side").

### H5 — Accumulators (your "endless parameters" instinct, priced exactly)
ACCU = survive-the-barrier compounding at 1–5% per tick. Empirical P(|Δtick| ≤ barrier distance)
on big samples × growth rate = exact per-tick RTP. If any symbol × rate has p×(1+g) ≥ 1, it's a
money printer; if close, the take-profit ladder sim says how close. (`tools/accumulator_scan.py`)

### H6 — Universe sweep (find the forgotten symbol)
`contracts_for` on EVERY active symbol. If digits are offered anywhere on a constrained process
(step indices move EXACTLY ±0.1; bear/bull, DEX, jumps, range-breaks have their own mechanics),
the structural attack in H2 becomes enormous. Nobody checks the weird symbols.

### n+1 mechanics (your "important consideration")
1-tick digit contracts settle on the first tick AFTER buy confirmation. Demo-measured this
session (`tools/n1_timing.py`): distribution of (entry/exit tick time − buy time), i.e. whether
conditioning on tick n actually targets n+1 or n+2, per symbol class (2s vs 1s). Every
conditional edge in this repo is computed against the *correct* target tick.

---

## 3. Results

**See `results/RESULTS.md` — the master verdict file.** Short version:
- H1/H4 (your adversarial/512-combo theory): **excluded by data** (steering ≥5% would be visible; measured curves are flat).
- H2 (step physics): **REAL on JD100** — z=5.59 non-uniformity, validated σ→edge curve, +0.99%/trade
  backtest over 14 days (+3.22%/trade in the deep regime), entirely execution-bound (n+1).
- H3 (payout surface): no Dutch book (lock costs 1.0138), no duration spreads; **R_100/R_10 pay
  strictly less than the other 18 symbols** for identical events.
- H5 (accumulators): G=(1+g)·P(survive) = 0.992–0.998 — cheapest gamble on Deriv, never +EV.
- H6 (universe): 20 digit symbols incl. forgotten RDBEAR/RDBULL/JD*/1HZ15-30-90V — all on the same
  static grid; only JD100 is near the σ boundary today.
- n+1: edge exists at lag 1 only (lag 2 = −1.5% EV); 30% slip flips the strategy negative.

## 4. Verdicts & what ships

See `results/RESULTS.md` §7 run-book + `tools/README.md`. Tools ship runnable either way:
- `tools/coverage_optimizer.py` — cheapest replication of any digit-set view (live payouts).
- `tools/multi_matches.py` — your 5-digit bundle, implemented properly (same-tick or spaced,
  per-leg stakes, live payout quotes, EV gate that refuses −EV rounds unless `--force`).
- `tools/conditional_trader.py` — over/under/parity trader driven by measured conditional
  tables, fires only when Wilson-lower-bound EV > 0 (on a fair RNG: never — and it says so).
- `tools/omniscan.py` — the live edge board: full payout surface + empirical probabilities +
  EV per contract, refreshed continuously.
