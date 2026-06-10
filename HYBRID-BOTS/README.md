# HYBRID-BOTS — experimental by design, honest by construction

| File | Status | What it does |
|---|---|---|
| `HybridScalper_CostGate.mq5` | EXPERIMENTAL | scalper that measures live edge (rolling lag-1 autocorr) vs live cost (spread+slippage) and only arms when edge ≥ 1.5× cost. On normal spreads it refuses to trade — that refusal IS the feature. Run on raw-spread accounts only. |
| `HybridML_ShadowGate.mq5` | EXPERIMENTAL (shadow default) | online logistic gate over the validated trend core. Ships in SHADOW mode: every signal trades, the gate's would-be vetoes are tracked, and the HUD shows a live A/B (gated vs ungated expectancy). You enable enforcement only if YOUR chart's data says it helps. Prior ML attempts were nulls (daily AUC 0.49; H1 net=-cost) — hence shadow-first. |

No hedging/martingale/recovery EAs here, deliberately: XAU/XAG z-score pairs
was falsified this session (PF 0.62-1.08 across configs — the ratio is not
stationary at tradeable horizons), and grid/martingale math was settled in
FINDINGS.md long ago.
