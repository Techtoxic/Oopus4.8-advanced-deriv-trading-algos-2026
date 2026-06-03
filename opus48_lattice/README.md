# Opus 4.8 — Lattice Microstructure: Built, Tested, Falsified (2026 session)

This folder is an independent, build-and-falsify study of a six-layer "lattice
breakdown" strategy for **Deriv last-digit contracts** (mini-clusters,
tapped/untapped digits, frequency graphs, adaptive regression, a game-theory
convergence table, Kelly sizing, and a feedback/drift loop).

The brief was explicit: **challenge it without bias** — implement the whole thing,
test it on real data in a sandbox, improve what was missed, and let the evidence
decide. So we built the bot, tested its *own* signals (not generic proxies), added
a planted-edge control that the prior research lacked, and pushed working code +
full docs + backtests.

## ⭐ Bottom line

> The framework's one true observation — "~90% of untapped digits fill on revisit"
> — is a restatement of the geometric law `1 − 0.9^K` and carries **zero per-tick
> edge**. `P(next digit = d | d untapped)` = **0.100** on all 7 indices (tight
> Wilson-99 CIs). No memory to order 3 (mutual information on the shuffle-null
> band). Layers 2–3 don't beat break-even; the regression is worse than "next =
> last." Sized honestly the bot trades **zero**; forced to trade it converges to
> **ROI ≈ −(house edge)**. The *identical* engine compounds a planted-edge stream
> from $1k to ~$3.8e13 — proving the pipeline is sound and the fair market, not the
> code, is what removes the edge.

Full reasoning and numbers: **[`FINDINGS_LATTICE.md`](FINDINGS_LATTICE.md)**.
Formal layer-by-layer spec: **[`STRATEGY_SPEC.md`](STRATEGY_SPEC.md)**.

## Layout

```
FINDINGS_LATTICE.md      the framework-specific verdict (math, evidence, figures)
STRATEGY_SPEC.md         the full 6-layer specification mapped to code
lattice/                 the bot (6 layers + orchestrator + live paper-trader)
  layer1_clusters.py     non-overlapping mini-clusters & strike zones
  layer2_distribution.py frequency / entropy / skew signal scoring
  layer3_regression.py   adaptive rolling-window regression (walk-forward shift)
  layer4_gametheory.py   40-contract convergence table + compound-chain intersection
  layer5_risk.py         adaptive fractional-Kelly (EV<=0 -> stake 0)
  layer6_feedback.py     logging + drift detector + calibration
  engine.py              full per-tick pipeline (belief & execution modes)
  live_bot.py            live Deriv paper-trader (no money)
  common.py              data, digit extraction, contract universe, payouts
validation/              the science
  fetch_data.py / fetch_payouts.py   fresh live ticks + measured payouts
  structural_invariant.py            40-contract / 20-20 proof + compound chains
  digit_uniformity.py                chi2 + split-half stability + measured EV
  test_untapped_fill.py              tautology vs exploitability (the core test)
  test_higher_order.py               mutual information to order 3 vs shuffle null
  test_skew_predictiveness.py        Layer-2 signals settled lag-2
  test_regression_location.py        Layer-3 vs persistence baseline
  controls.py                        PLANTED-EDGE + null calibration (proves detector)
  plots.py                           figures -> results/*.png
  run_all.py                         reproduce everything
backtest/backtest_lattice.py         full pipeline, live payouts, lag-2 settlement
data/                                fresh gzipped ticks (7 indices) + payouts.json
results/                             pre-computed outputs + figures
```

## Quickstart

```bash
pip install -r requirements.txt

# Reproduce the whole study (uses cached data/; add --fetch to re-pull live)
python validation/run_all.py

# Watch the house edge bleed on the LIVE feed, no money:
python -m lattice.live_bot --symbol 1HZ100V --max-ticks 3000
# Honest sizing simply observes (Kelly -> 0):
python -m lattice.live_bot --symbol 1HZ100V --honest --max-ticks 3000
```

## Figures
- `results/fig_untapped_fill.png` — measured fill sits exactly on `1 − 0.9^K`.
- `results/fig_exploitability.png` — `P(next|untapped)` straddles 0.10 on every index.
- `results/fig_house_edge_smile.png` — measured house edge per contract, never ≤ 0.
- `results/fig_equity_curves.png` — real data bleeds; planted-edge control compounds.
- `results/fig_controls.png` — detector calibration matrix.

## Relationship to the prior repos
This agrees with the earlier "no edge on synthetics" conclusion but reaches it by a
different and stronger route: it builds and tests the friend's *specific* system,
measures memory to order 3, and — crucially — proves its own detector with
planted-edge controls. It also corrects two concrete errors (the 42→40 contract
count; a payout-matching bug that faked a positive EV). If you can falsify any
claim here, every script is runnable — change it and show the passing test.
