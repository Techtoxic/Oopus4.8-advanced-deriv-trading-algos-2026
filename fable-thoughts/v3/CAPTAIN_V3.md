# CAPTAIN v3 — the adaptive layer, redone from physics (2026-07-05)

This is my independent redo of the "missing 25%" — the regime-adaptive layer. I did not touch
any original work; everything new lives in `fable-thoughts/v3/`. I re-derived the problem from
scratch, tested every claim on 1.38M ticks across three separated periods (June-11 wide regime,
June-29, and fresh July-4/5 data harvested this session), and live-verified execution on demo.

## 1. Why the algorithm won for 3 days and then got swept — the exact mechanism

Your diagnosis ("the algorithm followed a digit distribution curve fitted to one regime and
couldn't switch") is correct, but the root cause is simpler and harsher than a fitting problem:

**JD100's sigma is not a hidden regime variable. It is a deterministic function of the spot
price**: the index is a constant-100%-annualized-vol 1-second lattice, so the per-tick step
stdev in pips is

```
sigma_pips = c * spot,   c = 100 / sqrt(365*86400) = 0.017807
```

I verified this to <1% error over spot 218→440 on 1.38M ticks (see `captain_analysis.py`, H1).
Measured c on three independent periods: 0.017765 / 0.017959 / 0.017951 vs theory 0.017807.

The digit edge is wrapped-Gaussian concentration: P(next digit lands within ±2 of current) =
0.5 + excess, where the excess decays like `exp(-2π²σ²/100)`. With the 2.35% house margin on
the even-money ladder, EV crosses zero at **σ ≈ 4.45 pips, i.e. spot ≈ 250**.

- Your 3 winning days: spot 220–235 → σ 3.92–4.18 → EV +1…+3%/trade. Real edge, real wins.
- Day 4: spot drifted above ~250 → σ > 4.45 → the *same rule* has structurally negative EV
  → losing streak → stop swept. Nothing changed at Deriv; nobody "detected" you; the spot
  level IS the regime, and the bot had no gate on it.

## 2. Why Opus's adaptive attempt underperformed

The June-29 layer (trailing self-recalibrating table + rolling-σ gate) had three defects, two
of which it discovered and patched itself (narrow-contract optimizer curse; Kelly-sequencing
artifact). The remaining structural one:

- **The rolling-σ estimator (1800 ticks = 30 min) is pure lag + noise on a quantity that is
  known exactly from the current tick's price.** After any spot level move the gate is wrong
  for up to 30 minutes — it keeps trading into a dead regime after spot rises, and misses the
  first 30 min after a dip into the zone. Estimator noise also leaks trades across the
  boundary in both directions (the walk-forward's "sparse-table noise above σ 4.5").
- The trailing offset table is a second noise source solving a problem that doesn't exist:
  the step SHAPE never changes. Normalized steps Z = step/(c·spot) are Gaussian to 3 decimals
  (kurtosis −0.06 on 1.2M steps) and the shape is invariant across regimes (quantiles at
  σ≈4.5 vs σ≈6.6 match within 0.03). There is nothing to retrain. Ever.

## 3. The v3 rule (zero fitted parameters, adapts instantly to ANY sigma)

```
sigma = 0.017807 * current_spot                      # exact, per tick, zero lag
pmf   = discrete Gaussian (continuity-corrected) folded mod 10
pick  = max-EV of {OVER3, OVER4, UNDER5, UNDER6} conditioned on current digit
trade iff (p − 0.001 haircut) * live_payout − 1 > gate      (+ σ ≤ 4.6 safety ceiling)
```

Calibration (model P vs realized P, per σ-bin, `captain_analysis2.py`): within ±0.5pp on all
three periods, no systematic overconfidence — the wrapped-normal "overconfidence" reported on
June-12 was an artifact of conditioning on the *noisy rolling* σ, not of the Gaussian.

Walk-forward, day by day, `captain_backtest.py` (gate 1% / flat $1):

| period | trades | model p | real p | PnL/trade | t |
|---|---:|---:|---:|---:|---:|
| June-11 wide (incl. the −$19.5k regime) | 12,828 | 0.5205 | 0.5248 | **+2.49%** | 2.9 |
| June-29 | 42,612 | 0.5228 | 0.5205 | **+1.66%** | 3.5 |
| July-4/5 fresh (gate 0.5%) | 16,670 | 0.5180 | 0.5184 | **+1.25%** | 1.7 |

On the wide June-11 data the rule trades only the one in-zone day and idles the entire
high-spot stretch that bled −$19,562 static — same protection as the hard σ-gate, but with
zero lag and a smooth EV-based boundary instead of a cliff.

## 4. What spot range is good (the trigger table)

Per-digit trigger spots (trade fires only when EV clears the gate; d = current last digit):

| digit | best contract | gate 1% | gate 0.5% |
|---|---|---|---|
| 2 / 7 | UNDER5 / OVER4 (centered window) | spot < 238 | spot < 243 |
| 1,3 / 6,8 | UNDER5 / OVER4 (off-center) | spot < 231 | spot < 236 |
| 4,5 | UNDER6 / OVER3 | spot < 211 | spot < 216 |
| 0,9 | (worst case) | spot < 195 | spot < 201 |

Rules of thumb: **spot < 243 = edge begins (best digits only); spot < 235 = healthy
(most digits firing); spot < 225 = deep edge (+2–3%/t). Above 250 = structurally negative,
sit out.** The bot derives all of this live from σ = c·spot; the table is just for humans.

## 5. Can it work when sigma is high? (the honest answer)

No digit strategy can: concentration decays as `exp(-2π²σ²/100)` — at σ=5.0 the maximum
achievable boost is +0.9pp of win-prob vs the 2.35% margin (EV −1.4%), at σ=5.5 it's dead to
two decimals. This is not a modeling failure to fix with ML; it's the same physics that gives
the edge at σ=4. **The adaptive play at high σ is: don't trade JD100, and watch everything
else.** v3 does both automatically:

- Continuous EV gating means it re-enters the market the very tick spot dips into range
  (intraday dips happen — last 24h spot ranged 231–255).
- An hourly **universe scan** measures σ_pips for all 17 digit-capable symbols. Today: JD100
  4.45 (boundary), next best R_100 at 8.8 (edge ~2e-7, dead). If Deriv ever re-bases a
  volatility/jump index into the σ<4.4 zone, the scan flags it — that's a whole new field to
  harvest, same physics, same bot with `--symbol`.
- Step Indices show σ = 1.00–5.00 *exactly* (fixed ±0.1…±0.5 steps — next digit would be a
  50/50 two-point distribution = free money on digits) — **and Deriv knows: no digit
  contracts offered on step indices**, only rise/fall which is exactly 50/50 there. Checked.
- σ-independent side quest (untested, from Fable's FINDINGS): the RDBULL/RDBEAR midnight
  reset tick is deterministic (exactly 1000.0000, last digit 0). `reset_snipe.py` at
  23:58 GMT remains the one lottery ticket nobody has fired on the new API.

## 6. Live verification (this session, demo DOT93131975)

- Warmup asserts: c within 5% of theory (measured +0.1%), 1s cadence, payout grid
  1.953/1.634 intact (aborts if Deriv ever reprices — that's the kill-switch for the edge).
- Smoke trades: 3 forced min-stake buys → RTT ~100ms, all settled at decision-tick+1 (lag-1
  confirmed on the new API), CSV forensics logged.
- 8h unattended session started at 10:29 GMT with `--stake 0.35 --ev-gate 0.005
  --max-loss 40`. At launch spot ≈ 250 (σ 4.45) → correctly idle, trigger armed at 243.2.

## 7. Operating notes

- `python3 captain_sentinel.py --trade --stake 0.35 --ev-gate 0.005 --minutes 480 --max-loss 40`
- Flat stake only (Kelly-on-small-balance sequencing trap is real — kept from Fable's finding).
- Judge sessions by win-rate vs breakeven (0.512 on the 1.953 ladder) and %/trade at flat
  stake, never by short-window $ path; ±1,400-trade drawdowns are in-distribution.
- Kill conditions (bot self-checks the first two): c drifts >5% from 0.017807 (re-spec),
  payouts leave the grid (repricing), σ persistently > 4.6 (wait — spot must come to you).
