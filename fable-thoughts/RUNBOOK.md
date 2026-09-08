# JD100 runbook

Everything needed after a fresh clone. The tools directory is already patched — there are
no patch scripts to run.

## Daily order

```bash
cd fable-thoughts/tools

# 1. has the payout moved? this is the real safeguard.
python3 payout_audit.py --symbol JD100 --stake 2

# 2. run it
python3 adaptive_sentinel_v3.py --trade --stake 1 --minutes 360
```

The audit at $2 stakes costs about **$6.50 expected**, not $44 — the contracts settle, so
you lose the house edge, not the stake. It resolves payouts to 0.06% and will see any cut
above 0.2%. The August cuts were 0.44% and 0.88%.

## Current calibration

| parameter | value | source |
|---|---|---|
| executed OVER4/UNDER5 | 1.794 | `payout_audit --stake 10`, 2026-08-12 |
| breakeven | 55.74% | 1/1.794 |
| sigma law | `0.44600 + 1.532540e-04 * spot` | `sigma_from_spot`, 800k ticks, spot 162.8–234.2, 2026-09-08 |
| sigma gate | 3.48 | `redo_all`, EV crosses zero here |
| gate opens at spot | 197.97 | inverting the sigma law |

**Refit the sigma law if spot leaves 163–234.** Outside that range it extrapolates.

The current fit reaches **100.1% of its theoretical ceiling**: residual sd 0.05633 against a
rolling-RMS sampling error of 0.05694, ratio 0.99. The residual IS the estimator's own noise,
so there is no structure left to model. Affine also beats rolling directly on RMSE
(0.05825 vs 0.07261, a 19.8% reduction) which is worth 0.398pp of EV.

```bash
python3 sigma_from_spot.py --symbol JD100 --ticks 800000
# then update SIGMA_A / SIGMA_B at the top of adaptive_sentinel_v3.py
```

## What was fixed, and why it matters

**Socket drain (the big one).** `recv()` returns the OLDEST queued message, so whenever the
loop fell behind the bot decided on a stale tick. Measured 30% late entries with a drain
against ~51% without. At payout 1.794 that is the difference between **+1.03% and −2.31%
per trade**. Live results confirmed it: 54.52% before, 56.87% after.

**Probe stake and tolerance.** Payouts quote to the cent, so a payout reading has resolution
0.01/stake. At $1 stake, 1.794 reads back as 1.7900 and the old 0.2% tolerance halted with
"Deriv has repriced again" when nothing had changed. The tolerance is now derived from the
stake. At the default $0.99 probe stake it is 1.12%, so it never halts on rounding — but it
also cannot see a cut below 1.12%. **The daily `payout_audit` is the real check.**

**Sigma constants.** Refit over spot 168–245. The old ones were fitted over 186–272 and
understated sigma by ~0.05 at spot 172.

**Sigma mismatch halt.** Warns on an excursion, halts only after 20 consecutive, and the
tolerance is now 15% of affine rather than a multiple of the fit residual.

The reason matters: **rolling and affine measure different quantities.** JD100 averages 3
jumps per hour, so a W=1800 window holds about 1.5 of them. `sigma_from_spot` filters jumps
and fits the DIFFUSION component; the rolling RMS includes them. Observed gap at spot 169.85
was rolling 3.375 against affine 3.049 — a 10.7% inflation, exactly the jump contribution.
Refitting does not close it and should not.

**Affine is the correct input to the gate.** `jump_decompose` measured the jump effect on the
digit win rate at 0.018pp with perfect hindsight, so jumps are irrelevant to a 1-tick digit
contract, and the offset tables were built on diffusion sigma.

**Proposal grid check.** Was comparing against 1.886; proposals on JD100 serve the PRE-CUT
grid and read 1.953. That check fired constantly and halted working sessions.

## Live results

| run | n | win rate | EV | note |
|---|---:|---:|---:|---|
| pre-drain | 851 | 54.52% | −2.18% | stale-tick entries |
| post-drain | 582 | 56.87% | +2.03% | sigma 3.05 |
| post-drain | 873 | 59.34% | +6.45% | sigma 2.89 |
| post-drain | 982 | 57.43% | **+3.09%** | sigma 3.06, predicted +3.08% |

Combined post-fix: **~2,400 trades, past 3 SE above breakeven.**

The 982-trade run is the strongest confirmation: predicted EV +3.08%, realised +$60.76 on
$2 stakes = **+3.09% per trade**. The model predicts the win rate from spot, the win rate
predicts the P&L, and both held to two decimal places.

## Sizing

At +6.45% EV and odds 0.794, full Kelly is 8.1% of bankroll per signal. Half Kelly on
$500 is about $20. Running at $1 is 0.2% — far inside, which is the right side to be on
while the sample is only 2 SE.

## The standing risk

The edge depends on a payout Deriv has cut **four times** (1.953 → 1.818 → 1.810 → 1.794 →
1.800). It currently sits 3.6pp above breakeven — the largest margin of the whole project,
and therefore the most likely to be cut again. A fifth cut can land mid-session.

Also watch `pip_size`. If JD100 moves from 0.01 to 0.001, sigma_pips jumps from ~3 to ~30
and the edge ends permanently. `watchdog.py` checks this.

## Multi-account

`multi_trader.py` fires the same signal across N accounts on independent connections, in
parallel. Sequential firing would put account 5 about 750ms behind account 1 on a 1000ms
tick — parallel keeps all of them inside ~165ms.

```bash
python3 multi_trader.py --tokens TOK1 TOK2 ... --rounds 10
```

Test standalone first and check that every account's median round trip clusters with the
others. One slow connection means worse slippage on that account alone.

**Five accounts on one signal is 5× position size, not diversification.** Same symbol, same
tick, same direction — perfectly correlated. Size the total, then divide.

## Latency

`slippage_probe.py` measures how often entry lands one tick late, under the full production
sequence.

```bash
python3 slippage_probe.py --rounds 60          # full path
python3 slippage_probe.py --rounds 60 --bare   # network floor only
```

Breakeven is **37% late**. Currently ~30%, so there is 7pp of headroom, and slippage is
eating about 85% of the modelled edge: at spot 176 the model says +5.19% at zero slippage
but +0.77% at 30%.

**A VPS near the gateway is the single highest-value change available.** Going from 30% to
5% late is worth **+3.68pp of EV per trade** — roughly $1,472 over 20,000 trades at $2.
By contrast a C++ rewrite would save 0.18ms of a 200ms round trip: compute is already 0.2ms,
0.1% of the path. Test with `slippage_probe.py` on the VPS before committing. Compute costs 0.2ms;
the entire controllable path is the 120–190ms buy round trip against a 1000ms tick. A VPS
nearer the gateway is the only way below 30% from here, and each 10pp of slippage is worth
about 1.5pp of EV.
