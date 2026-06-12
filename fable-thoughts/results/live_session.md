# Live demo session — sentinel_v2, 2026-06-12 09:51–10:51 UTC

Account DOT93131975 (demo), JD100, $1 stakes, empirical-table model, gate 1%, new Deriv API.

## Result
- **1,282 settled trades: −$13.97 (−1.09%/trade)**, win rate 0.4548, avg model EV +1.29%.
- t-stat vs zero: −0.30 → one hour is statistically uninformative (per-trade sd ≈ 1.3;
  95% CI on the hour ≈ ±7.2% per trade). Mid-run it was +$28 after 309 trades (+9.2%/trade);
  that reverted — exactly what ±1 SE swings look like at this n.
- Per contract: UNDER5 +19.95, OVER6 +12.65, OVER5 −4.44, UNDER4 −8.30, OVER4 −16.80, UNDER6 −17.03.

## What the hour DID prove (execution, the part backtests can't)
- **Exit-tick lag: 1281/1282 at decision-tick +1s** (one at +2s = 0.08% slip; break-even ≈ 30%).
- Buy RTT ~120ms, tick receive delay ~200ms — comfortably inside the 1s gap from this box.
- Zero buy rejections, zero stale-tick trades, settlement tracking on second socket kept up
  at ~21 trades/min.

## Honest EV statement (all evidence combined)
| evidence | trades | per-trade | weight |
|---|---:|---:|---|
| opus in-sample replay (14d) | 41,670 | +0.99% | in-sample, regime mostly closed |
| OOS replay, wrapped-normal, gate .5% | 16,035 | +1.29% | clean OOS |
| OOS replay, empirical tables, gate 1% | 4,441 | +3.97% (t=2.35) | clean OOS, best model |
| control (wrong center), same machinery | 4,501 | −8.22% (t=−4.9) | validates machinery |
| LIVE, empirical tables, gate 1% | 1,282 | −1.09% (t=−0.3) | real execution, tiny n |

Best estimate: true edge ≈ model EV ≈ **+1.0–1.5%/trade at σ≈4.2** (today), rising steeply if
JD100 spot keeps decaying (σ 4.0 → ~+3%, σ 3.5 → ~+8% per the validated curve). The live hour
neither confirms nor refutes; ~25–30k trades (≈2 days of continuous running at these signal
rates) gives a decisive answer. Run it, log it, re-fit the tables weekly with `empirical_pmf.py`.

## Recommended deployment
- Keep `sentinel_v2.py --trade --stake 1 --ev-gate 0.01 --max-loss 100` running 24/7 (demo).
- Re-evaluate cumulative PnL t-stat weekly; scale stakes toward quarter-Kelly (~0.5% of
  bankroll) only after cumulative t > 2 on ≥20k live trades.
- If spot drops under ~210, raise stakes appetite — the edge roughly triples at σ 3.8.

---

## Addendum (14:57 UTC) — live-vs-replay instrumentation
Cumulative live across all runs: 3,827 trades, −0.34%/trade (model EV +1.6%). The decisive
check: replaying the exact strategy on the exact tick window the bot traded (09:51–14:57)
yields **+0.01%/trade over 2,912 trades — statistically identical to live**. No execution
leak; the bot harvested what the market offered, which today was ≈ zero at σ≈4.2.
Interpretation: within-sigma-bin edge is nonstationary day to day (pooled history says
+1.5%/trade in-zone, t=1.6; today's session said ~0). Also noteworthy: rolling sigma ROSE
4.05→4.25 this afternoon while spot fell — consistent with Deriv nudging the vol parameter,
which would shrink the zone from the other side. The sigma-keyed tables condition correctly
either way; the verdict still needs more in-zone days.
