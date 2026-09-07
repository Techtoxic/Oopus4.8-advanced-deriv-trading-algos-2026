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
| sigma law | `0.32240 + 1.611350e-04 * spot` | `sigma_from_spot`, 800k ticks, spot 168–245 |
| sigma gate | 3.48 | `redo_all`, EV crosses zero here |
| gate opens at spot | 195.96 | inverting the sigma law |

**Refit the sigma law if spot leaves 168–245.** Outside that range it extrapolates, which
caused a false mismatch halt at spot 172 under the old constants.

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

**Sigma mismatch halt.** Now warns on an excursion and halts only after 20 consecutive.
Mild volatility clustering (autocorr 0.0082 at lag 3) means rolling and affine can
legitimately diverge for a stretch.

**Proposal grid check.** Was comparing against 1.886; proposals on JD100 serve the PRE-CUT
grid and read 1.953. That check fired constantly and halted working sessions.

## Live results

| run | n | win rate | EV | note |
|---|---:|---:|---:|---|
| pre-drain | 851 | 54.52% | −2.18% | stale-tick entries |
| post-drain | 582 | 56.87% | +2.03% | sigma 3.05 |
| post-drain | 873 | 59.34% | +6.45% | sigma 2.89 |

Combined post-fix: **1,455 trades at 58.35%, +2.0 SE above breakeven.**

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

Breakeven is **37% late**. Currently ~30%, so there is 7pp of headroom. Compute costs 0.2ms;
the entire controllable path is the 120–190ms buy round trip against a 1000ms tick. A VPS
nearer the gateway is the only way below 30% from here, and each 10pp of slippage is worth
about 1.5pp of EV.
