# CRASH1000 live candidate audit — September 12

The user's completed 20-contract demo run lost $6.86: six wins at +$1.19 and fourteen
losses at -$1. All positions closed. The bot stopped at its 20-contract limit, not the
$10 realized-loss cap. No additional orders were placed during this review.

## Execution findings

All entries were in the tested state around spot 6002–6004, with the unchanged 4% growth
rate and relative barrier 2.3454e-6. Re-fetching the closed contracts and comparing their
complete audit paths established:

* All six winners automatically closed after exactly 20 protected ticks and paid $2.19.
* All fourteen losses had their first displayed-price model breach at their exit tick.
* The 300 protected movements contained seven near-boundary movements, with no observed
  path/model contradiction. This is limited execution evidence, not universal proof of
  internal rounding behavior across states and dates.
* Historical API data matched all 335 unique timestamps represented in the audit records.
* Two knockouts occurred on tick 20 itself. That final tick must also survive to receive
  the take-profit payout. They are correctly classified as losses.

Example: the first losing contract moved from 6002.627 to 6002.643, a .016 movement,
greater than the approximately .01408 relative barrier distance. The broker's displayed
upper boundary was 6002.6411. This is an ordinary valid knockout, not a late sell bug.
`ContractAlreadySold` in terminal validation fields is expected after settlement.

## What the research does and does not predict

The candidate predicts a conditional probability of surviving 20 steps, not a future
price or time at which the next barrier will break. Its historical survival estimate
was about 49%; losses on roughly half of contracts are compatible with that hypothesis.
At $2.19 returned for a win and zero for a loss, breakeven survival is 1/2.19 = 45.66%.

Six wins out of twenty is 30%, below breakeven. Under an illustrative independent-trade
model with p=.490068, the probability of six or fewer wins is approximately 6.87%.
Dependence and changing state can invalidate that approximation. This run does not
establish profitability, and is not large enough to conclusively reject the historical
expected-return estimate either. Do not reset risk caps or increase stakes to recover it.

There is no guaranteed safe exit before an unknown breach. Shorter holds reduce both
the chance of knockout and the winning payout. Selecting an exit by looking at these
twenty realized failure times is hindsight; shorter holds would also change subsequent
entry times, so the same twenty entry times are not a valid new-policy backtest.

## Research/live cadence mismatch checked

The old replication used fixed-spaced decisions; the live audit re-enters after early
knockouts. This was an unmodeled policy difference. A matched indicative replay with a
one-tick decision delay after closure retained a positive point estimate on the older
sample (+6.46% versus fixed-grid +7.32%). It does not support blaming this run's loss on
immediate re-entry.

A separately downloaded pre-session window excluded the actual twenty trades and older
discovery data. Only four hours in that window met the spot condition. In the live-style
re-entry replay, hypothetical fixed-horizon outcomes were:

| Protected ticks | Trades | Win rate | Rounded winning return | Modeled mean return |
|---|---:|---:|---:|---:|
| 5 | 1,982 | 83.75% | $1.22 per $1 | +2.18% |
| 10 | 1,256 | 69.67% | $1.48 | +3.11% |
| 15 | 958 | 57.52% | $1.80 | +3.53% |
| 20 | 795 | 48.18% | $2.19 | +5.51% |

These are exploratory indicative-price simulations, not executed results. Four hours,
several compared policies, hypothetical sale timing and cent rounding are significant
limitations. Automatic take-profit behavior at the shorter targets was not verified.
The table does not establish a better exit or justify changing the bot to chase this
sample. The 20-tick target and fixed risk limits remain unchanged.

## Software correction

The previous `spec_mismatch` field checked parameters but did not reconcile the actual
path with the knockout model. The bot now performs that check, halts on mismatches or
unavailable audit data, and keeps full audit paths in the file while printing concise
settlement summaries. This improves audit validity and visibility; it does not transform
an unproven statistical edge into guaranteed profit.
