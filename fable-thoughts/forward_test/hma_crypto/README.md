# Forward test: HMA crypto trend (paper only)

This is the one strategy from the September 2026 review that beat **exposure-matched random timing**
out of sample: long-only BTC/ETH on 4-hour candles (HMA 16/64 cross, RSI(14) > 52, close above
LinReg(50); exit on the HMA cross down). It is a lead, not a proven edge. The forward test checks
whether it keeps working on data nobody has seen yet.

**It never trades.** No API keys, no account connection: it reads public Binance candles and records
what the rule *would* have done.

## Setup (Windows, macOS or Linux)

```powershell
cd fable-thoughts\forward_test\hma_crypto
python -m pip install pandas numpy
python -m unittest -v test_forward.py
```

## Run it

Either keep a window open (checks every 15 minutes, acts only on closed 4-hour candles):

```powershell
python forward.py --loop
```

or schedule `python forward.py` every 4 hours (Windows Task Scheduler / cron). Missed runs are safe:
the next run catches up on every closed candle it has not processed, and re-running never
double-counts.

Files are written to `run/`:

- `state.json` — start time, open paper positions, last candle processed per symbol
- `trades.csv` — every closed paper trade with net return after 3 bp per side
- `events.jsonl` — every entry and exit signal

Keep `run/` for the whole test. Deleting it restarts the clock.

## Check progress

```powershell
python evaluate.py
```

This compares the paper result with 2,000 random-timing strategies that have the same number of
trades and the same holding lengths over the same period, and applies the frozen rule in
`prereg.json`:

- under 6 months: "too early", keep running, change nothing
- pooled timing percentile ≥ 95 and better than the random median: PASS
- below 50: FAIL
- otherwise: inconclusive, extend without changing anything

## Rules

1. **Do not edit `prereg.json`, `signals.py` or the fee.** `forward.py` refuses to continue if the
   pre-registration file changes. Any change means a new test in a new `--out` folder with a new
   start date.
2. Six months of 4-hour trend trades is only a few dozen trades per coin. A PASS means "consistent
   with an edge", not proof. A long-run edge of this size needs years to confirm.
3. If you later trade it, use **spot** (no overnight financing). CFD swaps would erase much of it.

## Where the numbers come from

`signals.py` reimplements Ziad Francis' `CoinQuant_Vs_Python_HMA_Strategy` defaults without
`pandas_ta`. On BTCUSDT 4H from 2017 to 2026 it reproduces his entry and exit signals exactly
(0 mismatches over 279 entries and 546 exits). Backtest timing percentiles versus random entries:
BTC 96th (2017-12 to 2019-02, before his sample) and 99.8th (2019-02 to 2026-05); ETH, BNB and SOL
were mixed, with SOL failing before 2021.
