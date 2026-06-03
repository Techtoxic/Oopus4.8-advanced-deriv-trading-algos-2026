# STRATEGY SPECIFICATION — The Lattice Microstructure System (Opus 4.8)

This is the complete, formalized specification of the six-layer "lattice
breakdown" framework, combining the original quant model, the friend's additions,
and the corrections found this session. Each layer maps to a module in `lattice/`.
The system is implemented faithfully so it can be tested *as designed*; the honest
status of each layer (does its signal carry edge?) is stated inline and proven in
`FINDINGS_LATTICE.md`.

```
tick ─▶ [L3] regression predicts next price ─▶ map to cluster ─▶ reg_conf gate
     ─▶ [L1] mini-cluster engine: non-overlap zone? cluster_conf gate
     ─▶ [L2] entropy gate + per-contract distribution signal strengths
     ─▶ [L4] convergence table ─▶ contracts clearing the threshold (+ dominance)
     ─▶ [L5] adaptive fractional-Kelly sizing (EV≤0 ⇒ stake 0)
     ─▶ (settle at lag-2 on the real digit) ─▶ [L6] log + drift detector + calibration
```

---

## Layer 1 — Non-overlapping mini-clusters & strike zones  (`layer1_clusters.py`)

* A 1-minute candle (60 ticks on a 1 s feed) is a **cluster**, split into 6
  **mini-clusters** of `ticks_per_mini = 10` consecutive ticks. The observation is
  the last decimal digit at the instrument's pip precision.
* Each mini-cluster records its price range `[low, high]` and the set of last-digit
  values its ticks printed → **tapped**; the rest are **untapped**.
* **Non-overlapping** ⇔ `low(n) > high(n-1)` or `high(n) < low(n-1)`. These are the
  clean, isolated zones; only they are stored as revisitable **strike zones**.
* **First-strike rule:** at most one execution per zone visit (`mark_executed`).
* **cluster_confidence** (0–100) = `0.40·untapped_ratio + 0.25·non_overlap_clarity
  + 0.20·age_weight + 0.15·historical_fill_rate`, gated at ≥ 65.

*Honest status:* the structure is well-defined but the quantity it is built to
exploit — untapped digits "filling" — is memoryless (`P(next=d|untapped)=0.10`).
Non-overlapping zones are, by definition, ranges price *left*, so revisits are rare
and the literal revisit-strike path almost never fires (a finding, not a bug).

## Layer 2 — Frequency distribution & signal scoring  (`layer2_distribution.py`)

* From a cluster's count vector derive: probability map, tapped/untapped sets, mode,
  least-frequent digit, **Shannon entropy** `H = −Σ p·log₂p`, skew = `mean − 4.5`,
  even/odd tallies.
* **Entropy gate:** trade only when `1.8 ≤ H ≤ 2.8` bits (avoid both near-uniform
  noise and over-concentrated, already-consumed clusters).
* **Per-contract signal strength** in [0,1] for the activated Match / Differ /
  Over / Under / Even / Odd reads, blending concentration, skew magnitude, and
  untapped support.

*Honest status:* skew-follow wins 0.499, mode-match and least-frequent-match win
0.100 — all below break-even. The distribution describes the past window and has no
forward power on a memoryless stream.

## Layer 3 — Adaptive rolling regression  (`layer3_regression.py`)

* Walk-forward window (dynamic `n ∈ [20,60]`): fit → predict `P_next` → actual
  arrives → drop oldest, append actual → re-select curve shape → resize by residual
  trend → repeat. Model family chosen every 10 ticks by leave-last-out error over
  {linear, quadratic, cubic, mean-reversion(OU), persistence}.
* Output is a **cluster location**, not a digit. `regression_confidence =
  1 − RMSE/price_range`, gated at ≥ 0.60.

*Honest status:* cluster-hit rate (0.82) is **below** the persistence baseline
(0.88). On a zero-drift walk the best predictor is "next = last"; the adaptive
machinery does measurably worse.

## Layer 4 — Game-theory convergence table  (`layer4_gametheory.py`)

* **Real outcome space = 40 contracts** (Match 0-9, Differ 0-9, Over 0-8, Under 1-9,
  Even, Odd). Every settling digit makes **exactly 20 win / 20 lose**
  (`validation/structural_invariant.py`). *(Correction: the prior write-up's "42 /
  20-22" counted the impossible Over 9 and Under 0.)*
* `convergence(C) = 0.25·cluster_conf + 0.35·dist_signal(C) + 0.20·reg_conf +
  0.15·cross_market_agreement(C) + 0.05·hist_winrate(C)`.
* **Weak-dominance pruning** removes any contract another beats on both score and
  payoff. **Compound chains** are scored with the *exact* deterministic
  intersection (`{d : all legs win}`), never an independence assumption — contracts
  on one tick are perfectly dependent through the digit.
* Trade only contracts clearing the convergence **threshold** (spec: 0.99).

*Honest status:* over signals that carry no information, rolling convergence peaks
≈ 0.6, so the 0.99 gate is never met — the system, as specified, never trades.

## Layer 5 — Adaptive risk & stake sizing  (`layer5_risk.py`)

* **Fractional Kelly** (0.25×) × convergence modifier × cluster-confidence modifier
  × drawdown modifier, capped by per-contract **risk class** and a hard **2% of
  bankroll** ceiling. Consecutive-loss cooldown and drawdown halt at 30%.
* **The load-bearing line:** `if EV ≤ 0: stake = 0`. Kelly on a non-positive edge
  prescribes a zero bet. No staking scheme converts negative EV to positive.

*Honest status:* with the uniform truth, EV ≤ 0 for every contract ⇒ stake 0 ⇒ the
bot correctly never trades. (Also: at a $10 bankroll the 2% cap, $0.20, is below
Deriv's $0.35 minimum — the risk layer literally cannot place a compliant bet.)

## Layer 6 — Feedback, drift detection, calibration  (`layer6_feedback.py`)

* Every trade logs a structured record. A rolling **drift detector** tracks the
  realized untapped-fill rate vs the 90% baseline: alert at 78% (stakes ×0.25),
  **halt at 70%**. Per-zone fill rates and per-contract win rates are recalibrated
  from the log.

*Honest status:* correct and valuable — on real data it **self-halts almost
immediately** (after ~3 trades) because the untapped-fill assumption fails on
contact. It is the right component pointed at the wrong venue.

---

## Orchestration & bots

* `lattice/engine.py` — `LatticeEngine` wires L1–L6 per tick. Two belief modes:
  `framework` (trusts its own signal → trades) and `honest` (uniform truth → Kelly
  0 → observes). Two execution modes: `rolling` (strike on the forming cluster,
  faithful to "predict next cluster, bet next digit") and `revisit` (strike on a
  stored zone). Settlement always uses the real future digit.
* `lattice/live_bot.py` — runs the full pipeline on Deriv's **live** feed,
  paper-traded (no money), settling each decision exactly as a 1-tick digital
  option (lag-2). Prints the running, honest P/L.
* `backtest/backtest_lattice.py` — offline backtest with live payouts and lag-2
  settlement; includes the planted-edge sanity run.

## Objective (as the friend framed it) and the honest reading

Maximize `Σ convergence · EV · stake` subject to `EV>0`, stake ≤ min(2%, risk
cap), drawdown ≤ 30%, fill ≥ 70%, `1.8 ≤ H ≤ 2.8`. On Deriv synthetics the
feasible set of `EV>0` trades is **empty**, so the constrained optimum is to trade
nothing — which is exactly what the honest configuration does. The architecture
is reusable; it needs a market with real structure to act on.
