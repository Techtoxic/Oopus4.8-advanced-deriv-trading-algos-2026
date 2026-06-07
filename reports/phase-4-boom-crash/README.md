# Phase 4 — Boom & Crash

**Indices:** Boom drifts **down** on most ticks then occasionally **spikes up**; Crash drifts **up**
then occasionally **spikes down**. The number in the name (300/500/600/900/1000) is the *average*
number of ticks between spikes. Tradeable only via `MULTUP` / `MULTDOWN` (no binary spike bet exists).

## Spike-interval analysis (real ticks: 5,000 then paginated to 30,000)

Spikes detected as moves beyond 5σ in the spike direction. With 30k ticks:

| Symbol | n spikes | mean interval | median | min | max | CV | KS vs geometric (p) | interval ACF(1) |
|--------|---------:|--------------:|-------:|----:|----:|---:|--------------------:|----------------:|
| BOOM500   | 41 | 713.8 | 566.5 | 35 | 3017 | 0.86 | 0.73 | −0.10 |
| CRASH500  | 54 | 549.7 | 513.0 |  8 | 2680 | **1.00** | 0.42 | −0.07 |
| BOOM1000  | 22 | 1087.8 | 686.0 | 118 | 4804 | 1.08 | 0.83 | −0.11 |
| CRASH1000 | 24 | 1189.2 | 993.0 | 134 | 3738 | 0.69 | 0.33 | +0.10 |

**Mean intervals track the index names** (Crash500 ≈ 549, Boom1000 ≈ 1088, etc.), confirming the stated
design. But the interval distribution is **geometric / memoryless**:

- **CV ≈ 1** (coefficient of variation of a geometric distribution is ≈ 1) for the larger samples.
- **KS test vs geometric: p > 0.05** for every symbol — cannot reject the memoryless model.
- **interval autocorrelation ≈ 0** (−0.11 … +0.10): a long gap does **not** predict the next gap.

## The three exploit hypotheses — all fail

- **Step 1 — Periodicity:** none. The per-tick spike hazard is ≈ constant `1/N`. Spikes are *not* evenly
  spaced and do *not* avoid clustering; back-to-back-ish spikes occur (min interval 8–35 ticks observed).
- **Step 2 — Pre-spike pattern:** there is no detectable pre-spike signature because spike timing is
  memoryless — the generator decides each tick independently with probability `1/N`. Conditioning on the
  prior 10 ticks gives a hazard indistinguishable from the unconditional `1/N`.
- **Step 4 — "Minimum safe window":** **false.** A geometric process has positive probability of an
  arbitrarily short gap; the observed minima (8 for CRASH500, 35 for BOOM500) are just sample minima, not
  a hard floor. There is **no interval in which a spike cannot occur**, so no protected window to trade.
- **Step 3 — Post-spike fade:** between spikes the price drifts the *opposite* way of the spike
  (Boom drifts down ~97% of ticks, Crash drifts up). This drift is exactly compensated by the spike in
  expectation — that is how the index stays driftless-ish — and trading it via multipliers still pays
  commission and risks the next (unpredictable) spike. No +EV.

## Verdict

**No +EV.** Spike timing is memoryless (geometric, hazard ≈ 1/N), so it cannot be predicted, faded, or
front-run, and there is no safe window. The only access is leveraged multipliers, which add commission.
Data: [`../../boom-crash/spike_analysis_30k.json`](../../boom-crash/spike_analysis_30k.json).
