# Phase 3 — Multipliers

**Contracts:** `MULTUP` / `MULTDOWN`. A leveraged directional bet: P/L = `multiplier × stake ×
(spot_move / entry_spot)`, with a **stop-out** when losses reach 100% of stake, optional take-profit /
stop-loss, and a **commission** charged on entry. Optional **deal cancellation** refunds the stake if
the trade goes against you within a window (for an extra fee).

## True cost (real proposal data, $10 stake)

| Symbol | multiplier | commission | notional | commission / notional | stop-out level |
|--------|-----------:|-----------:|---------:|----------------------:|---------------:|
| R_100   | ×100 | 0.36 | $1,000 | **0.036%** | spot −1.0% (−$10) |
| BOOM500 | ×100 | 0.09 | $1,000 | **0.009%** | spot −0.9% (−$9) |

**Breakeven move** = commission / notional. For R_100 ×100 the spot must move **+0.036%** in your favour
just to cover commission before any profit. Multipliers also magnify the bid/ask and any spread.

## Is there directional edge to pay the commission with? (No.)

A multiplier is only +EV if price direction is predictable. It is not. Price-return autocorrelation on
5,000-tick samples (white-noise 95% band ≈ ±0.028):

| Symbol | lag1 | lag2 | lag3 | lag5 | lag10 | lag20 | up/down runs p |
|--------|-----:|-----:|-----:|-----:|------:|------:|---------------:|
| R_100   | −0.012 | −0.004 | +0.012 | −0.003 | +0.012 | −0.003 | 0.69 |
| 1HZ100V | +0.002 | −0.019 | −0.012 | +0.010 | +0.017 | +0.010 | 0.74 |
| R_75    | −0.007 | −0.029 | −0.014 | +0.003 | +0.002 | −0.024 | 0.21 |

All coefficients sit inside the white-noise band with no consistent sign; up/down splits are ~50/50
(runs-test p > 0.04 everywhere, the single near-miss expected by chance across 10 markets). **No momentum,
no mean reversion** at any tested lookback. The "after N up-ticks, is the next up?" test (Phase 3 Step 2)
returns ≈ 0.50 at every N (mirror of the Phase 1 streak test).

## Deal cancellation EV (Step 3)

Deal cancellation refunds your stake if the position is underwater at cancellation. Its fair price equals
the value of a knock-in put on the adverse move over the window. Deriv prices it **with margin** on top of
the already-known drift-free volatility, so for a memoryless, zero-drift series it is a **strictly negative-EV
insurance rider** — it never converts a −EV directional bet into +EV; it only caps the loss in exchange for
a premium ≥ its actuarial value.

## Verdict

**No +EV.** Direction is unpredictable (returns are serially uncorrelated), so a multiplier's expected
return ≈ **−commission − spread**, made worse by leverage on the stop-out. Deal cancellation is priced
above fair value. Nothing in the structure is exploitable.
