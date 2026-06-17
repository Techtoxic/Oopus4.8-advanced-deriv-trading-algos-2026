# DRAWDOWN_REPORT.md — JD100 sentinel, demo account DOT93131975

Session date: 2026-06-17. Stake **$0.35**, contract DIGITOVER/UNDER 1-tick on JD100, demo only.
All numbers are reproducible from this branch: `edge_confirm.py`, `drawdown.py`, `balance.py`.
Source data: `live/sentinel_trades.csv` (20,278 settled trades) + `live/sigma_ticks.csv` (41,942 ticks).

---

## 0. Edge re-verification (asked for, done from scratch — not assumed)

I re-ran the edge from a hostile starting position before doing anything else. It holds.

- **`edge_confirm.py` reproduces exactly:** real lag-1 rule on 41,942 independent monitor ticks =
  **+6.26%/trade, t=8.43**; every placebo that breaks the current→next-digit link collapses to the
  house margin (decoupled −3.2%, shuffled −4.6%); both data halves independently significant
  (t=5.5 / 6.4); EV monotone as sigma falls.
- **Mechanism confirmed independently** (offset PMF rebuilt from raw ticks, not trusting the script):
  next-digit offset peaks at 0 = **0.1148 vs 0.10 chance (+10σ)**, decaying to the antipode (0.085).
  A digit-conditioned 4-wide OVER/UNDER window sits on that peak and captures **0.441 vs the 0.40**
  the payout grid assumes → ~+5.8% against the fixed 2.43× payout. Matches the replay. Not a
  look-ahead bug (a leak survives shuffling; this dies when the lag-1 link is broken).
- **Linchpin — live payouts:** I pulled live Deriv demo proposals in this exact regime; they match the
  assumed GRID to **±0.15%** (OVER/5 = 2.430 live vs 2.427; MATCH = 8.930 vs 8.929). Deriv is paying
  fixed uniform-digit odds while the digits are non-uniform — so the edge is real on demo here, not a
  fictional-payout artifact.

**The two surviving caveats are mechanical, not "is it real":** regime dependence (a JD100 re-base to
higher spot flattens the offset PMF back to the −3% line) and an execution haircut (replay +6.2% vs
live realised +3.72%). Both are characterised below.

---

## 1. Drawdown (20,278 trades, $0.35 stake, single realised path)

| metric | value |
|---|---|
| win rate | 42.79% |
| cumulative PnL (CSV) | **+$263.68** |
| mean per trade | +$0.0130 (**+3.72% of stake**), t = 3.62 |
| **max drawdown** | **−$28.09** (from a peak equity of +$49.66) |
| drawdown shape | **513 trades down to the trough, then 907 trades to recover** (≈1,420 trades / ~5–6h underwater at ~250 trades/h) |
| **longest losing streak** | **19 consecutive losses** |

The edge is real but **low and very high-variance**: per-stake return std = 1.46 (fat tails from the
8.9× MATCH payouts). The equity curve grinds up with deep, long drawdowns — a −$28 dip that took
~1,420 trades to clear is normal, not a malfunction.

## 2. Risk of ruin (Monte-Carlo, order-resampled — not the single lucky path)

The single historical path survived a $30 start (min balance $25.90) only because the big drawdown
arrived *after* a cushion was built. Resampling trade order (4,000 paths, fixed $0.35 stake, horizon =
full sample) shows the true risk:

| start balance | P(ruin) | 5th-pctile worst balance |
|---|---|---|
| $5  | **61.4%** | −$23.90 |
| $10 | 35.0% | −$18.49 |
| $15 | 21.9% | −$13.43 |
| $20 | 12.9% | −$8.38 |
| **$30** | **4.9%** | +$0.58 |
| **$50** | **0.8%** | +$20.36 |
| $100 | 0.0% | +$71.15 |

Ruin = balance can no longer cover one $0.35 stake. **A $30 account has ~5% blow-up risk at $0.35
fixed stake; $50 drops it under 1%.** Anything below $20 is unsafe (>13%).

## 3. Kelly & safe sizing

From the realised per-stake return distribution (mean +0.0372, std 1.462):

- **Full Kelly fraction f\* = 0.018** (1.8% of bankroll per bet). Tiny because variance dominates a
  small edge. **Quarter-Kelly = 0.0047.**
- A flat $0.35 stake equals full-Kelly only at a **~$19 balance** and quarter-Kelly at **~$77**.
  Below ~$19 the $0.35 stake is *over* full-Kelly → that is why sub-$20 starts ruin.

**Recommendation (demo):**
- **Minimum safe starting balance: $50** (≈0.8% ruin). $30 is "playable but exposed" (~5%); treat $50
  as the floor.
- **Max safe stake at a given balance ≈ quarter-Kelly ≈ 0.5% of balance:** ~$0.25 at $50, ~$0.15 at
  $30. The current **$0.35 flat stake is fine from ~$75+**; below that it is progressively aggressive.
- **Scale the stake with balance, don't keep it flat.** Increase stake only after balance clears the
  next ~$75 step (so $0.35→$0.50 around $100, etc.), keeping stake ≤ ~0.5% of equity.
- **Discount all of this heavily:** Kelly assumes the +3.7% edge persists. It is regime-bound. If spot
  re-bases, the edge → −3% and any Kelly sizing becomes a guaranteed bleed. Size for "edge might
  vanish tomorrow," not for the in-sample mean.

## 4. Sigma ↔ equity (Objective 3/4) — with the look-ahead removed

**Important honesty note:** a first pass using a *centered* sigma smoother showed a spectacular split
(trend-up −2.4%/tr vs trend-down +10%/tr). **That was look-ahead** — the centered window used future
ticks. Recomputed **strictly causally** (trend = sigma[i] − sigma[i−k], past only), the effect is real
but far smaller:

| signal (causal) | rising / up | falling / down |
|---|---|---|
| 1-tick direction | +2.35%/tr | **+6.80%/tr** |
| 40-trade trend | +2.41%/tr | **+5.37%/tr** |

- **Falling sigma is genuinely better than rising** (~+1.5–3 pp/trade), causal. Confirmed.
- **Most of it is a proxy for sigma *level*, not an independent trend signal.** Within fixed sigma
  bins the trend effect mostly shrinks and is inconsistent (in the 3.65–3.80 bin, rising slightly
  *beats* falling). The dose-response (lower sigma ⇒ stronger edge) is the dominant real effect.
- **EV by sigma bin:** 3.5–3.6 +7.0% · 3.6–3.7 +3.6% · 3.7–3.8 +2.8% · **3.8–3.9 −0.5%** · 3.9–4.0 +6.6%.
  The only soft spot is a shallow ~3.8–3.9 dead zone (noisy, small n).

**Pause filter (causal, back-tested):** skipping trades when sigma rose over the last 40 trades keeps
10,163 of 20,278 trades and lifts mean from +3.72% → **+5.05%/trade**. A real but modest improvement,
and it halves trade count. Recommended form for `sentinel_v2.py`: an *optional* causal gate
`--pause-if-sigma-rising` (skip if `sigma_now > sigma_{40 ago}`). I did **not** hard-wire it into the
bot, because (a) the gain is modest, (b) it is partly redundant with the existing sigma≤4.35 gate, and
(c) it must never use a centered/future window. Spec + the validated numbers are in `drawdown.py`.

## 5. Balance tracking & reconciliation (Objective 1)

`balance.py` pulls the **real** balance over the authenticated WS (`{"balance":1}` → confirmed working,
$395.37 on DOT93131975) instead of trusting CSV PnL.

Reconciling the committed run against the prior assumed $226.57 start:
- CSV cumulative PnL = **+$263.68**
- Real balance move = **+$168.80**
- **Discrepancy = −$94.88 → FLAGGED (>$1).** CSV PnL is **not** a reliable balance proxy.

The historical run can't be reconciled exactly because **no baseline balance was snapshotted at its
first trade** — that is the precise gap this tool closes. Workflow going forward: `balance.py
--snapshot` before trading, `balance.py --reconcile` after. A fresh bounded run demonstrating an exact
reconciliation is in §6.

## 6. Fresh bounded run — exact reconciliation in action (this session)

I snapshotted the baseline, ran `sentinel_v2.py --stake 0.35 --minutes 15` live on demo, then read the
real balance back. This is the Objective-1 workflow proving itself:

| | value |
|---|---|
| baseline (snapshotted at start) | $395.37 |
| end balance (live WS) | $462.91 |
| **real balance move** | **+$67.54** |
| contracts bought (bot session log) | 897 |
| trades that reached the CSV | **435** (PnL +$35.81) |
| **discrepancy (balance_move − csv_pnl)** | **+$31.73 → FLAGGED** |

The gap is the **~462 contracts that were bought and settled on Deriv but never polled into the CSV**
before the 15-min window closed — the exact failure mode that made CSV PnL untrustworthy. The *real*
result (from balance) is **+$67.54 over 897 contracts ≈ +21.5% of stake**, in a notably low-sigma
window (spot fell further, sigma ~3.3). That is a strong but **short, high-variance** sample at the low
end of the dose-response curve — directionally consistent with §0/§4, not independent proof of a larger
edge. Lag histogram on the logged trades: **430/435 settled at decision-tick+1** (T+1 holds).

Takeaway reinforced: **always read PnL from the API balance with a snapshotted baseline; the CSV
undercounts whenever the process stops before the settler drains.**


---

## Bottom line

- The edge is **real on demo in this regime** (independently re-verified, live payouts confirmed).
- It is **low and fat-tailed**: +3.7%/trade, max DD −$28, 19-loss streaks, ~1,400-trade recoveries.
- **Safe demo floor ≈ $50** ($0.35 stake, <1% ruin); $30 ≈ 5%; <$20 unsafe. Stake ≈ ≤0.5% of balance.
- Sigma/equity: falling-sigma and low-sigma are genuinely better; a causal pause-if-rising filter adds
  ~+1.3 pp/trade. The dramatic version of that finding was a look-ahead artifact and is corrected here.
- Balance must be read from the Deriv API, not CSV — the CSV drifts ~$95 from reality on this run.
- The one thing that ends all of it: a JD100 **spot re-base**. Monitor it; everything above assumes the
  low-spot regime persists.
