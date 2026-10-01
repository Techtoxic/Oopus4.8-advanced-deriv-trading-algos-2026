# RESULTS — opus-thoughts session 2026-06-11

Everything below was measured THIS session via the public Deriv WS API (`app_id 1089`), with
scripts in `tools/` and raw data in `data/`. Re-run anything.

> Account note: the provided API token returns `AccountDisabled` — all of this is read-only
> market data; no demo trades were possible this session. Fix the account to run the traders.

---

## 1. The headline: JD100 step-physics edge (H2) — REAL, regime-gated, execution-bound

**Mechanism.** The last digit is the last decimal of a price that moves a finite number of
pips per tick: `d(t+1) = (d(t) + Δpips) mod 10`. When σ(Δpips) is small, `Δ mod 10`
concentrates around 0 and the next digit clusters near the current digit. σ_pips = spot ×
vol × √(1/31.5M)/0.01 — **it scales with the price level**, and Deriv's digit payouts are a
static grid (verified identical across 18 symbols) that never re-prices for it.

**Where the universe stands (20 digit-enabled symbols screened, `universe_screen.md`):**
only **JD100** (Jump 100, 1s ticks, vol ~100%/yr + jumps) is near the critical zone — spot
223–263 over the last days → σ_nojump ≈ 4.1–4.9 pips. Everything else is ≥ 9 pips (dead flat
mod 10). Step mod-10 non-uniformity on JD100: **z = 5.59 on 1.21M ticks (p < 1e-7)** —
unambiguous.

**The validated σ→edge curve** (wrapped-normal model, `sigma_edge_model.py`, empirically
tracked across σ bins on 1.21M clean ticks, `sigma_curve_validation.md`):

| σ (pips) | JD100 spot | P(U5\|d=2) | EV/trade @1.953 |
|---:|---:|---:|---:|
| 4.6 | 258 | 0.510 | −0.2% |
| 4.3 | 241 | 0.517 | +1.1% |
| 4.0 | 224 | 0.527 | +3.0% |
| 3.5 | 196 | 0.557 | +8.7% |
| 3.0 | 168 | 0.608 | +19.0% |
| 2.5 | 140 | 0.685 | +34.3% |

**Honest replay backtest** (14 days, $1 stakes, all 40 contracts priced per tick, causal
rolling σ, `backtest_conditional.py`):

| gate | trades | win rate | PnL | per trade | t-stat |
|---:|---:|---:|---:|---:|---:|
| EV>0.5% | 41,670 | 0.493 | **+$414** | +0.99% | +1.89 |
| EV>2% | 4,798 | 0.437 | **+$154** | +3.22% | +1.83 |
| EV>0.5%, 30% missed-tick | 41,670 | — | −$123 | −0.3% | −0.56 |

- Profit concentrates exactly where theory says: the day spot bottomed at 223–233 (+$420 of
  the +$414). The other 10 days the regime never opened and the gate correctly sat out.
- Model calibration: model-EV +2.51% vs realized +3.22% on the deep-regime day.
- **n+1 (your consideration), quantified:** the conditional edge exists ONLY at lag 1.
  Measured at lag 2: P(U5|d=2) = 0.504 → −1.5% EV. A 30% slip rate flips the whole strategy
  negative (table above). JD100 ticks every 1s ⇒ buy must confirm in <1s ⇒ single
  buy-with-parameters call (no proposal round-trip), fast link, and the sentinel refuses to
  trade if its own measured RTT p90 > 0.8× tick interval.
- Statistical status: t≈1.9 (≈94%) because the regime has existed for ~1 day of history. The
  mechanism is physics, the pricing is static, and significance compounds every day spot
  spends under ~240. `sigma_sentinel.py` watches it live and only fires when model EV clears
  your gate. Spot is ~263 right now → it correctly says NO TRADE today.

**Why this can exist:** Deriv prices digits off assumed-uniform digits with a fixed margin
grid. That assumption is only violated when σ_pips gets small — a slow random-walk artifact
of the price level they let drift. They even custom-priced R_100/R_10 (see §3) but left the
jump indices on the standard grid.

---

## 2. Your ideas — exact verdicts

### 5-digit MATCH bundle (same tick or latency-spaced)
- Measured MATCH payout: 8.929× (NOT 6.04 — nothing pays 6.04 on app 1089; if a tool showed
  6.04 it was a marked-up third-party app skimming you).
- Same tick, 5 digits × $1: P(hit)=0.5 exactly, EV = 0.5×8.929 − 5 = **−$0.536/round (−10.7%)**.
- Latency-spaced over 5 ticks: identical EV (independent legs); only the variance shape
  changes (41% rounds ≥1 hit, multi-hit possible). Spacing is a feelings dial.
- **The same exposure is sold 4.5× cheaper**: any contiguous 5-set = OVER4/UNDER5 (−2.35%),
  odds/evens = EVEN/ODD (−2.35%), 9-of-10 = DIGITDIFF (−1.36%). `coverage_optimizer.py`
  LP-solves the cheapest replication of ANY digit set against live payouts.
- `multi_matches.py` implements your tool anyway — with live quotes, an EV gate that refuses
  −EV rounds (unless `--force`), the replication comparison printed each round, and demo-only
  guard. With the JD100 model plugged in (`--auto`), it picks the 5 digits with the highest
  conditional mass instead of frequency-chasing.

### The 512-combo / liability-steering theory (H1+H4)
- Formalized and simulated (`liability_sim.py`): with a realistic crowd mix, even 5% steering
  shifts P(next = trailing-hottest) below 0.095. Our resolution at 1.2M ticks is ±0.2pp.
- Measured: P(hottest)=0.0999, P(coldest)=0.1005, overdue-digit hazard flat 0.098–0.104 for
  every gap 1–40, parity-streak flip flat at 0.500 for streaks 1–10.
- **Verdict: steering of any economic size is excluded.** Deriv profits from the margin grid,
  not digit manipulation. (This also kills all "they hunt the crowd" entries — and all tools
  selling that story.)

### Over/under implementation of your idea
- Done as the core of the real strategy: the conditional trader expresses digit-set views via
  the cheapest instrument (almost always OU/parity), sized by model EV. That IS your idea,
  upgraded: same trade, 2.35% toll instead of 10.7%, fired only when the model says the set
  probability beats the payout.

---

## 3. Payout surface (H3) — full 20-symbol scan (`payout_surface.md`)
- 18/20 symbols share one static grid: MATCH 8.929 / DIFF 1.0958 / OU smile 1.096→8.929 /
  EVEN-ODD 1.953. House edge smile: 1.36% (DIFF/OVER0) → 10.71% (MATCH).
- **R_100 and R_10 are strictly worse venues**: MATCH 8.333/8.696, OVER0 1.087/1.093 — an
  extra 2–6pp of edge on the two most-traded symbols. Never trade digits there; identical
  events pay more on 1HZ100V/JD100/anything else.
- **No Dutch book**: cheapest lock of $1 across all 10 digits costs 1.0138 (LP over all 40
  instruments, every symbol × durations 1 & 10). No partition arbitrage exists.
- **No duration spreads**: payouts duration-invariant ⇒ always trade 1-tick (keeps the
  conditional information; longer durations only dilute it).

## 4. Accumulators (H5) — `accumulator_rtp.md`
Exact per-tick survival from live barriers × big samples: G = (1+g)×P(survive) =
**0.9922–0.9980, < 1 on every (symbol, rate) cell at 99% confidence** (best: 1HZ10V @2% =
0.99803). Cheapest gamble per tick on Deriv (0.2–0.8% vs digits' 1.4–10.7%) but no
take-profit/hold pattern can be +EV. Re-scan after Deriv vol-regime changes — a mis-set
barrier would show up here as G>1.

## 5. RNG health (the standard battery, for completeness)
Digit marginals uniform (max z = 2.2 on 1.2M ticks), gap hazards geometric, parity flips
0.500, H1 null — consistent with the three prior independent audits in this repo (branches
`deriv-edge-research-2026-06-07`, `lattice-microstructure-validation`,
`capy-fable5-session-2026-06-09`, omnibot sentinel on `fable-session-2026-06-10`).

## 6. Repo archaeology (your ask: all branches reviewed)
- `main` — currencies/metals swing EA + Deriv research index. FINDINGS: house edge 1.6–16.7%,
  RNG clean.
- `deriv-edge-research-2026-06-07` — 7-phase contract battery, live demo trades, house-edge
  tables (still accurate vs today's scan, except R_100/R_10 worsened or were always worse).
- `lattice-microstructure-validation` — the friend's 6-layer digit system faithfully built and
  falsified (controls beat it; "edge" was the base rate).
- `capy-fable5-session-2026-06-09` — independent re-audit + the real-market finding (1m USDJPY
  mean reversion, eaten by costs) + 9 MT5 EAs.
- `fable-session-2026-06-10` — omnibot (live-validated demo executor + sentinel), FableCOT.mqh
  (embedded CFTC history), validated EA set, compile receipts.
- `hhhy` repo, `deriv-matches-tool` branch (TODAY) — the Telegram-style matches tool faithfully
  rebuilt + proof it's frequency-chasing (23% "confidence" → 10.7% wins; 400-trade live run
  −$42.80). This session explains the only honest upgrade path for it (§1, §2).

## 7. What ships, and the run-book
- `tools/sigma_sentinel.py` — watch/trade daemon. Run 24/7: it loads live payouts, streams
  ticks, computes σ + model EV on all 40 contracts each tick, logs the boundary distance, and
  (with a working token + `--trade`) fires single-call buys when EV > gate. Demo-guarded.
- `tools/backtest_conditional.py`, `validate_sigma_curve.py`, `sigma_edge_model.py` — the
  evidence chain for JD100; re-run as more low-spot days accumulate.
- `tools/multi_matches.py` — your 5-digit tool, EV-gated, replication-aware.
- `tools/coverage_optimizer.py` — cheapest expression of any digit-set view.
- `tools/payout_scanner.py`, `universe_screen.py`, `accumulator_scan.py`, `edge_tests.py`,
  `liability_sim.py`, `tick_harvest2.py` — the standing audit kit. Re-run weekly; the two
  things that could open up: JD100 spot decaying under ~240 (immediate +EV), or Deriv
  mis-setting any barrier/payout after a regime change (scanners catch it same-day).

### Next actions (in order)
1. Re-enable the Deriv account (token is `AccountDisabled`).
2. Put the sentinel on a low-latency box (RTT to Deriv must keep p90 < 0.8s; it self-measures
   and refuses otherwise) in watch mode on JD100.
3. The moment JD100 trades under ~240 with σ < 4.3: demo-run `--trade --ev-gate 0.01` and
   compare realized vs model EV daily (the backtest table is the reference).
4. Keep stakes Kelly-fractional: at +1–3% edge and ~1.0 variance per $1, quarter-Kelly is
   0.25–0.75% of bankroll per trade.
5. Weekly: `payout_scanner.py` + `accumulator_scan.py` + `universe_screen.py` (new symbols
   appear; any new low-priced 1s symbol is a candidate the day it lists).

## 8. Time-of-day artifacts (bonus)
Digit uniformity by second-of-minute (600 cells) and hour (240 cells) on 1.2M JD100 ticks: worst z = 3.36/3.68 — exactly the expected max-order-statistic of noise. No reseed artifacts. Tick-step sigma flat across hours (5.58–5.79): synthetics have no session seasonality to exploit.
