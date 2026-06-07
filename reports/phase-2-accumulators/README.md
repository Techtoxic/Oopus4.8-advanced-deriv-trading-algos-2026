# Phase 2 — Accumulators

**Contract:** `ACCU`. Balance grows by a fixed **growth rate per tick** as long as the spot stays
within a symmetric per-tick barrier band. A single barrier breach = total loss of stake.
Growth rates offered: 1%, 2%, 3%, 4%, 5% per tick (barrier widens/narrows to match).

This is a **volatility** product, not a digit product. The question: is there an exit strategy or a
timing window that produces +EV?

## The EV identity (why this is decidable in closed form)

Each tick, the contract either survives (prob `p`, balance ×`(1+g)`) or breaches (prob `1-p`, balance →0).
Holding for exactly `k` ticks and cashing out:

$$ \mathrm{EV}(k) = \big[(1+g)\cdot p\big]^k \cdot \text{stake} $$

- If `(1+g)·p = 1` the product is **fair** (zero edge) at every horizon.
- If `(1+g)·p < 1` there is a **per-tick house edge** and `EV(k)` is **strictly decreasing in k** —
  every additional tick held loses money in expectation, so **no profitable exit point exists**;
  the EV-maximising hold is `k = 0` (i.e. don't trade).

Break-even at `g = 1%` requires `p ≥ 1/1.01 = 0.990099`, i.e. an average run length of **100 ticks**.

## Real data (Deriv-provided `ticks_stayed_in`, 100 recent runs/market) + live spot barriers

Pulled live via `proposal` (`growth_rate = 0.01`). `ticks_stayed_in` is Deriv's own record of how many
ticks recent accumulators on that symbol survived — real live outcomes.

| Symbol | mean run | median | implied p_survive | per-tick EV mult | **per-tick house edge** | barrier band |
|--------|---------:|-------:|------------------:|-----------------:|------------------------:|-------------:|
| R_100   | 66.4 | 43 | 0.98516 | 0.99501 | **+0.499%** | ±0.0613% |
| 1HZ100V | 62.1 | 41 | 0.98415 | 0.99399 | **+0.601%** | ±0.0433% |
| R_25    | 65.5 | 44 | 0.98497 | 0.99482 | **+0.518%** | ±0.0153% |

`implied p_survive = L/(L+1)` from the geometric mean run length `L`; cross-checked against the median
(geometric median ≈ `ln0.5/ln p` ≈ 46 for p=0.985, observed 41–44 — consistent).

### Survival curve (memoryless / geometric)

| Symbol | %>10t | %>20t | %>50t | %>100t | max |
|--------|------:|------:|------:|-------:|----:|
| R_100   | 90% | 75% | 45% | 25% | 364 |
| 1HZ100V | 79% | 71% | 45% | 22% | 259 |
| R_25    | 79% | 70% | 45% | 19% | 469 |

The roughly-constant survival hazard (each band halves over a fixed number of ticks) is the signature
of a **memoryless** process: "I've already survived 30 ticks" carries **no** information about the next
tick's breach probability. So Phase 2 Step 3's "optimal exit given the survival curve" reduces to the
EV identity above — and that identity says hold = 0.

## Step-by-step findings

- **Step 1 — Volatility profiling:** per-tick moves are symmetric, roughly Gaussian, with no volatility
  clustering strong enough to break the constant-hazard fit (ACF of |returns| within noise; see Phase 3/5).
- **Step 2 — Barrier breach distribution:** mean ≈ 62–66 ticks, median ≈ 41–44, geometric shape.
  `p_survive ≈ 0.984–0.985` vs the **0.990099** needed for break-even.
- **Step 3 — Optimal exit:** none exists with positive EV. `EV(k)=0.995^k` ⇒ holding the *median* 43
  ticks has EV ≈ `0.995^43 ≈ 0.806` (expected **−19%** despite "surviving"); holding 100 ticks ≈ `0.606`
  (**−39%**). The longer the target, the worse.
- **Step 4 — Live testing:** the Deriv-provided `ticks_stayed_in` *is* live outcome data; it confirms
  `p < 1/(1+g)`. (A supplementary live ACCU batch is included where run — accumulators take 40–60s each,
  so samples are smaller than digit batches.)
- **Step 5 — Volatility timing:** synthetic indices have no calendar/session structure (they run 24/7 on
  a constant-variance generator), so there is no "calmer time window" to exploit. Survival hazard is
  stationary across the sample.

## Verdict

**No +EV.** Accumulators carry a **+0.50%–0.60% per-tick** house edge that **compounds** with hold time.
There is no exit rule, no timing window, and no survival-curve trick that turns it positive, because the
breach process is memoryless and `(1+g)·p < 1`. Data: [`../../accumulators/`](../../accumulators/).
