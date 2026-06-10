# MQL5-ENHANCED — your existing EAs, upgraded (compile-verified 0/0)

| File | What it is | Key v-upgrades |
|---|---|---|
| `FableCOT.mqh` | **Embedded weekly CFTC COT history 2005→2026** (9 assets, publish-lagged +3d). Makes every COT filter below BACKTESTABLE in the Strategy Tester. Keep it in the same folder as the EAs when compiling. | regenerate anytime: `RESEARCH-2026-06-10/cot_study/gen_fablecot_mqh.py` |
| `AdaptiveSwingTrader_v2.mq5` | Your top performer, v2 | tri-source COT (live API ↔ embedded ↔ off; 4 modes), vol-targeted sizing, ER regime router, partial/BE/chandelier/giveback, daily+weekly breakers, expectancy governor |
| `FFZ_v3_PurpleBills.mq5` | Purple Bills follower v3 | embedded-COT modes (tester-capable), vol-regime gate, breakers, governor — v2 exits kept |
| `XU_SEMA_NoRepaint_v2.mq5` | Causal SEMA lock EA v2 | optional embedded-COT gate, equity breakers |

Recommended first runs (Strategy Tester, real ticks):
- AdaptiveSwingTrader_v2 on XAUUSD H4/D1 — A/B `InpCOTMode` OFF vs INDEX (this was impossible before FableCOT.mqh).
- FFZ_v3 on your usual Purple Bills symbol/TF — A/B `COT_EMBED_SIGN` vs `COT_OFF`.
