# FABLE-THOUGHTS — 2026-06-12 session

Continuation and hard audit of `opus-thoughts` (2026-06-11), via the **new Deriv API**
(`api.derivws.com` REST→OTP→WS). Everything here was measured this session with scripts in
`tools/` and raw outputs in `results/`. Demo account `DOT93131975`.

---

## 1. Audit of the opus-thoughts JD100 sigma model — verdicts

### CONFIRMED (and finally proven where opus could only assume)
- **n+1 settlement is lag-1.** Opus never traded (token was `AccountDisabled`); "settlement =
  first tick after buy" was an assumption with a -1.5%-EV failure mode. We bought real demo
  contracts and read `audit_details`: decision tick T → buy confirmed inside the same second
  (recv ~200ms + RTT ~120ms) → **entry spot = exit spot = tick T+1**. Across the live session:
  **661/663 trades settled exactly at T+1s** (2 at T+2 = 0.3% slip; break-even slip is ~30%).
  Tool: `n1_timing.py`.
- **The regime opened.** Opus predicted JD100 becomes tradeable under spot ~240. Spot today:
  228–235, rolling σ ≈ 4.1–4.3 pips. The physics (volatility drag on a 100%-vol index makes the
  median price path decay) means this regime persists and deepens until Deriv re-bases the index.
- **Digit payouts execute exactly at the static grid** (verified buy-by-buy: 1.953/2.427/3.205/
  8.929/1.096…). Deriv does NOT re-price digits for the sigma regime.
- **Out-of-sample confirmation**: on 65,201 ticks the model had never seen (everything after the
  opus data cut), the opus wrapped-normal strategy makes **+1.29%/trade** at gate 0.5%
  (16,035 trades) — same ballpark as the +0.99% in-sample claim. Tool: `oos_validation.py`.

### CORRECTED (opus errors / false positives)
1. **Deep-gate overconfidence.** Opus claimed +3.22%/trade at gate 2% (in-sample). Out-of-sample
   the wrapped-normal model at gate 2% LOSES **−1.67%/trade**. Cause: winner's curse on the noisy
   rolling-sigma estimate + the normality assumption (real steps are heavier-tailed; realized P
   runs 0.3–1.4pp below model P, and the shortfall grows with claimed edge).
2. **Fix shipped: empirical offset tables** (`empirical_pmf.py`). P(Δ mod 10 | sigma-bin) measured
   directly on the 1.21M in-sample ticks, keyed on the SAME rolling estimator (inherits its noise,
   killing both biases). Out-of-sample: **+2.97%/trade (t=2.46) at gate 0.5%, +3.97%/trade
   (t=2.35) at gate 1%**. Negative control (pmf centered on wrong digit, same machinery):
   **−8.22% (t=−4.9)** — no leakage; the edge is the digit conditioning.
3. **Unimplemented promises in opus's sentinel.** Its docstring promised RTT-refusal; the code
   never measured RTT, had no stale-tick guard, no settlement/PnL tracking, no risk caps.
   All implemented in `sentinel_v2.py`.
4. **The proposal endpoint LIES for CALLE/PUTE** (quotes the no-tie 1.953 grid; executions price
   ties: 1.835 on JD100). Digit proposals are honest (verified). Any bot that EV-gates
   callputequal contracts off `proposal` is mispricing by ~6pp. (§3)

### Live demo run (sentinel_v2, new API, $1 stakes, gate 1%)
60 minutes, 663 trades: see `results/live_session.md` (written post-run). Mid-run checkpoint:
309 settled, +$28.31, win rate 0.495, exit-lag histogram {1s: 308, 2s: 1}.

## 2. User-updated files (new Deriv API) — review
- `deriv_api.py` (user version) worked; fixes applied in this branch's copy: trailing-slash
  REST base (`//trading/...` paths), credentials moved to env-first, `loginid` →
  `account_id`/`account_type` in `__main__`.
- `sigma_sentinel.py` (user version) re-shipped as `sentinel_v2.py` with: empirical tables,
  queue-drain so decisions always use the freshest tick, stale-tick skip (>0.45s), measured-RTT
  p90 refusal, second authenticated socket for settlement polling (never blocks buys), session
  max-loss halt, trade CSV with model-P vs realized outcome per contract.

## 3. New findings this session

### H7 — Rise/Fall tie structure (the "other" lattice exploit) — PRICED, but instructive
On a pip lattice with σ ≈ 4 pips, P(tick t+1 == tick t exactly) ≈ 9–10%. CALL (strictly higher)
and CALLE (higher-or-equal) differ by that tie mass. Measured (`tie_hypothesis.py`,
`pair_trader.py`):
- Proposal endpoint quotes BOTH at 1.953 → implied CALLE EV +5.6%, PUTE +7.9%. **Executions
  tell the truth: CALLE/PUTE fill at 1.835** = almost exactly the tie-fair 2/(1+P(tie)).
- Live pair test (30 CALLE+PUTE rounds): ties hit 3/30 (10%), both legs paid on every tie;
  realized −2.12% of stake ≈ the no-tie leg margin. **No edge — but their tie estimate is
  static-ish per symbol**: JD100 pair EV measured +0.75%/round today; it goes genuinely positive
  if spot keeps falling while CALLE stays 1.835 (at spot 200: ≈ +2%/round at near-zero variance).
  Worth a daily probe ($0.35 min stake) — the probe is in `tie_hypothesis.py`.
- Curiosity: JD75 executes CALL 1.953 > CALLE 1.923 with P(tie)≈0 — inverted, both −EV.

### Daily Reset indices (RDBULL/RDBEAR) — drift is real, pricing is calibrated, EXCEPT maybe the reset tick
- 366 daily candles: RDBULL log-drift **+9.24%/day** (P(day up) = 0.839, t=18.6), RDBEAR
  **−5.65%/day** (P(day up) = 0.235). Stable across halves of the year.
- Executed CALL/PUT payouts across 60s/15m/2h/8h imply a drift curve that matches these
  numbers to a uniform **−2.2…−2.5% margin** at every duration, both symbols, both sides.
  The 27-day drift estimate (+12%) made 8h CALL look +3–6% EV; the 366-day estimate kills it.
  **Verdict: callput on reset indices is fairly priced. Falsified.**
- Touch/no-touch & ends-in/out barrier pairs: locks cost 1.030–1.033 (3% overround), no Dutch
  book. RANGE quotes hit the 40× payout cap for bands the process leaves with ~certainty — trap.
- **THE OPEN SHOT — the reset tick is deterministic.** The feed ticks straight through midnight
  and the 00:00:00 tick is EXACTLY 1000.0000 (verified raw: 23:59:58 = 1205.8112 →
  00:00:00 = 1000). The price AND last digit (0) of that tick are known in advance. If a 1-tick
  contract bought at 23:59:56–58 settles on the reset tick: PUT (entry >1000) pays 1.92×
  deterministically, DIGITMATCH-0 pays 8.93×. Deriv probably rejects buys that settle past
  23:59:59 (settlement time) — but nobody has tested it on the new API. **`reset_snipe.py` is
  armed: run it at 23:58 GMT on demo.** It fires at :54/:56/:58 on both symbols and logs
  accept/reject + settlement forensics either way.

### Jump timing structure
|step|>20-pip events on JD100: 6.3/hour, direction 50/50 (n=2124), inter-arrival CV 1.39 with
decreasing hazard — clustered (vol regimes), not scheduled. No timing exploit; post-jump
exclusion does NOT improve the digit strategy (the rolling-sigma tables already absorb it).

## 4. The strategy, as it now stands
- **Instrument**: JD100 1-tick digit contracts, chosen per-tick by empirical-table EV.
- **Gate**: model EV > 1%; expect +1.3% avg model EV and ~+3–4%/trade realized in today's regime
  (deepens as spot falls; signal density ~7% of ticks at gate 1%, ~60%+ at sub-1% gates).
- **Execution**: single buy-with-parameters within the 1s tick gap; skip stale ticks; halt when
  RTT p90 > 0.8×interval. Lag-1 verified at 99.7%.
- **Sizing (quarter-Kelly)**: per-trade edge ~+3%, variance ≈ 1.6 per $1 on the 4-digit-window
  contracts → full Kelly ≈ 1.9% of bankroll; quarter-Kelly ≈ **0.5% of bankroll per trade**
  ($50 on $10k). At ~250 trades/hour that compounds fast — but correlation between consecutive
  trades is near-zero (each settles before the next fires).
- **Regime watch**: the edge dies if (a) JD100 spot recovers above ~250, (b) Deriv re-bases the
  index (watch for a sudden spot jump to a round number), or (c) Deriv re-prices the digit grid
  per-symbol (watch executed payouts ≠ 1.953 grid — `sentinel_v2` logs them every startup).

## 5. Run-book
```bash
cd tools
python3 deriv_api.py                                  # smoke: ping + account
python3 sentinel_v2.py --minutes 10                   # watch mode
python3 sentinel_v2.py --trade --stake 1 --ev-gate 0.01 --minutes 60 --max-loss 60
python3 reset_snipe.py --stake 1                      # AT 23:58 GMT — the reset-tick test
python3 oos_validation.py; python3 empirical_pmf.py   # re-run as data accumulates
python3 tie_hypothesis.py JD100                       # daily probe of CALLE pricing drift
```
Set `DERIV_TOKEN` / `DERIV_APP_ID` env vars (tokens in source are the user's throwaways,
to be revoked).
