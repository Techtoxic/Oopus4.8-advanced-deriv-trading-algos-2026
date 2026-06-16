# Sentinel V2 — standing evaluation (metrics engine)

*Read-only analysis. The original `fable-thoughts` work is untouched; this lives on a separate
branch. Numbers below are reproducible with `edge_tests.py` and `analyze.py`.*

## TL;DR
- **The execution engine is genuinely good.** Lag-1 settlement holds at ~100% live, the dual-socket
  design / stale-tick guard / RTT guard / risk caps all work. Nothing here leaks edge through bad
  execution. Worth preserving as-is.
- **The *edge* itself is not demonstrated.** Run the bot's own decision rule on the repo's own
  historical ticks and the realised result is statistically indistinguishable from zero / the house
  margin. The strongest single number (OOS replay t=1.99) is undercut by the fact that realised EV
  *exceeds* the model's claim there — the signature of a lucky sample, not a calibrated edge — and by
  the authors' own stricter day-by-day walk-forward (t=1.60, not significant).
- **Right now the bot is trading entirely outside its validated zone.** JD100 spot has fallen to
  ~210, rolling sigma ~3.7, which is **below the 4.0 floor of the empirical tables**. Every live trade
  is using the **wrapped-normal fallback** — the exact model `FINDINGS.md` flagged as OOS-overconfident
  / losing. It is claiming ~+5%/trade EV in a regime with no measured table and no historical data.

## 1. Does the digit edge exist? (`edge_tests.py` on historical ticks)

**T1 — last digit is uniform.** Over the in-zone ticks the unconditional digit distribution is flat
(live_window: chi²=8.3, p=0.51; oos: chi²=6.6, p=0.68; max deviation from 0.10 ≈ 0.003). No surprise.

**T2 — the offset distribution P((next−current) mod 10) is *almost* flat.** A few sigma bins show a
statistically detectable departure from uniform (e.g. oos sigma 4.2: chi²=32.8, p<0.001), but the
**largest deviation of any single offset is ~0.7–1.6 percentage points** (10.1–11.6% vs the uniform
10%). At N=10k+ even microscopic structure becomes "significant"; the question is whether ~1pp of
structure can beat a house margin of **1.4%–10.7%** per contract. It cannot, except by chance.

**T3 — best achievable EV, measured in-sample (maximally optimistic).** Even letting the strategy pick
the single best contract per bin using the realised conditional distribution *of the same data*:

| sigma | live_window best | oos best |
|---|---|---|
| 4.1 | DIGITEVEN **+1.60%** | DIGITUNDER9 **−0.99%** |
| 4.2 | DIGITUNDER5 **−0.49%** | DIGITODD **+0.94%** |
| 4.3 | DIGITUNDER8 **−0.67%** | DIGITODD **+1.66%** |
| 4.4 | DIGITOVER7 **+4.08%** | DIGITOVER7 **+1.69%** |

The "best" contract and even its **sign flips between the two datasets** in the same sigma bin. A real
structural edge would pick the same contract and stay positive out of sample. This is the fingerprint
of fitting noise.

**T4 — exact replay of the bot's decision rule** (empirical tables where available, argmax-EV over 40
contracts, gate 1%, sigma-max 4.35):

| dataset | trades | realised %/trade | t | win rate | model *claimed* %/trade |
|---|---:|---:|---:|---:|---:|
| live_window | 8,297 | **+0.07%** | +0.06 | 0.462 | +1.52% |
| oos | 9,270 | **+2.20%** | +1.99 | 0.499 | +1.40% |

live_window is flat. oos is borderline — but realised (+2.20%) is **57% above** the model's own claim
(+1.40%); a calibrated edge realises *at* its model, not far above it. The gap is variance. This is
the same conclusion `results/walkforward.md` reached honestly (gated t=1.60).

## 2. What the live run shows (`analyze.py`, updates each run)

See `out/dashboard.html` for charts. Current snapshot (small N, grows as the run continues):

- **100% of live trades are on the wrapped-normal fallback** (`by_source` = `wn` only) because sigma is
  ~3.7. Zero trades have used the empirical tables this session. **This is the single most important
  finding for "sharpening":** the validated edge (such as it is) was measured at sigma 4.0–4.3. The bot
  is now operating below that, extrapolating with a model its own audit discredited.
- Realised ≈ model claim *so far* only because both are large positive numbers and N is tiny; the
  t-stat is ~0.5 (pure noise). Win rate ~47%.
- Execution: exit-lag histogram is all `1` (lag-1 confirmed), RTT well within the tick interval.

## 3. Honest verdict for the "go-live" decision
The math is **not** "proven game-changing positive EV." It is, on the best available evidence, a
near-zero-EV process against a house margin, with a possible sub-1pp structural wobble in the digit
offsets that is far too small and too unstable to bank. The positive live stretches you've seen are
fully consistent with variance on a fair-ish game with ~250 fast trades/hour (a random walk wanders up
as often as down over a few hours). Note also that deleting losing sessions from the repo removes part
of the true distribution — the engine should ingest **all** runs, winners and losers, or the average is
biased upward by construction.

## 4. Concrete sharpening recommendations (non-destructive)
1. **Don't trade sub-4.0 sigma on wrapped-normal.** Either build an empirical table for the 3.x bins
   from freshly collected in-regime ticks, or hard-gate `--sigma-min 4.0` until measured. This is the
   biggest live risk right now.
2. **Decision rule for belief:** only size up when, over ≥25–30k in-zone trades, realised ≈ model AND
   cumulative t > ~3 AND the sign is stable across two distinct sigma regimes. The engine tracks all
   three.
3. **Calibration auto-halt:** stop the session if realised diverges below model by a set margin over a
   rolling N (the live EV-calibration chart is the input).
4. **Log the digit** in a fork of the bot (its CSV writes it blank) so win/loss-by-digit needs no
   side-channel.
5. **Keep every run.** Feed winners and losers alike into `analyze.py`; selection is the enemy here.
