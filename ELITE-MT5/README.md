# ELITE-MT5 — only what survived validation (compile-verified 0/0)

Two EAs, each with receipts. The folder is small **because the validation was
honest**: London breakout, Asian-range fade, XAU/XAG pairs and next-bar ML all
FAILED out-of-sample tests and were not shipped (see SESSION-REPORT.md §3).

| File | Edge | Evidence |
|---|---|---|
| `EliteGold_TSMOM.mq5` | Time-series momentum on gold, vol-targeted, COT tilt | Sharpe 0.39 IS (2004-17) / **0.40 OOS (2018-25)**; fails on FX → metals only (`validate_tsmom.py`) |
| `EliteCOT_SwingPortfolio.mq5` | Donchian trend + CFTC positioning gate, 5-symbol basket | gate lifts mean PF 0.98→1.22-1.28 across 9 pairs; GBPUSD/NZDUSD excluded with data (`cot_study.py`) |

Honest expectations: thin, lumpy edges (PF 1.2-1.5, win ~30-40%) that pay on a
few big winners a year. Vol targeting + breakers keep the bad years survivable.
Forward-demo ≥4 weeks. Not a money printer; a harvest.
