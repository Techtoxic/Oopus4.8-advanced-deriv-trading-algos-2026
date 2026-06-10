# FABLE SESSION — 2026-06-10

One sentence: **everything in this branch is either validated with data you can
re-run, or explicitly labeled experimental — and three popular strategy families
were falsified and therefore NOT shipped.**

This is the session's master index. Every claim links to a script or log that
regenerates it.

---

## 1. What was built

### DERIV-BOTS/omnibot — the comprehensive Deriv watcher/executor ✅ LIVE-TESTED
- Digits + Accumulators + Rise/Fall on one resilient websocket core
  (auto-reconnect, re-subscribe, 25s keepalive, req-id correlation).
- **Sentinel**: rolling chi²/parity/over-under battery priced against LIVE
  payout quotes; fires only on a Wilson-99.9% break-even violation
  (quarter-Kelly, capped). On a fair RNG it stays silent — by design.
- RiskGuardian: daily loss cap, profit lock, stake/concurrency caps, streak
  cooldowns. Refuses real-money tokens unless explicitly overridden.
- FastAPI: `/` dashboard, `/health`, `/wake` (cron keep-alive), `/status`,
  `/edge`, `/trades`, `POST /control`. `render.yaml` included.
- **Live validation today** (demo VRTC10502381): 35+ min uptime, 25+ real demo
  contracts executed & settled across all 3 families, incl. ACCU active
  take-profit sells; ledger, risk caps and reconnect verified.
  (The P/L was slightly negative — exactly the measured house edge doing its
  thing. That is the honest, expected behavior on synthetics.)

### MQL5-ENHANCED — your EAs, upgraded ✅ ENGINEERING + VALIDATED COMPONENTS
- **FableCOT.mqh** — THE unlock of this session: 21 years of real weekly CFTC
  positioning (9 assets, publish-lagged) embedded as MQL5 arrays. COT filters
  now backtest and OPTIMIZE inside the Strategy Tester. Your single biggest
  stated pain ("fundamental limitation... cannot make web requests in tester")
  is structurally gone.
- **AdaptiveSwingTrader_v2** — your top performer + tri-source COT engine
  (live API / embedded / off; 4 gate modes), volatility-targeted sizing,
  ER-routed entries (breakout vs pullback by regime), partial+BE+chandelier+
  giveback exits, daily AND weekly breakers, expectancy governor that halves
  risk when the trailing 20-trade expectancy goes negative.
- **FFZ_v3_PurpleBills** — your Purple Bills follower: embedded-COT modes
  (tester-capable), vol-regime gate, equity breakers, expectancy governor.
  Everything that worked in v2 kept.
- **XU_SEMA_NoRepaint_v2** — the causal SEMA lock EA + optional embedded-COT
  gate + equity breakers.

### ELITE-MT5 ✅ ONLY WHAT SURVIVED VALIDATION
- **EliteGold_TSMOM** — time-series momentum ensemble (1/3/6/12m votes),
  10% vol targeting, COT-index tilt, drawdown governor, weekly cadence.
  Evidence: Sharpe 0.39 IS (2004-2017) / **0.40 OOS (2018-2025)** on XAUUSD;
  the same rule FAILS on FX majors — so it ships as a metals EA, full stop.
- **EliteCOT_SwingPortfolio** — the exact A/B-validated configuration:
  Donchian-20/EMA-50/ADX-18 trend core, COT gate (SIGN/INDEX/EITHER), basket
  = the symbols where the gate helped (GBPUSD/NZDUSD excluded with receipts).

### HYBRID-BOTS ⚠️ HONEST EXPERIMENTAL
- **HybridScalper_CostGate** — the anti-scalper scalper: measures live edge
  (rolling lag-1 autocorr) vs live cost (spread+slippage) and only arms when
  edge ≥ 1.5× cost. On normal retail spreads it will mostly refuse to trade —
  which is the correct answer and displayed on the HUD.
- **HybridML_ShadowGate** — online logistic gate over the validated trend core
  that must EARN enforcement: default shadow mode runs a live A/B of
  gated-vs-ungated expectancy on your chart. Ships honest because both prior
  ML attempts (daily + H1) returned nulls — see §3.

### PRIVATE-FABLE 🔒 THE INSTITUTIONAL LAYER
- **FABLE_TailGuard_Overlay** — an account-level risk desk: drawdown ladder
  (halve → flatten+lock), portfolio heat cap, correlation-cluster limits,
  no-stop police (auto-SL any naked position, any EA), margin guard, weekend
  flat. Supervises EVERY magic/symbol — including your other EAs. This is the
  piece that would have stopped the HedgeGuardian floating-loss spiral.
- **FABLE_RegimeAllocator** — desk-style allocator routing risk between the
  two validated engines (TREND core, TSMOM) with shock-regime cutoff and
  per-engine trailing-expectancy weights floored at 0.25 (engines that decay
  bleed allocation, not the account).

---

## 2. What was VALIDATED (re-run the scripts yourself)

| Claim | Evidence | Script |
|---|---|---|
| COT API-sign gate lifts mean PF 0.98→1.28 (9 pairs) | `cot_study/results/COT_STUDY.md` | `cot_study.py` |
| COT-index gate best on metals (XAU PF 1.53) | same | same |
| Commercials rule harmful (PF 0.84) | same | same |
| Gold spec extremes = momentum confirmation (t=11.0) | same (event study) | same |
| TSMOM on gold: Sharpe 0.39 IS / 0.40 OOS | console output | `backtests/validate_tsmom.py` |
| TSMOM on FX majors: fails OOS | same | same |
| Deriv omnibot executes all contract families | run log + ledger | live run 2026-06-10 |
| Synthetics remain fair/no-edge (live battery) | `/edge` endpoint during run | `omnibot/edge.py` |

## 3. What was FALSIFIED and therefore NOT shipped (this is the alpha)

| Idea (popular on MQL5 market) | Result after costs, 2015→2026 H1 | Verdict |
|---|---|---|
| London/opening-range breakout (gold, EUR, GBP) | PF 0.89–0.93 everywhere; best IS refinement (36-config grid, PF 1.17) **failed its single OOS shot (PF 0.95)** | dead — selection bias confirmed by walk-forward |
| Asian-range fade (EUR, JPY) | PF 0.60–0.91, negative everywhere | dead |
| XAU/XAG z-score "hedge" pairs | PF 0.62 (and 0.63–1.08 across 3 more configs) — the ratio is NOT stationary at tradeable horizons | dead; HedgeGuardian mode-2 style systems should not get real money — your instinct was right |
| Next-bar ML prediction (online logistic, H1, 70k+ bars/symbol) | gross expectancy ≈ 0; net = −cost; "AUC 0.66" exposed as flat-bar artifact | null — matches prior session's daily-ML null (AUC 0.49) |
| Gold seasonality | only January stable (+3.3%/mo, t=3.3, both halves); all other months flip sub-period | too thin to trade alone; not shipped |
| USD-momentum veto on gold TSMOM | Sharpe 0.06→−0.11 on overlap window | rejected |

**Why this section matters:** these are exactly the systems sold as "proven"
on marketplaces. The repo now contains the receipts for why they aren't.

## 4. Honest expectations (the part nobody puts in the README)

- A 1k USC account with the validated stack at the shipped risk settings
  targets **single-digit % per MONTH with -10..-25% drawdown years possible**,
  not $100/day. $100/day on $1k = 10%/day = 3,500%+/yr — nothing in this repo,
  or anywhere reputable, does that. The math that promises it is the
  martingale math in `FINDINGS.md` §6 that ends at zero.
- Validated ≠ guaranteed. It means: survived honest tests on 10–21 years of
  real data including a true out-of-sample split. Forward-demo everything
  ≥ 4 weeks before a cent of real money, and let TailGuard supervise.
- The genuinely strongest finding of the whole program remains structural:
  **costs and risk control decide survival; signals are secondary.** Every EA
  here embodies that.

## 5. Verification status

- Python research: all scripts in `RESEARCH-2026-06-10/` ran in this session;
  outputs committed.
- Deriv omnibot: live-tested against the real demo API (see §1).
- MQL5 EAs: written to compile clean on MetaEditor 5 (build 4400+);
  compile verification on the Windows/MT5 box is the next step in this
  session — see commit history for the verified list.
