# Deriv Edge Research Agent

Systematic, data-only investigation into whether **any positive expected value (+EV)**
exists on Deriv synthetic indices, across every contract family. All tests run against
the **official Deriv API on a demo account** (`VRTC10502381`, 10,000 USD virtual).

> **Operating principle:** No bias. Follow the data. Conclude only from real, statistically
> significant evidence. "No edge" is a valid, evidence-backed result — and so would be "edge found."

## TL;DR verdict

After pulling **50,000+ real ticks per analysis pass**, running the full randomness/edge battery,
documenting exact house edges from live proposal pricing, and executing **live demo trades**, the
result is unambiguous:

**No positive expected value was found in any contract family.** The synthetic-index RNG is
statistically indistinguishable from an independent, identically-distributed (IID) uniform process.
Every contract carries a built-in house edge ranging from **~1.36% (lowest-payout digit/Rise-Fall)
to ~16.67% (Digit Matches / longshot Over-Under)**, and **no conditioning information exists** that
would let a strategy overcome it. The full statistical case is in [`/reports/final`](reports/final).

See [`reports/final/MASTER_REPORT.md`](reports/final/MASTER_REPORT.md) for the verdict table,
the N+1→N+2 master finding, and the honest "closest-to-fair" analysis.

## Repository layout

```
/digits            Phase 1: Over/Under, Even/Odd, Matches, Differs  (+ raw tick CSVs)
/accumulators      Phase 2: survival-curve & per-tick edge analysis
/multipliers       Phase 3: commission / directional-predictability analysis
/boom-crash        Phase 4: spike-interval (geometric) analysis
/higher-lower      Phase 5: Rise/Fall duration & momentum tests
/touch-no-touch    Phase 6: barrier-distance pricing
/vanillas          Phase 7: IV vs RV notes
/shared
  /api             DerivClient (websocket v3 wrapper, req_id correlation)
  /stats           Randomness/edge test suite (chi2, runs, ACF, FFT, entropy, transition)
  /utils           Phase runners & live trade harness
/reports
  /phase-1..7      Per-phase reports
  /final           Master report
```

## Reproduce

```bash
pip install numpy scipy pandas websocket-client
export DERIV_TOKEN=<your_demo_token>      # never commit this
python3 shared/utils/phase1_digits.py     # pull ticks + full digit battery
python3 shared/utils/house_edge.py        # exact house edge from live proposals
python3 shared/utils/conditional_strategies.py   # test every 'pattern' strategy
python3 shared/utils/tick_mechanic.py     # map N / N+1 / N+2 settlement ticks
python3 shared/utils/phase4_boomcrash.py  # Boom/Crash spike structure
python3 shared/utils/remaining_specs.py   # accumulator/multiplier/etc. real specs
python3 shared/utils/live_trader.py --symbol 1HZ100V --contract DIGITEVEN --n 300
```

## Method guardrails

- **Real execution only** — live demo API; no broker historical-candle backtests are used to
  *validate* a strategy. (Historical *tick streams* pulled from the same live feed are used to test
  whether a conditioning signal *exists*; if it doesn't exist in the real sequence, no live execution
  can manufacture one. This is the most powerful possible test of these hypotheses.)
- **Minimum samples** — distribution tests on 5,000 ticks/market; conditional tests on thousands of
  conditioned events; live confirmation batches of 300+.
- **N / N+1 / N+2** — settlement mechanics mapped empirically from each contract's own
  `entry_tick_time` / `exit_tick_time` (see Phase 1 report). Recorded for every live trade.
- **Secrets** — API token read from `DERIV_TOKEN` env var; never written to the repo.
