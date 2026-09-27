# Cross-asset digit grid audit — where the lattice edge lives, and where Deriv removed it

Session 2026-09-27. Authenticated demo session `DOT93131975`, minimum stake $0.35,
Deriv Options API (`api.derivws.com`). Tools: `grid_snapshot.py`, `payout_probe.py`,
`endpoint_compare.py`, `jump_lattice_scan.py`, `jd100_counterfactual.py`.

This supersedes the earlier reading of `payout_audit.md`, which concluded that the
proposal endpoint lies. **It does not.** Two things were conflated, and separating them
changes the conclusion.

---

## 1. Three corrections to the earlier record

### 1a. The proposal endpoint does not lie

`payout_probe.py` quotes and fills on ONE authenticated socket, in one atomic block,
recording the proposal's `spot_time` and the buy's `purchase_time` so the pairing is
checkable.

```
 #  proposal  executed  ratio tick d prop dig exec dig  verdict
 1    0.4700    0.4700  1.000      0        9        9  SAME-TICK: MATCH
 2    0.4700    0.4700  1.000      0        1        1  SAME-TICK: MATCH
 4    0.4700    0.4700  1.000      0        2        2  SAME-TICK: MATCH
 5    0.4700    0.4700  1.000      0        6        6  SAME-TICK: MATCH
 7    0.4700    0.4700  1.000      0        5        5  SAME-TICK: MATCH
 8    0.4700    0.4700  1.000      0        6        5  SAME-TICK: MATCH

probes ok: 8/8   same-tick pairs: 6
same-tick executed/proposal ratio: min 1.0000 max 1.0000
```

JD100 `DIGITOVER 4`, $0.35 stake, proposal payout 0.47, contracted payout 0.47,
`1.3429x` both ways. Eight for eight, and exactly 1.0000 on every same-tick pair.

### 1b. The real mechanism is a tier split, not a lying endpoint

`payout_audit.py` used **two different sockets** inside one loop:

```python
ws = DerivWS(token="")   # PUBLIC socket        -> proposal
tr = DerivWS()           # AUTHENTICATED socket -> buy
```

`endpoint_compare.py` quotes the same contracts back to back on both tiers:

| contract | public | authenticated | | contract | public | authenticated |
|---|---:|---:|---|---|---:|---:|
| DIGITOVER0 | 1.09 | *delisted* | | DIGITOVER5 | 2.43 | 1.54 |
| DIGITOVER1 | 1.23 | *delisted* | | DIGITOVER6 | 3.21 | 1.82 |
| DIGITOVER2 | 1.40 | 1.05 | | DIGITOVER7 | 4.72 | 2.22 |
| DIGITOVER3 | 1.63 | 1.18 | | DIGITOVER8 | 8.93 | 2.86 |
| DIGITOVER4 | 1.95 | 1.33 | | DIGITUNDER8/9 | 1.23 / 1.09 | *delisted* |

All 18 rows differ. The public socket still serves the **old intact grid**; the
authenticated session serves the **restricted grid** and returns
`"This contract offers no return."` for OVER0, OVER1, UNDER8, UNDER9.

So every "PROPOSAL LIES (1.886 vs 1.343)" row compared a public quote against an
authenticated fill across a repricing in flight. It was a **tier artifact plus a
tick-state shift**, and `payout_audit.py` has been fixed to quote and fill on the same
session and to only say `ENDPOINT LIES` when proposal and fill share an epoch. Re-run:

```
| DIGITOVER4 | 1.953 | 1.342857142857143 | 1.342857142857143 | 0.6876 | CUT 31.2% |
```

`proposal == executed` on every row that trades. The cut is real; the lie was not.

### 1c. The stale table in `quantum_lattice_sentinel.py` was wrong in both directions

The previous `AUTHENTICATED_EXECUTED_PAYOUTS` had `OVER4 = 1.8000` and `OVER8 = 6.3429`.
That matched neither the 1.95 the public tier quoted nor the 1.33 the authenticated tier
fills — it was an intermediate guess that happened to sit inside the old edge zone. It is
now replaced by the verified live grid, with the historical grid kept beside it as
`HISTORICAL_INTACT_PAYOUTS` so the counterfactual stays reproducible.


---

## 2. The digit book, symbol by symbol

`grid_snapshot.py --all`, authenticated session. `full` = 18 OVER/UNDER barriers;
`CUT` = restricted ladder with delisted wings.

| symbol | family | grid | OVER4 | OVER8 | MATCH0 | delisted |
|---|---|---|---|---|---|---|
| **JD10** | Jump | full | 1.95 | 8.93 | 8.93 | — |
| **JD25** | Jump | full | 1.95 | 8.93 | 8.93 | — |
| **JD50** | Jump | full | 1.95 | 8.93 | 8.93 | — |
| **JD75** | Jump | full | 1.95 | 8.93 | 8.93 | — |
| **JD100** | Jump | **CUT** | **1.33** | **2.86** | **2.86** | OVER0, OVER1, UNDER8, UNDER9, DIFF0 |
| R_10 / R_25 / R_50 / R_75 | Vol 2s | full | 1.95 | 8.93 | 8.93 | — |
| R_100 | Vol 2s | full | 1.92 | 8.33 | 8.33 | — |
| 1HZ10V / 1HZ100V | Vol 1s | full | 1.92 | 8.33 | 8.33 | — |
| 1HZ25V / 1HZ50V / 1HZ75V | Vol 1s | full | 1.95 | 8.93 | 8.93 | — |

The restriction is **JD100 and nothing else**. 14 of 15 indices carry the intact grid.

---

## 3. But the intact indices cannot use it — sigma is the gate

The lattice edge needs tick sigma measured in **pips** to be small, because that is what
makes the next digit sticky and `P(d_{t+1} | d_t)` non-uniform. The walk-forward in
`SESSION_2026-08-04.md` crossed zero at **sigma 3.56 pips**.

`jump_lattice_scan.py`, 40,000 live ticks per symbol, policy optimised against the live
quoted grid, validated on a 50/50 temporal split:

| symbol | sigma (pips) | grid | in-sample EV | **OOS EV** | OOS t | verdict |
|---|---:|---|---:|---:|---:|---|
| JD10 | 11.29 | full | -0.20% | **-3.36%** | -3.1 | negative |
| JD25 | 11.04 | full | -0.36% | **-3.09%** | -3.4 | negative |
| JD50 | 11.01 | full | -0.52% | **-3.70%** | -4.4 | negative |
| JD75 | 11.26 | full | -0.59% | **-2.50%** | -3.4 | negative |
| **JD100** | **2.38** | CUT | -10.24% | **-9.45%** | -21.1 | negative |

Every Jump index except JD100 runs at sigma ~11 pips — **three times the 3.56 crossing**.
At that width the digit is not sticky, `P(d' | d)` is flat, and the policy degenerates to
buying the cheapest house margin: about -3%, which is what the OOS numbers show, and the
lag-2 and permutation controls sit at essentially the same place.

**This is a trap, and it is the answer to the question.** The four indices that still carry
the full grid have microstructure ten times too wide for the lattice. The one index whose
sigma (2.38 pips) sits comfortably inside the edge zone is the one Deriv restricted.


---

## 4. The counterfactual: same ticks, two grids

`jd100_counterfactual.py`, 60,000 real JD100 ticks, sigma 2.38 pips, identical digits,
identical policy engine. Only the payout grid differs.

```
JD100
  public tier        : 18 digit contracts quoted
  authenticated tier : 14 digit contracts quoted
  public-only rows   : [('OVER',0), ('OVER',1), ('UNDER',8), ('UNDER',9)]

  [INTACT / PUBLIC grid]        OOS EV  +38.48%  99% CI [+35.60%, +41.36%]  t = 34.4
  [AUTH grid, what fills]       OOS EV  -10.06%  99% CI [-11.01%,  -9.11%]  t = -27.4
```

**Deriv's repricing is worth 48.5 percentage points of expected value** on this index,
measured on the same ticks with the same signal. The lattice signal is not noise: under the
old book it produces +38.5% OOS with t = 34.4, and the permutation control on the old book
sits at -5% (the house margin), so the control behaves exactly as the theory requires.

### A correction to the original negative controls

The lag-2 control does **not** collapse to zero under the intact grid: it reads +9.40% in
`jd100_counterfactual.py` and +10.75% in `quantum_lattice_sentinel.py --grid historical`.
`QUANTUM_LATTICE_FINDINGS.md` §4.3 claimed it fell to -0.17%.

That claim was wrong, and the reason matters. JD100 sigma is 2.38 pips; two ticks of
diffusion give `2.38 * sqrt(2) = 3.37` pips, which is still below the 3.56 breakeven and
therefore still inside the lattice. Lag-2 genuinely retains part of the edge, because the
digit is still sticky two ticks out. The lag-2 test is therefore **not** a valid
decoherence control for this strategy — it is only a control for a strategy whose horizon
is one tick long. The permutation control, which does collapse to the house margin, is the
control that carries weight.

The signal is real. The book it needed is gone.

---

## 5. Where that leaves each market

| market | microstructure | book | status |
|---|---|---|---|
| JD100 digit | sigma 2.38 pips, in the edge zone | **restricted** | **dead** — -10.06% OOS at the live grid |
| JD10/25/50/75 digit | sigma ~11 pips | intact | dead — no lattice, -2.5% to -3.7% OOS |
| Volatility digit (all 10) | sigma 12.9–17.2 pips | intact | dead — no lattice, -3% to -4% OOS, confirms FINDINGS.md §3 |
| CRASH/BOOM accumulator | spike law + pip lattice | **tighter on buyable terms** | **dead** — 0 tradable cells, see below |

### The accumulator lead I quoted is WITHDRAWN — and the same tier split killed it

I wrote above that the CRASH/BOOM accumulator phase lattice "is untouched by this repricing"
and quoted its `D = 1.00327 ... VERDICT: PASS-B`. **That was wrong, and the number is
stale.** The same document withdraws it two sections below the headline I read:

> **Correction (same day): the PASS-B above is withdrawn.** Every proposal in these tools was
> public, and logged-in accounts are sold tighter barriers (CRASH1000 4%: 2.3454e-6 public vs
> 2.28724e-6 authenticated; CRASH500 4%: 4.7141e-6 vs 4.598554e-6; BOOM300N 2-5% also
> tighter; BOOM1000 4% equal). … Re-evaluated on authenticated barriers, the CRASH1000 4%
> best band is about -6.3% per trade and BOOM300N 3% about -11.5% on the same ticks.

`ACCU_PHASE_LATTICE.md` §6b, its own re-analysis on authenticated barriers:

| | public barriers (information only) | authenticated barriers |
|---|---|---|
| pooled D | 1.00327 [1.00202, 1.00454] | n/a: no tradable cell |
| tradable cells (A3) | CRASH1000/500 2–5% | **0** |
| C1 (model calibration) | pass | 0.98949, **fails A5** |
| CRASH500 frozen-band replay | positive | **-8.78%/trade, 99% [-11.0%, -6.6%]** |
| verdict | PASS-B | **INCONCLUSIVE** |

**So the answer to "did you test this with the authenticated barrier?" is: the repo did, and
it failed.** I quoted the pre-correction headline. That is the second time in this session the
same failure mode produced a false positive — a public-tier quote being compared against an
authenticated-tier fill.

#### Verified live today (2026-09-27)

`accu_tier_compare.py`, proposals only, no orders:

```
cells compared: 20   auth TIGHTER: 13   equal: 7   looser: 0
cells where the integer K band changed: 7
   CRASH1000 g=0.04  K 14 -> 13
   CRASH500  g=0.02  K 16 -> 15
   CRASH500  g=0.03  K 15 -> 14
   CRASH500  g=0.05  K 14 -> 13
   BOOM300N  g=0.02  K 9 -> 8
   BOOM300N  g=0.04  K 8 -> 7
   BOOM300N  g=0.05  K 8 -> 7
```

The first line is the whole story. The frozen candidate gate is **phase in [14, 14.25)**,
which requires `w = b·spot/pip` to have integer part **K = 14**:

```
CRASH1000 4%  public:   w = 14.075  -> K = 14, phase 0.075  IN BAND
              auth:     w = 13.726  -> K = 13, phase 0.726  nowhere near
```

A 2.48% tighter barrier moves the lattice one whole pip step and the gate can never fire on
the price you can actually buy. That is why §6b found "CRASH1000 4% has no in-band ticks".
The tightening is targeted, not incidental: it lands exactly on the cells where the band had
positive G, while `BOOM1000` (which never had an edge) is left identical across all five
growth rates.

**Status: the accumulator phase lattice is closed on buyable terms.** `ACCU_PHASE_LATTICE.md`
§6b reached that verdict on 2026-09-26 and it still holds today.

---

## 6. What to watch

Two one-line checks, either of which changes the picture:

1. `python grid_snapshot.py --all` — a second repricing on any index shows up in a second.
   Watch JD10/25/50/75 and the vol indices for a `CUT` flag, and watch JD100 for a
   **restoration** (that would reopen the edge immediately at sigma 2.38).
2. Sigma drift on JD100. The edge needs sigma < 3.56. It is 2.38 today, deeper into the
   zone than anything in the walk-forward (best band 4.15–4.30), so the *signal* is
   stronger now than when the strategy was written. If Deriv ever restores the wings, this
   is tradable the same day.

## 7. Reproduce

```sh
export DERIV_TOKEN=... ; export DERIV_APP_ID=...
cd fable-thoughts/tools

python grid_snapshot.py --all                    # full grid, every index, read-only
python endpoint_compare.py --symbol JD100        # public vs authenticated book
python payout_probe.py --symbol JD100 --barrier 4 --n 8   # quote vs same-tick fill
python payout_audit.py --symbol JD100            # regenerates results/payout_audit.md
python jump_lattice_scan.py --ticks 40000        # sigma + OOS EV per Jump index
python jd100_counterfactual.py --ticks 60000     # same ticks, both grids
python quantum_lattice_sentinel.py --grid live       --ticks 50000
python quantum_lattice_sentinel.py --grid historical --ticks 50000
```

### Raw evidence captured in `results/`

| file | what it is |
|---|---|
| `grid_snapshot.json` | all 15 indices, full authenticated grid, machine-readable |
| `payout_audit.md` | regenerated with same-session quote/fill; `proposal == executed` on every row |
| `payout_audit_stdout.txt` | the ladder + the restricted-ladder EV verdict |
| `jump_lattice_scan.json` | per-symbol sigma, per-digit policy, OOS EV, controls |
| `jump_lattice_scan_stdout.txt` | the summary table in §3 |
| `jd100_counterfactual.json` | same-tick EV under both grids, per-digit policy |
| `jd100_cf_stdout.txt` | the +38.48% / -10.06% comparison |
| `sentinel_hist.txt` | `--grid historical` run: OOS +41.46%, t = 28.06 |
| `sentinel_live.txt` | `--grid live` run: OOS -8.17%, t = -12.81 |
| `live_engine_gate_check.txt` | the live engine measuring the gate and refusing to trade |
