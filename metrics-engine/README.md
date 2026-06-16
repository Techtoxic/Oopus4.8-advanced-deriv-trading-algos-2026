# metrics-engine — Sentinel V2 evaluation & monitoring

A **separate, read-only** analysis layer for `fable-thoughts/tools/sentinel_v2.py`.
It does not trade and does not modify the original bot. It exists to answer:
behaviour of **sigma vs equity**, **when/why drawdowns happen**, and **what wins vs loses**.

## Components
- `monitor.py` — opens its own public Deriv websocket, recomputes the *exact* rolling-sigma
  estimator the bot uses, and logs one row per tick (`epoch, quote, digit, sigma, sigma_bin`).
  Independent of the bot, so it captures the digit the bot's CSV omits and a continuous sigma track.
- `edge_tests.py` — runs four statistical tests on the repo's historical ticks
  (`fable-thoughts/data/*.csv.gz`): last-digit uniformity, offset-PMF per sigma bin,
  best-achievable EV per bin against the real payout grid, and an **exact replay of the bot's
  decision rule** with realised PnL + t-stats. Writes `out/edge_tests.json`.
- `analyze.py` — fuses the bot's trade log with the monitor's tick log and produces
  `out/metrics.json` + a self-contained `out/dashboard.html`: equity/sigma overlay, drawdown
  underwater plot, **EV calibration (claimed vs realised)**, sigma-direction vs outcome,
  and win/loss anatomy by contract / sigma bin / source / digit.

## Run
```bash
pip install websocket-client numpy scipy matplotlib pandas
export DERIV_TOKEN=...  DERIV_APP_ID=...

# 1. (optional) monitor sigma alongside the bot
python3 monitor.py --symbol JD100 --minutes 480 --out live/sigma_ticks.csv &

# 2. run the bot (unchanged) writing into live/
cd ../fable-thoughts/tools
python3 sentinel_v2.py --trade --stake 0.35 --ev-gate 0.01 --minutes 480 --max-loss 40 \
    --log ../../metrics-engine/live/sentinel_trades.csv

# 3. analyse anytime (re-run to refresh the dashboard)
cd ../../metrics-engine
python3 edge_tests.py
python3 analyze.py --probe-balance
open out/dashboard.html
```

See `REPORT.md` for the standing evaluation.
