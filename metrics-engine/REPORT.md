# Sentinel V2 — standing evaluation (metrics engine)

*Read-only analysis. The original `fable-thoughts` work is untouched; this lives on a separate
branch. Numbers below are reproducible with `edge_tests.py` and `analyze.py`.*

> **UPDATE — corrected verdict (`edge_confirm.py`).** My first pass (sections 1–6) concluded "no
> bankable edge." That was **wrong for the current regime**, and I'm flagging it loudly rather than
> burying it. The mistake: I measured *fixed-barrier* contracts, which average over the current digit
> and wash the signal out. The bot's *actual* rule conditions the barrier on the current digit. Replay
> that real rule on **26,700 independent monitor ticks** → **+6.24%/trade, t=+7.0**; every placebo that
> breaks the lag-1 link collapses to the **−3% house margin**; both data halves are independently
> significant (t=4.4 / 5.5); and the edge **grows monotonically as sigma falls** (3.8–4.0 +4.5%, 3.6–3.8
> +6.2%, 3.4–3.6 +8.4%). There **is** a real, currently-exploitable lag-1 digit-clustering edge below
> sigma 4.0 — see **section 7**. It is genuine but **regime-bound**. Sections 1–6 are the superseded
> skeptical pass, kept for the audit trail.

## TL;DR
- **The execution engine is genuinely good.** Lag-1 settlement holds at ~100% live, the dual-socket
  design / stale-tick guard / RTT guard / risk caps all work. Nothing here leaks edge through bad
  execution. Worth preserving as-is.
- **The edge IS real in the current sub-4.0 regime (corrected — see section 7).** Exact-rule replay on
  26.7k independent ticks: **+6.24%/trade, t=7.0**; placebos collapse to the house margin; stable
  across halves; monotone in sigma. The historical ticks (sigma 4.0–4.3, sections 1–6) looked flat
  because clustering is weak there — it is strong below 4.0 where the bot is actually trading. Live
  realised so far +3.8% (an execution haircut vs the +6.2% replay). The real caveat is **regime
  dependence, not significance.**
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

## 6. The decisive sub-4.0 measurement (`sub4_edge.py`, added live)

The bot has spent this entire run at sigma ~3.5–3.9 — *below* the 4.0 table floor, on the
wrapped-normal fallback. So I measured the digit structure of that regime directly from **22,700
independent monitor ticks** (no bot selection). This refines section 1:

- **The clustering is REAL here.** Offset PMF at sigma 3.5–4.0:
  `[0.114, 0.111, 0.099, 0.097, 0.087, 0.088, 0.089, 0.097, 0.106, 0.110]` — peaked at offset 0/±1,
  depressed at the far offsets. P(next digit repeats) = **0.114 vs 0.10 chance, +4.9σ**. Fable's
  physical intuition (low sigma ⇒ the digit stays near itself) is *correct in this regime*. The
  original tables (floor 4.0) never saw it because the regime hadn't opened during training.
- **But Deriv's grid is calibrated almost exactly to that clustering.** Realised EV of every standard
  contract over those 22.7k ticks, against the real payout grid, is **negative**:
  DIGITDIFF −1.16%, DIGITUNDER −1.31%, DIGITOVER −1.45%, DIGITEVEN −1.83%, DIGITODD −2.87%
  (CIs exclude zero on the tight ones). The mild clustering is not enough to beat the house margin.
- **The single exception is a coin-flip.** "DIGITMATCH the current digit every tick" hits 0.114 vs the
  **0.112 breakeven** baked into the 8.929× payout → EV **+2.0%/trade point estimate, but 95% CI
  [−1.7%, +5.7%] straddles zero** on 22.7k ticks. It is statistically indistinguishable from
  breakeven. The grid's DIGITMATCH breakeven sits *just above* the actual repeat rate — as if it
  already prices in mild clustering.

**Conclusion of section 6 was WRONG — superseded by section 7.** This section measured *fixed-barrier*
contracts (one barrier applied to every tick), which average over the current digit and therefore
cannot see the conditional edge. DIGITMATCH-on-current *is* near breakeven as a fixed strategy, true —
but the bot does not run a fixed strategy. It picks the contract+barrier per the current digit, which
harvests the clustering (bet "stays high" when the digit is high, "stays low" when low). That
conditional rule is strongly +EV; see section 7.

## 7. The corrected, decisive test (`edge_confirm.py`) — the edge is real but regime-bound

Replaying the bot's **exact** decision rule (argmax-EV over 40 contracts, conditioned on the current
digit and rolling sigma, gate 1%) on the **independent monitor ticks** — every tick, no bot selection,
deterministic outcome from the real next digit:

```
CONTROLS (same rule; only the outcome link changes):
  REAL next-tick (lag-1)        n=26,726  EV=+6.24%/tr  t=+7.04  win=0.4354
  placebo decoupled (lag+137)   n=26,726  EV=-3.15%/tr  t=-3.67  win=0.4033
  placebo shuffled next-digit   n=26,726  EV=-3.90%/tr  t=-4.59  win=0.4014
STABILITY (real lag-1):
  first half   n=13,346  EV=+5.00%/tr  t=+4.38
  second half  n=13,380  EV=+7.48%/tr  t=+5.51
DOSE-RESPONSE (real lag-1, by sigma):
  3.4-3.6  EV=+8.38%  t=+3.06     3.6-3.8  EV=+6.21%  t=+5.89     3.8-4.0  EV=+4.47%  t=+2.53
```

Why this is a real effect and not a leak/variance:
1. **Placebos kill it.** Break the current→next link any way (decouple by 137 ticks, shuffle the next
   digit, or randomise the decision digit) and EV reverts to the **−3% house margin**. A look-ahead
   bug would survive shuffling; this doesn't. The signal lives specifically in the lag-1 relationship.
2. **It's stable.** Both halves of the data are independently +EV and significant.
3. **It's mechanistic.** EV rises monotonically as sigma falls — exactly what digit clustering predicts
   (smaller per-tick steps ⇒ the next digit lands nearer the current one ⇒ a uniform-priced grid is
   beatable by conditioning the bet on the current digit).
4. **Why every *fixed* contract still loses (section 6):** a fixed barrier is right half the time and
   wrong half the time as the current digit varies; only the *conditional* rule always bets *with* the
   clustering. The edge is in the conditioning, which is why my fixed-barrier pass missed it.

### The caveats that actually matter (these, not "is it significant")
- **Regime dependence is the whole story.** The edge exists only because JD100 spot has decayed to
  ~200, making steps (~3.7 pips) small vs the 10-unit digit cycle. If Deriv **re-bases** the index
  (spot jumps back toward 1000 — they can do this anytime; it's the same mechanism behind the "reset
  tick"), sigma jumps to tens of pips, mod-10 washes the clustering out, and the strategy reverts to
  the −3% placebo line **instantly**. Harvest-while-it-lasts, not a permanent money printer.
- **Execution haircut is real.** Replay says +6.2%; the live bot realised +3.8%. The gap is stale-tick
  skips and the occasional lag-2 settlement. Size on the conservative live number, and keep the
  stale-tick / RTT guards — they are load-bearing, not optional.
- **Counterparty risk.** If Deriv switches digit pricing from the static uniform grid to a
  volatility-aware grid, the edge dies. They use the static grid today (verified buy-by-buy); whether
  they keep doing so is outside our control. `sentinel_v2` already watches for grid changes on startup.
- **Demo ≠ guaranteed real.** Everything here is the demo account. Real-account fills/latency/limits
  are untested and could differ.
- **Fat-tailed variance.** True edge ~+4%/trade but per-trade variance is high (8.9× MATCH payouts).
  Drawdowns like the −8% we logged are normal; quarter-Kelly (~0.5% of bankroll/trade) is the sizing
  that survives them.

**Net:** Fable's central claim holds up under hostile testing — there is a genuine, statistically
overwhelming lag-1 digit-clustering inefficiency in JD100's current low-sigma regime, and the bot
exploits it correctly. I was wrong to call it noise; the controls are unambiguous. The honest framing
for funding is not "is the edge real" (it is) but "how long does this regime last, and can execution
hold the +4% net after the demo→real and variance haircuts."
