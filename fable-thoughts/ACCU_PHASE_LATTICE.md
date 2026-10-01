# Accumulator phase lattice: the CRASH lead generalised, and everything else that died

Session 2026-09-24. A 27-agent research pass over this branch: five mappers, three hypothesis
generators with different lenses, and two adversarial refuters per hypothesis (one theory,
one empirical, both writing and running code). Everything was measured offline on the local
tick files in `data/` plus fixtures found on older branches. No network, no orders.

**Verdict up front.**
- No new, independent edge survived.
- Six of the nine hypotheses were refuted by both refuters. The other three were refuted by one and could not be settled offline by the other.
- What survived is a sharper and more general form of the one live lead (the CRASH1000/CRASH500 accumulator candidates), plus four facts this repo never had.
- The edge is still unproven at 99%. The tools in `tools/accu_phase_*.py` turn it into a GO / KILL decision against criteria fixed in advance (below). That decision needs data only your machine can fetch.

---

## 1. Four new facts (measured, not assumed)

### 1a. The Boom/Crash generator is one law

Split each tick into two kinds:
- **Normal step**, with probability 1 − 1/N: move WITH the drift by `spot × (1e-3/N) × Y`.
- **Spike**, with probability 1/N: move AGAINST the drift by `spot × 1e-3 × Y′`.

Here `Y ~ |N(0.8536, 0.8634)|`, a folded normal with mean exactly 1. Y′ has the same shape.

- **Fit:** one parameter pair fits CRASH1000, CRASH500, BOOM1000 and BOOM500 jointly (p = 0.34; per-symbol p = 0.16, 0.87, 0.12, 0.32).
- **Alternatives rejected by thousands of χ² points:** half-normal, truncated normal, gamma, Weibull, exponential, Rayleigh, generalised normal.
- **Direction separates spikes exactly:** 0 counter-drift normal steps in about 332k. No size threshold is needed.
- **Step size ∝ spot:** α = 1.04 [0.28, 1.80] on CRASH1000. The likelihood favours α = 1 over α = 0 by 23.6. So there is no JD100-style drift; the lattice effect below is pure phase.
- **No memory anywhere, every test against a local-shuffle control:**
  - step autocorrelation at lags 1–20;
  - conditional tail P(step > K | previous step);
  - pip-level MI (Miller–Madow corrected, within ±0.6 millibits of shuffle);
  - post-spike regime;
  - hour of day;
  - last-digit and parity.
- **Spike timing is geometric:** KS p 0.15–0.92, flat hazard by age. Timing can gain at most 1/N per tick, and the measured gain is zero.
- **Exception, the N-series:** BOOM300N and CRASH300N normal steps are about 2.5× this law (8.7–9 pips, not 3.35). That came from the repo's own 2026-06-07 ticks in `deriv-edge-research-2026-06-07:boom-crash/spike_analysis.json`, which nobody had connected.

**Artifact warning for future work:** MI on spot-scaled bins shows z = 127. It is caused by the shared lattice and spot between neighbouring ticks, not by dependence. A global shuffle is the wrong control there; a local shuffle removes it.

### 1b. Deriv's accumulator barrier ladder, reverse-engineered

`b_g = κ_g × 1e-3 / N`, where κ_g solves `F_Y(κ_g) = P_g / (1 − 1/N)` and P_g is:

| g | 1% | 2% | 3% | 4% | 5% |
|---|---:|---:|---:|---:|---:|
| P_g | 0.985 | 0.977 | 0.967 | 0.9575 | 0.9465 |

- **Fit to recorded barriers:** it reproduces all six Boom/Crash barriers the repo ever recorded, to within +0.01% to +0.25%.
- **Ratio check:** b3/b4 is 1.0433 and 1.0441 recorded, against 1.0435 and 1.0443 predicted. A Gaussian ladder would give 1.051, which the recorded values reject.
- **Volatility indices:** the same P_g reproduce them exactly, as `b = σ_tick · Φ⁻¹((1+P_g)/2)`.
- **Consequence:** averaged over phase, the house is exactly calibrated (G ≈ 0.9947 at 4%). The only lever left is the pip lattice.

### 1c. `ticks_stayed_in` is a free oracle for the house knockout rule

Every ACCU proposal returns `contract_details.ticks_stayed_in`: the house's own last 100 run lengths, oldest first, with the last entry the run still in progress. Replaying ticks under a candidate rule and aligning the resulting runs against that list tests the rule with zero money.

| symbol | runs matched (repo rule) | shuffled-list control (max of 200) |
|---|---:|---:|
| R_100 | 99 completed + in-progress 19 | 3 |
| 1HZ100V | 75 / 75 | 3 |
| R_25 | 73 / 73 | 3 |

The repo rule:
- the barrier is `b × previous displayed spot`, exact and not rounded;
- a move that touches the barrier knocks out.

All three exact-touch events (1HZ100V at phase 0.9054 and 0.9604, R_25 at 0.9633) were knockouts, so the "ceil_strict" reading is falsified (it scores 44 and 53). **Phase [0.9, 1) is confirmed as the worst band.**

### 1d. The sawtooth has a closed form

```
P(stay | K, φ) = (1 − λ) · E_u[ F_Y( κ (K + 1 − u) / (K + φ) ) ]
```
where K = floor(w), φ = frac(w), w = b × spot / pip, and λ is the spike rate.

- **Jump at each integer crossing:** (1+g)·f_Y(κ)/μ_pips, where μ_pips is the mean normal step in pips (spot/N for pip 0.001). The jump is 0.0171 at K = 14.
- **Measured:** +0.0192 (z = 3.4) on CRASH500.
- **Fit:** the model is parameter-free. It reproduces survival in every phase bin to within 1–2 SE, predicts CRASH1000 from CRASH500 (z = 0.8), and virtual-barrier controls came out positive in 13/13 and 7/7 cells.
- **Functional-form trap, number four for this repo:** the naive `P(|r| ≤ b·floor(w)/w)` underestimates survival by about half a pip of probability and fails the collapse test (z = 3.4 and 6.0). Only the continuity-corrected form passes.

---

## 2. What survived: phase-gated Crash ACCU with growth-rate choice

On CRASH1000 and CRASH500, per-tick `G = (1+g)·P(stay)` is above 1 only in the phase band **[0.05, 0.125)** at integer levels K ≤ K*(g). It is below 1 everywhere else.

| | value |
|---|---|
| Model G, CRASH1000 4% K=14 band | 1.00133 – 1.00165 (two kernels) |
| Direct, CRASH500 K=14 phase [0, .125) | 1.00239, 99% CI [0.9979, 1.0064], n = 13.8k |
| Spot coverage with an eligible (g, K) cell | ~13–14% (vs ~8% using 4% alone) |
| Mean G when eligible | ~1.0013, about +2.6% per 20-tick 4% contract |

How to read these numbers:
- **Why growth-rate choice helps:** each growth rate has its own barrier and therefore its own phase at the same spot. Picking whichever g is in band roughly doubles the time a cell is tradable.
- **1% is never eligible:** its barrier puts K too high for the sawtooth to beat the margin.
- **The CRASH500 interval includes zero.** The local files hold only one day in band.

**Corrections to the existing bots:**
- **The frozen gate [14, 14.25) is marginal.** Model G is 1.0008 [0.9999, 1.0019], worth about +1.6% per trade.
- **The core [14.05, 14.125) is about 1.0015**, worth about +2.9%. If `crash1000_accu_audit.py` or `crash500_accu_audit.py` runs at all, narrow its gate to that core.
- **Phase [0, 0.05) is unresolved.** Barrier display rounding could make an exactly-K move a knockout there. The oracle check (OR-2) in `accu_phase_decide.py` settles it for free.
- **The historical +7.38% CRASH1000 replication figure is likely a tick-20 off-by-one.** The live run's 6/20 wins has about a 7% chance under the model, so it neither confirms nor refutes.

---

## 3. Refuted, each with its killing argument

| hypothesis | killed by |
|---|---|
| BOOM300N has the coarsest lattice, ~3× edge | Repo's own June ticks: steps 8.73 pips, not 3.35 (z ≈ −13 on zero-step share). K ≈ 21, finer than CRASH; band G ≈ 1.0004, which needs ~87k trades to detect. |
| Vol-index ACCU: fixed pip + decaying spot = JD100 failure again | No cell has G ≥ 1 at today's σ_pips 12.9–17.2. It turns positive only below ~9.6 (R_100 < ~381, 1HZ100V < ~539). Only in-regime sample: G 0.99985 [0.9970, 1.0026]. Kept as a watcher alert, not an edge. |
| Barrier displayed rounded up, touch doesn't knock out → phase (0.9, 1) is a hidden good band | Oracle: all 3 exact touches were knockouts; the rule matches 247/247 runs. |
| Cent rounding: stake $1.13 @4%, 1-tick take-profit pays 1.18 not 1.1752 | Bonus 0.37–0.41% vs margin 0.35–0.62%: net −0.23% to +0.03%. Inside the lead band it earns less per stake-tick than the 20-tick hold. Still worth three demo wins to settle the untested ROUND_HALF_UP assumption in `payout_terms`. |
| Spikes cluster in UTC seconds 50–59 (1.46×, p = 0.0004 scan-corrected) | The window picked on 3 symbols fails on the 4th (ratio 1.04, p = 0.38); all data is one UTC day; worth ≤ 0.03%/tick even if real. The `analyze` step re-tests it on ≥ 1500 spikes. |
| Age-since-spike renewal on N = 50 / 150N | Prior ≤ 10%: BOOM300N gap CV 1.026 already rejects the uniform-gap law. Re-tested for free on the fetch. |
| 8 never-audited ACCU symbols; N = 50 at 1% is impossible | Spikes alone cap G at 0.9906 / 0.9996 on N = 50 at 1–2%; on N = 600/900 the spike lever is ≤ 0.17%/tick, below the margin. |

---

## 4. The decision protocol (fixed before seeing the data)

Run `accu_phase_decide.py all` (read-only, no token, about 60–75 min). It:
1. harvests the barrier ladder for every Boom/Crash symbol at every growth rate;
2. takes oracle snapshots;
3. fetches 150k–600k ticks per symbol;
4. analyses them and writes `summary.txt`. Its last line is `VERDICT: PASS-A | PASS-B | KILL | INCONCLUSIVE <reasons>`.

**PASS-A** (go to demo mechanics) requires all of:
- **A1:** sawtooth contrast z ≥ 3 with an observed/model ratio in [0.65, 1.35];
- **A2:** the model χ² passes (p ≥ 0.01) on each Crash symbol;
- **A3:** the model lower 99% bound ≥ 1 on every tradable cell;
- **A4:** the direct measurement is not significantly below the model;
- **A5:** the placebo and ungated controls are below 1;
- **A6:** the oracle confirms the knockout rule on both Crash symbols;
- **A7:** the 4% barriers are unchanged.

**PASS-B** (the minimum before real money) requires PASS-A, plus a direct 99% lower bound ≥ 1 over at least 16 hour-blocks. Expect that to take about 270k in-band ticks, i.e. 2–3 weeks of the watcher.

**KILL** on any one of:
- a direct upper bound < 1 on ≥ 100k in-band ticks;
- a model upper bound < 1 under both kernels;
- a sawtooth z < 1 on ≥ 300k ticks;
- an oracle rule failure.

Tick history only reaches back about 4.6 days, so `accu_phase_watch.py` must keep archiving ticks and oracle snapshots for the 2–3 weeks PASS-B needs.

### How to run it (read-only: no token, no orders)

```sh
pip install "websocket-client>=1.6" numpy scipy
cd fable-thoughts/tools
python3 -m unittest test_accu_phase test_accu_phase_watch   # 45 offline tests, ~40 s

# 1. one-shot: barrier ladder + oracle + ~4M ticks + analysis (~60-75 min)
python3 accu_phase_decide.py all

# 2. leave running for >= 14 days (tick history only reaches back ~4.6 days)
python3 accu_phase_watch.py --hours 0 --cells ../results/accu_phase_<UTC>/analysis.json
```

**Send back after step 1:**
- the stdout;
- `results/accu_phase_<UTC>/` (`ladder.json`, `oracle_*.json`, `analysis.json`, `summary.txt`);
- the `ticks/` folder, zipped.

**Send back weekly from the watcher:**
- `summary.log` and `alerts.jsonl`;
- `quotes.jsonl`;
- `oracle/` and `ticks/`, zipped.

**Offline smoke test on the repo's own data:** from `fable-thoughts/tools`, run
```sh
python3 accu_phase_decide.py analyze \
  --local-json ../../data/CRASH1000.json.gz ../../data/CRASH500.json.gz \
               ../../data/BOOM1000.json.gz ../../data/BOOM500.json.gz \
  --barrier CRASH1000=0.04:2.3454e-6 CRASH1000=0.03:2.447e-6 \
            CRASH500=0.04:4.7141e-6 CRASH500=0.03:4.9222e-6 \
            BOOM1000=0.03:2.4506e-6 BOOM500=0.03:4.9295e-6
```
Those are the six barriers the repo has recorded. It returns `INCONCLUSIVE` with these numbers:
- CRASH500 band G = 1.00246;
- pooled D = 1.00094 [0.99746, 1.00412] on 23k in-band ticks;
- no oracle data (A6 fails).

That is the honest state of the evidence today.

**Known conservative biases, left as pre-registered:**
- **The model underestimates survival near phase 0** by 0.0004–0.0010. The V2 kernel adds rounding noise twice. So A3, K2 and eligibility lean toward "no".
- **C1 (ungated G < 0.998) depends on which phases the archive happens to cover.** It can fail on a short, phase-lucky archive and converges as spot wanders.

Both biases push toward INCONCLUSIVE, never toward a false PASS.

## 5. Why this is the right posture

This is the same shape as the only edge that ever existed here: a parameter calibrated on an average (the barrier) meeting a state the trader can observe and select on (lattice phase).

The weaknesses need stating plainly:
- **The edge is small:** about +0.13% per tick in band.
- **It is only open part of the time:** about 13% of spot levels.
- **The counterparty can close it in one release**, by quantising barriers to the pip or recalibrating per phase. Their demonstrated reaction time on JD100 was days.

Size accordingly: $1 stakes until PASS-B. Treat any `BARRIER_CHANGE` alert from the watcher as a stop.

---

## 6. First live decision run (2026-09-26)

`accu_phase_decide.py all` on 19–26 Sep ticks (600k CRASH1000/500, 300k other Boom/Crash), rescored after
two oracle-scoring fixes made **after** seeing KILL verdicts (commits 2947237, 13422b0):

- **Barrier ladder (law A):** 32 new growth-rate ratios on N = 500/600/900/1000 predicted to <= 0.07%.
- **Oracle:** 80/80 cells match all 99 completed house runs under raw_incl (shuffled null <= 5). The
  in-progress entry is 1 tick short in 10 cells (delta -1), which is what the original scoring tripped on.
- **Decision:** D = 1.00327, 99% [1.00202, 1.00454], 191,566 in-band ticks, 72 blocks; C4 z = 37.7 at
  0.97x model; placebo 0.990; 20-tick replay CRASH1000 4% +6.3%/trade [+3.3%, +9.3%], placebo -19%.
  **VERDICT: PASS-B** under the rescored oracle.
- **Caveats:** the pass definition changed twice after results were seen; the data window overlaps the
  previous run almost entirely (not an independent replication); everything is feed replay, no executions.
- **BOOM50/CRASH50/BOOM150N/CRASH150N: dead.** They quote the 1HZ10V Gaussian ladder, so the model predicted
  spike-only knockouts and G up to 1.04/tick. Deriv's own lists refute it: 0/99 completed runs match
  (longest 1-10, same as shuffled), and on BOOM150N the house runs are ~330 ticks shorter than the replay.
  The knockout rule on these symbols is not the one modelled; the apparent edge was a model error.
- **BOOM300N (exploratory, not pre-registered):** spot fell from ~1004 (June) to ~400, coarsening the
  lattice to K = 7-9. D = 1.00386 [1.00256, 1.00508], replay +8.4%/trade, oracle 99/99 in 10/10 cells.
  Needs its own fresh-data test.

**Correction (same day): the PASS-B above is withdrawn.** Every proposal in these tools was public, and
logged-in accounts are sold tighter barriers (CRASH1000 4%: 2.3454e-6 public vs 2.28724e-6 authenticated;
CRASH500 4%: 4.7141e-6 vs 4.598554e-6; BOOM300N 2-5% also tighter; BOOM1000 4% equal). The 12 Sep demo
run executed at 2.3454e-6, so the authenticated barrier was tightened after that. A7 compared public to a
public-era record and could not catch it. Re-evaluated on authenticated barriers (reported independently),
the CRASH1000 4% best band is about -6.3% per trade and BOOM300N 3% about -11.5% on the same ticks.
Commit 3bfac08 moves all proposals to authenticated demo terms and makes A7 check the barrier you can
buy. Status: read-only until an authenticated cell passes on fresh data not seen during design.

### 6b. Re-analysis on authenticated barriers (2026-09-26)

Same ticks and oracle, barriers from `ladder_auth` (demo token; 45/85 cells tighter than public). Rules,
phase band and thresholds unchanged from the pre-registration; nothing re-fitted.

| | public barriers (information only) | authenticated barriers |
|---|---|---|
| pooled D | 1.00327 [1.00202, 1.00454] | n/a: no tradable cell |
| tradable cells (A3) | CRASH1000/500 2-5% | 0 |
| C1 (model calibration) | pass | 0.98949, fails A5 |
| oracle OR-1 | PASS | PASS (unchanged) |
| A7 | n/a | PASS, but the barrier inside the window is assumed, not observed |
| CRASH500 frozen-band replay | positive | **-8.78%/trade, 99% [-11.0%, -6.6%]** |
| verdict | PASS-B | **INCONCLUSIVE** (A3, A4, A5 not met) |

- **CRASH lattice: closed on buyable terms.** The tightening lands exactly on the cells where the band
  had positive G; CRASH1000 4% has no in-band ticks and its best band is G 0.98151. The one eligible level
  (CRASH1000 5%, K7) is not reached at current spot. The untouched cells (CRASH300N/600/900, 1% rates)
  never had an edge.
- **N=50/150N: closed.** All cells below 1 on both barrier sets; the oracle already refuted the model.
- **BOOM300N: not tested.** Eligible only at K2-K4, which needs spot ~170-211 (currently ~400). If spot
  gets there, it needs a fresh-data test at authenticated barriers, pre-registered before the data is seen.
- **Reading of the evidence:** Deriv's authenticated terms appear to be set per cell to remove the lattice
  surplus. The public proposal is not the price a customer pays, so any edge measured on public quotes is
  not evidence of anything.
