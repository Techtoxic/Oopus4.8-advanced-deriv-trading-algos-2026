# opus-thoughts/tools — run-book

All tools use the public `app_id 1089` for market data; trading requires `DERIV_TOKEN` env var
(demo-guarded; real accounts refused without explicit flags). No tokens are stored in this repo.

```bash
pip install websocket-client numpy scipy

# audit kit (read-only, re-run weekly)
python3 universe_screen.py                       # every symbol: digits? accu? step physics
python3 payout_scanner.py                        # full payout surface + Dutch LP (slow, ~15min)
python3 accumulator_scan.py R_100 1HZ100V 1HZ10V # per-tick survival vs growth
python3 tick_harvest2.py JD100:14 R_100:8        # clean deep ticks (windowed paging!)
python3 edge_tests.py JD100 R_100                # H1 adversarial + conditionals + hazards
python3 validate_sigma_curve.py                  # JD100 sigma-binned conditional validation
python3 backtest_conditional.py JD100 0.01 0.0   # replay backtest (gate, p_miss)

# the live deliverable
python3 sigma_sentinel.py --watch JD100 --minutes 60                  # watch mode
DERIV_TOKEN=... python3 sigma_sentinel.py --watch JD100 --trade \
    --stake 1 --ev-gate 0.01 --minutes 480                            # demo trade mode

# your 5-digit tool, done right
python3 multi_matches.py --symbol JD100 --stake 1 --auto --dry        # model-picked digits
python3 multi_matches.py --symbol 1HZ100V --stake 1 --digits 1,3,5,7,9 --dry
python3 coverage_optimizer.py --symbol JD100 --set 5,6,7,8,9          # cheapest replication
```

Key files produced into `../results/`: `RESULTS.md` (the session verdicts), `payout_surface.md`,
`universe_screen.md`, `sigma_edge_model.md`, `sigma_curve_validation.md`, `accumulator_rtp.md`,
`liability_sim.md`, `backtest_JD100.md`, `edge_tests_*.json`.

Gotcha that cost a re-harvest: Deriv's `ticks_history` silently clamps `count+end` paging to
the most recent ~1 day and re-serves it. Always page with explicit `start`/`end` epochs
(`tick_harvest2.py` does).
