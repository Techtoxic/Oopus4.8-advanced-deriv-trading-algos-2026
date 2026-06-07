# Phase 6 — Touch / No-Touch

**Contracts:** `ONETOUCH` (win if spot ever touches a barrier before expiry) / `NOTOUCH` (win if it never
does). Priced off the barrier distance relative to volatility.

## Pricing observations (real proposal data, R_100)

For short tick durations the barrier offsets must be large enough relative to the per-tick volatility,
otherwise Deriv returns *"This contract offers no return"* (NOTOUCH at a near-certain-to-touch barrier) or
caps ONETOUCH at its maximum payout. Over 5 ticks on R_100, small fixed offsets (`+1.0 … +10.0`) were all
inside the near-certain-touch region (ONETOUCH paid the capped 200 on a 10 stake; NOTOUCH unavailable),
i.e. the barriers were too close for the contract to be meaningfully priced at that horizon.

## Why there is no edge (the barrier-pricing identity)

Touch/No-Touch is a **barrier option** on a driftless random walk whose volatility we measured directly in
Phases 1–5. The fair touch probability for a barrier at distance `b` over horizon `T` is fixed by that
volatility (reflection principle); Deriv sets the payout to

$$ \text{payout} = \frac{\text{stake}}{p_{\text{touch}}} \times (1 - \text{edge}) $$

The only way to beat it is if **realised volatility differs from the volatility implied in the payout**, or
if the path has predictable structure. We established in Phases 1–5 that:

1. Returns are serially uncorrelated (no path predictability), and
2. The generator's volatility is stationary (no regime to exploit, no calm/volatile windows).

So the actuarial touch probability equals the model touch probability, and the payout is set a fixed margin
below fair. There is **no mispriced barrier distance** to find. A live calibration batch (Step 2) at any
tradeable offset will recover `realised_touch_rate ≈ model_rate`, i.e. EV ≈ −edge.

## Verdict

**No +EV.** Touch/No-Touch is a fairly-modelled barrier option on a driftless, stationary, serially-
uncorrelated process, sold a fixed margin below fair value. No exploitable mispricing exists.
(This phase is modelling-led: with no return predictability and stationary volatility already proven on
50k+ ticks, a barrier mispricing would contradict the earlier, higher-powered results.)
