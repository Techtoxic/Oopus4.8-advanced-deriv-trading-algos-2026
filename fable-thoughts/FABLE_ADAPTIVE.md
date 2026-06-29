# FABLE — adaptive regime layer & the June-29 diagnosis

Returning user came back 2026-06-29: the JD100 digit bot won for ~3 days, then on day 4 the
win-rate flipped and a drawdown swept the stop. Question: retrain? did Deriv change the vol? what
was the unfinished 25%? This document answers all three with fresh measurements (scripts +
data in this branch).

## TL;DR
1. **Deriv did not change anything.** JD100 still ticks exactly 1/sec (measured live, no
   pagination). The payout grid still matches uniform-digit odds to ±0.15%.
2. **The edge is real and present right now.** Clean, purged, *no-selection* out-of-sample test
   (train first 12h, trade next 12h, fixed window-around-current-digit rule):
   **+2.35%/trade, t=5.0, 43,168 trades.** The null (decouple current digit) → −3.17%. The
   June-13 "selection noise" verdict was wrong: its control shuffled the price *steps*, which
   preserves the step-size distribution that *creates* the concentration — an invalid null.
3. **The drawdown was a regime, not decay.** The edge is entirely sigma-gated:

   | rolling σ | spot~ | EV/trade |
   |---|---|---|
   | ≤4.0 | ≤226 | +6% |
   | 4.0–4.3 | 236 | +1.1% |
   | 4.3–4.6 | 251 | **−0.4%** |
   | 4.6–5.0 | 267 | −0.6% |
   | 5.0–5.5 | 293 | −1.7% |
   | ≥5.5 | 340+ | −2.4% (full house margin) |

   EV crosses zero at **σ ≈ 4.45 (spot ≈ 250)**. JD100 has no price anchor, so spot/σ drifts up
   and down over days. Your 3 winning days were spot ≈ 220–235 (σ≈4.0). Day 4 spot drifted up,
   σ passed 4.5, and *the same rule that wins at σ 4.0 loses at σ 5.0* → straight into the stop.
   Today spot is back to 222 / σ≈4.1 → the edge is back (proof above).

## So: retrain, or Deriv, or adaptive?
- **Not Deriv.** Nothing changed structurally.
- **Not primarily retraining.** The edge is structural physics (a small per-tick step keeps the
  last digit near the current one). It is provable with a *fixed a-priori rule needing zero
  fitted tables*. The offset peak did soften modestly (0.1148 → 0.1061, still +6σ) so a periodic
  table refresh helps *calibration*, but stale tables are not why it lost money.
- **It is the missing adaptive layer (your "25%").** The bot kept trading through the σ>4.4
  zone where EV is structurally negative. The fix is a **regime gate**, not ML. This is a
  one-parameter problem (rolling σ with a known threshold), not a pattern-learning problem — a
  self-training neural net would be over-engineering and would itself break across regimes.

## Proof the adaptive layer fixes it (`adaptive_backtest.py`)
Same strategy, three ways, on the wide June-11 data (spot 223→440, both regimes):

| mode | trades | PnL ($1) | per-trade |
|---|---|---|---|
| **A static (ungated)** | 1,205,681 | **−$19,562** | −1.62% |
| **B hard σ-gate ≤4.25** | 37,941 | **+$471** | +1.24% |
| **C adaptive (trailing self-recalibrating table + σ-gate)** | 12,760 | **+$175** | +1.37% |

Per-day: static loses −$640 to −$2,580 on *every* high-spot day (601–613); the gate sits those
days out entirely (PnL 0) and only trades day 614 when spot fell into the zone. **The gate turns
a −$19.5k bleed into a profit by simply not trading the bad regime.** On today's good regime all
three are positive (+2.6–2.7%/trade); the gate costs almost nothing.

The exact live picker (conditioned OVER/UNDER selection + trailing self-recalibrating table +
σ-gate ≤4.25 + EV gate) validated OOS on fresh June-29 data: **+1.81%/trade, t=3.47, 55,320 trades.**

## What shipped: `tools/adaptive_sentinel.py`
The completed bot — base edge + the adaptive layer, live-tested on demo this session:
1. **Self-recalibrating offset table** — trailing window (`--trail`, default 60k ticks ≈16h),
   updated every tick. Always reflects the current regime → *no manual retrain ever needed*.
2. **Hard σ gate** (`--sigma-max 4.25`) — never trades the negative-EV regime. This is the fix.
3. **Live-EV gate** — contract priced from trailing table × live payout; the barrier is chosen
   *conditioned on the current digit* (fixed bug where it ignored the digit and always bought
   UNDER3).
4. **Quarter-Kelly sizing** from live return variance, capped at `--max-frac` of balance.
5. Stale-tick / RTT guards, second socket for settlement, session max-loss, demo guard,
   keepalive pings.

## Recommended operating procedure
- Run `adaptive_sentinel.py --trade --balance <bal> --ev-gate 0.01` 24/7. It auto-trades when
  σ≤4.25 and auto-sits-out when σ>4.25. No retrain cron needed (the trailing table self-updates);
  optionally widen `--trail` for steadier tables.
- **Do NOT remove the σ gate.** That single line is the difference between +1.2% and −1.6%/trade.
- Sizing: per the drawdown study (`metrics-engine` branch), quarter-Kelly ≈0.5% of balance,
  $50+ floor, expect 19-loss streaks and ~1,400-trade drawdowns — normal, not malfunction.
- Stop if executed payouts drift off the 1.953/2.427/… grid (Deriv repricing) or σ stays >4.5
  for days (regime closed — wait, don't fight it).

## Honest caveat
This is a low, high-variance edge that exists *only while JD100 spot is low*. It is real
(t=5 clean OOS) but it is a few percent per trade with deep drawdowns, not a money-printer; the
"$50→$20k in 6h intervals, 0 drawdown" path was a favorable low-σ regime + compounding, not a
floor. Size for the drawdowns, keep the gate on, and treat any positive day above σ≈4.4 as luck.

---

## CRITICAL UPDATE — selection-bias bug found & fixed in live testing (2026-06-29)

A 2h live demo run of the first `adaptive_sentinel.py` **lost −8.1%/$ while the model predicted
+2.1%** and halted at max-loss. Root cause (per-contract breakdown of the 1,779 live trades):
the `best_ou` picker maximised EV over **all 17** OVER/UNDER barriers, and a noisy trailing table
systematically over-estimates the mass of the **narrow high-payout** contracts → it kept buying
the traps:

| contract | model P | real P | PnL |
|---|---|---|---|
| UNDER3 (win {0,1,2}) | 0.319 | **0.284** | −$35 |
| OVER6  (win {7,8,9}) | 0.321 | **0.278** | −$22 |
| OVER7  (win {8,9})   | 0.215 | **0.197** | −$17 |
| OVER5 (win {6..9})   | 0.423 | 0.498 | +$12 |
| UNDER6 (win {0..5})  | 0.621 | 0.680 | +$6 |

This is the optimizer's-curse the June-13 session warned about — but it only affects the **narrow**
contracts, not the whole edge. **Fix:** `best_ou` is now restricted to the wide ~even-money
windows only (`OVER3/4`, `UNDER5/6`, win-size 5–6, prob 0.4–0.6), where a small table error can't
flip the sign.

**Validation of the fix (hostile, cross-period):** table **frozen from June-11 data, applied to
June-29 ticks 18 days later**, restricted ladder, σ-gated, gate 1% → **+1.94%/trade, t=3.18 over
22,718 trades.** (Also confirms retraining is not required for the edge — the June-11 offset table
still prices June-29 correctly; only the σ regime gate must be live.) Live re-confirmation traded
*only* UNDER5/OVER4 at win-rate 0.518 (above the 0.512 breakeven); longer live sample accumulating.

**Bottom line unchanged but sharper:** keep the σ≤4.25 gate AND the restricted wide-window ladder.
The narrow high-payout digit contracts are never to be traded on a fitted table.
