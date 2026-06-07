# Phase 7 — Vanilla Options

**Contracts:** `VANILLALONGCALL` / `VANILLALONGPUT`. You pay a premium and receive a payout proportional
to how far the spot finishes in-the-money — a long option position. Edge exists only if the **implied
volatility (IV)** baked into the premium differs from the **realised volatility (RV)** of the underlying.

## Pricing availability

Vanillas are not offered at the very short tick durations on R_100 (*"Trading is not offered for this
duration"*); they require longer/seconds-based durations. The economic test below does not depend on the
exact duration.

## IV vs RV (the only source of vanilla edge)

A long vanilla is +EV iff `RV > IV` (underlying moves more than priced); a short vanilla position is +EV
iff `RV < IV`. We measured RV directly from the tick data collected in Phases 1–5:

- Synthetic-index volatility is **constant and known by construction** (e.g. "Volatility 100" targets an
  annualised 100% — Deriv generates the series to a fixed σ). There is no stochastic-vol process, no
  vol-of-vol, and no clustering strong enough to create RV/IV gaps (ACF of |returns| within noise).
- Because Deriv both **generates the series and prices the option from the same σ**, IV is set equal to
  the generator's σ **plus a margin**. There is no informational asymmetry: the house knows RV exactly
  (it makes it) and will not sell an option for less than its fair value.

Consequently `IV ≈ RV + margin` structurally. A long vanilla pays the margin to the house; a short
vanilla is not directly offered to retail (you can only *buy* `VANILLALONG*`), removing the one side
(`RV < IV` ⇒ sell vol) that could in principle be +EV.

## Verdict

**No +EV.** The underlying's volatility is constant and known to the issuer, IV is set at RV plus a
margin, and only the long (premium-paying) side is offered. There is no IV/RV gap to harvest.
