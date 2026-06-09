# MT5 EA Suite — Fable 5 Session (2026-06-09)

Nine compiler-verified EAs (MetaEditor 5, **0 errors / 0 warnings each**).
Copy `Experts/*.mq5` into your `MQL5/Experts` folder and compile, or use the
`.ex5` workflow you prefer. Everything draws its state on the chart.

---

## 1. `FFZ_v2.mq5` — Purple Bills follower, rebuilt exits
Your real-money FFZ with the four things it was missing:
- **Chop filter**: ADX floor + Choppiness Index ceiling. In chop it still closes
  on opposite arrows but refuses NEW entries (`InpChopBlocksFlip`).
- **Profit protection** (fixes "was up nicely, closed at a loss on the flip"):
  ATR initial SL → breakeven at +1R → 50% partial at +1R → chandelier trail →
  **giveback guard** (banks the trade if it surrenders 50% of peak profit).
- **COT bias**: `COT_OFF / COT_MANUAL / COT_AUTO_API`. Auto polls your COTAPI
  (`InpCOTApiUrl`, whitelist the URL in Tools→Options→EAs); in the Strategy
  Tester it automatically falls back to the manual bias input so you can
  still A/B the effect of bias in backtests.
- Risk-% sizing, session filter, spread cap, HUD.
Purple Bills stays `iCustom` (no source available) — everything else is built in.

## 2. `XU_SEMA_NoRepaint_EA.mq5` — the "settled arrow", made causal
The SEMA indicator repaints because the newest ZigZag arrow keeps moving until
an opposing window-extreme prints. This EA reproduces that exact lock rule
bar-by-bar, so it fires **once, at the moment the arrow would have settled,
and the signal can never un-print**. Backtest = live by construction.
- Entry scale (default 12) + higher SEMA scale (48) as trend filter.
- SL beyond the locked pivot, RR TP, BE/trail/giveback, ADX filter.
- Draws every locked pivot and entry so you can audit it visually.

## 3. `XU_SEMA_QTheory_EA.mq5` — Quarterly Theory system
Your `XU_SEMA_QFib_TZ_VP` indicator turned into a complete causal system:
- Anchor candles 00:15 / 06:15 M15 project QFib levels (0 / ±2 / ±2.5 / ±4).
- Fibonacci Time-Zone regimes (Omega→Zeta); trade only in chosen windows
  (default Gamma+Delta, 06:00–18:00 server).
- Entry = **QFib level sweep** (wick through, close back) → **SEMA pivot lock**
  in the sweep direction → enter. SL beyond sweep extreme, TP at next opposing
  QFib level (bounded by Min/Max RR).
- Draws fibs, anchor boxes, sweep markers, pivots, entries.

## 4. `GoldSilver_HedgeGuardian_EA.mq5` — hedging done right
Two engines:
- **ZONE_RECOVERY**: EMA+ADX seed, ATR-adaptive zone, capped hedge legs
  (default 4), and the critical check most grids skip: the **worst case is
  computed before the cycle starts and the cycle is refused/downsized if it
  exceeds your basket cap**. Basket TP in % of balance + basket trail +
  bounded-loss exit + cooldown.
- **RATIO_PAIR**: market-neutral gold/silver ratio mean reversion —
  short the rich metal, long the cheap one (notional-matched), enter |z|≥2,
  exit z≈0, stop |z|≥3.5, time stop. This is a desk-style hedge: you trade
  the spread, not the direction.
Shared equity guardian: max basket loss %, daily loss cap, cooldowns.
**Demo it for weeks first, as you said — especially Mode 1.**

## 5. `MSNR_v532_Fixed.mq5` — why it never traded, fixed
Not one bug — a probability product of stacked gates: 5/10 layer minimum
**AND** exact whitelist combos **AND** `DisableNormalMarket=true` (kills most
hours) **AND** premium/discount filter **AND** range-edge filter **AND** spam
lock **AND** final RR ≥ **5.0**. Each is sane; multiplied they ≈ never fire.
v5.32 changes:
- **Rejection funnel**: with `PrintDebug=true` every dead cluster now prints
  exactly which gate killed it (`FUNNEL: ... killed by RR 3.10 < MinRR 5.00`).
- Defaults relaxed to a tradeable-but-strict profile: MinRR 5→2, whitelist off,
  confirmations 5→4, `DisableNormalMarket` off, `DisableBuyInWeakHTF` off.
- All originals are inputs — set them back to reproduce v5.31 exactly.

## 6. `IFVG_EA_v2.mq5` — structure exits for the inversion EA
- Replaces floating-dollar SL/TP with **structure SL beyond the IFVG zone
  (± ATR buffer) + RR take-profit**, attached to the order (no naked positions).
- Risk-% sizing, HTF EMA trend filter, breakeven + giveback guard, spread cap.
- `InpUseStructureExits=false` restores legacy dollar behavior for comparison.

## 7. `MTF_RSI_Divergence_EA_v2.mq5` — your divergence EA, hardened
- SL at the divergence pivot (± ATR buffer), RR TP, risk-% sizing.
- **Conviction sizing**: multi-TF synced divergences get `SyncRiskMult` risk.
- Options: only-synced signals, skip hidden divs.
- BE + giveback management. Legacy USD exits available via input.

## 8. `QuantumGoldSilver_v4.mq5` — bug fixed + protected
- **Fixed a real math bug**: TP was `ATR × ratio` while SL was
  `ATR × slMult`, so "TP/SL ratio 2.0" actually delivered RR ≈ 1.33.
  Now TP = SL-distance × ratio, as the input promises.
- Added partial close at +1R, giveback guard, spread filter.
- Original "quantum/AI" logic untouched (it's a weighted indicator vote —
  treat the AI labels as marketing, the votes as a regime filter).

## 9. `SEMA_FVG_CISD_Confluence_EA.mq5` — the combined system
Your three favorite tools, fused causally:
**HTF FVG (location) → SEMA lock (structure) → CISD close (confirmation)**.
- Multi-TF FVGs (default H1+H4, D1 optional) tracked until mitigated.
- Setup arms when a non-repainting SEMA pivot locks inside a live FVG.
- Entry only on a CISD close beyond the delivery sequence's open.
- TP at next opposing HTF FVG when sensible, else RR. Full protection stack.

---

### Shared design principles (the "adaptive, still works in 6 months" part)
- Every distance is **ATR-multiplied**, never fixed points → self-calibrates
  to volatility regimes.
- Every EA sizes by **risk % of balance**, not fixed lots.
- Every discretionary input is exposed; defaults are sane.
- No EA averages down without a pre-computed bounded worst case.
- Everything visualized: pivots, zones, levels, regime, sweep markers, HUD.

### Suggested first backtests (M15–H1, 1-2 years, real ticks where possible)
| EA | Symbol | TF |
|---|---|---|
| FFZ_v2 | what you run FFZ on now | same |
| XU_SEMA_NoRepaint | XAUUSD | M15/H1 |
| QTheory | XAUUSD, EURUSD | M5/M15 |
| HedgeGuardian mode 2 | XAUUSD+XAGUSD | H1 |
| MSNR v5.32 | XAUUSD | M5 (PrintDebug on, watch the funnel) |
| SEMA_FVG_CISD | XAUUSD, GBPUSD | M15 |
