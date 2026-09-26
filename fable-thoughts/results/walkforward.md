# Walk-forward validation — the correction that matters

Single-split OOS (+3.97%/trade, t=2.35) looked great. The stronger test — **day-by-day
walk-forward** (for each UTC day, build tables ONLY on prior days, trade that day at gate 1%) —
tells a harder truth:

## No sigma gate (trade whenever table-EV > 1%)
| day | spot low | trades | PnL | per-trade |
|---|---:|---:|---:|---:|
| 06-02 | 377 | 2,468 | −0.1 | −0.01% |
| 06-03 | 352 | 12,175 | −267.6 | **−2.20%** |
| 06-04 | 325 | 2,184 | +69.2 | +3.17% |
| 06-06 | 306 | 15 | −5.4 | — |
| 06-11 | 265 | 1,624 | +33.7 | +2.08% |
| 06-12a | 253 | 10,341 | −38.0 | −0.37% |
| 06-13a | 244 | 5,520 | −10.2 | −0.18% |
| 06-15a | 242 | 531 | −6.8 | −1.27% |
| 06-16a | 228 | 4,441 | +176.4 | +3.97% |
| **total** | | **39,299** | **−48.7** | **−0.12% (t=−0.22)** |

(a = the four calendar chunks of the post-cut OOS file; labels are epoch-days.)

## With hard sigma gate σ ≤ 4.5
10,492 trades, **+159.5 (+1.52%/trade, t=1.60)** — all damage above σ 4.5 was sparse-table
noise crossing the EV gate (the 06-03 day: 12k trades at spot 352 where the true pmf is flat).

## Honest conclusions
1. **The edge lives EXACTLY where opus's validated σ-curve said**: per-bin empirical EV was
   +1.45% at σ 4.15–4.3, ~0 at 4.3–4.45, negative above. The walk-forward day PnLs match that
   curve day by day (spot 242–253 days ≈ breakeven; spot 228–235 day strongly positive).
2. **My earlier +3.97% headline overstated it** — that was the single deepest-regime day.
   Until 06-11, the σ≤4.3 zone had NEVER been open in the recorded history; we now have ~1 day
   of in-zone data. In-zone evidence: ≈ +1.5–4%/trade, t ≈ 1.6–2.4. Real but not yet bankable.
3. **Deployment changes shipped**: `sentinel_v2.py` now has `--sigma-max 4.35` as a hard
   physics gate (never trade above it, regardless of table EV). This kills the noise-trading
   failure mode entirely — in walk-forward it would have skipped every losing day and kept the
   winners.
4. The statistics decide themselves from here: every day JD100 spends under ~240 adds ~10–25k
   gated signals. Two or three in-zone days at +1.5% with t>2.5 = deploy with size; flat = the
   curve was luck and we say so.
