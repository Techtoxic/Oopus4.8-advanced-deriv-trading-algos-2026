# Phase 5 — Higher / Lower (Rise / Fall, CALL / PUT)

**Contracts:** `CALL` (Rise) / `PUT` (Fall). Win if the exit spot is higher/lower than the entry spot
after the chosen duration. Also `CALLE`/`PUTE` (Rise/Fall with equals).

## Duration & house edge (real proposal data)

Tick durations are restricted to **5–10 ticks** for Rise/Fall on the volatility indices (1–3 tick
durations are rejected: *"Number of ticks must be between 5 and 10"*). At-the-money (barrier `+0.0`):

| Symbol | duration | payout ($10 stake) | implied p_win | **house edge** |
|--------|---------:|-------------------:|--------------:|---------------:|
| R_100   | 5t | 19.23 | 0.50 | **3.85%** |
| 1HZ100V | 5t | 19.23 | 0.50 | **3.85%** |

The edge is the same 3.85% as Digit Even/Odd — the at-the-money Rise/Fall is structurally identical to a
50/50 binary with a 1.923× payout. (Longer/longer-dated durations and non-zero barriers shift `p_win` and
the payout together along the same favourite-longshot curve documented in Phase 1.)

## Momentum vs mean reversion (Steps 2–3)

Because settlement depends on the **sign of the cumulative return** over the duration, Rise/Fall is
exploitable only if returns have serial structure. They do not (5,000-tick samples, white-noise band ±0.028):

- Return autocorrelation at lags 1,2,3,5,10,20 all within noise, no consistent sign (see Phase 3 table).
- "After k consecutive up-moves, is the next up?" ≈ **0.50** for k = 3,5,7 across all markets
  (the Phase 1 streak/momentum test, mirrored on the over/under split): scattered p-values at the
  multiple-comparisons false-positive rate, no replicable direction.
- Mean-reversion (betting the opposite) is the exact complement and equally ≈ 0.50.

## Directional bias across markets (Step 4)

Up/down counts over 5,000 ticks are ~50/50 for every index (R_10/25/50/75/100, all 1HZ variants);
runs-test p-values > 0.04 throughout (one near-miss expected by chance). **No index shows a persistent
directional bias** over 1,000–5,000-tick windows.

## Verdict

**No +EV.** Rise/Fall carries a 3.85% at-the-money edge, and the sign of the return is a fair coin with
no exploitable momentum or reversion at any lookback or duration.
