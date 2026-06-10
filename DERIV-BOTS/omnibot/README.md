# Fable OmniBot — Deriv watcher/executor (digits + accumulators + rise/fall)

A comprehensive Deriv trading bot for your **demo** account, built to run 24/7
on Render's free tier. It watches the synthetic-index algo continuously,
executes every major contract family, and — this is the core design — contains
a statistical **sentinel** that is armed to exploit any RNG deviation the
moment one ever appears.

**Live-validated 2026-06-10** on demo `VRTC10502381`: authorized, streamed
ticks on 3 symbols for 35+ minutes, executed and settled real demo contracts
of all types (DIGITOVER, ACCU with active take-profit sell, CALL/PUT),
auto-reconnect and risk caps verified. See `SESSION-REPORT.md` at repo root.

---

## The honest part (read once, it matters)

This repo proved twice, independently (`FINDINGS.md`, `FINDINGS_FABLE5.md`):
on Deriv synthetics **every contract is negative-EV by construction** —
house edge 1.6–16.7% measured live, RNG clean (chi-square, Markov, drift,
Ljung-Box all pass on 850k+ ticks). No bot can flip that sign, including this
one. What this bot IS:

- **An execution machine** that stays connected, sized, capped and logged —
  the infrastructure you need on demo while you learn and experiment.
- **A watcher**: the `sentinel` strategy re-runs the statistical battery on a
  rolling window against LIVE payout quotes, forever. If Deriv's generator
  ever drifts beyond the house edge, the sentinel's Wilson-99.9% lower bound
  clears break-even and it fires with quarter-Kelly sizing within ~20 seconds.
  On a fair RNG it stays silent — its silence on the dashboard is the proof.
- **A discipline harness**: hard daily loss cap, profit lock, stake caps,
  loss-streak cooldowns, concurrency caps. The flow strategies exist to keep
  the pipeline hot and generate calibration data, with stakes that cannot hurt.

It refuses to run on a real-money token unless you explicitly set
`ALLOW_REAL=true`. Don't.

## Architecture

```
app.py                FastAPI + lifespan-managed bot
omnibot/
├── deriv.py          resilient WS client: reconnect w/ backoff, re-subscribe,
│                     req-id correlation, 25s keepalive
├── bot.py            orchestrator: auth, safety, tick routing, strategy guard
├── config.py         all settings via env vars
├── risk.py           RiskGuardian: daily caps, cooldowns, concurrency
├── edge.py           rolling battery: chi², parity, over/under, drift,
│                     Wilson bounds vs live payouts
├── strategies.py     digits_flow / sentinel / accumulators / rise_fall
├── executor.py       proposal→buy→track→settle, ACCU profit-target sells
└── ledger.py         JSONL ledger + live aggregates
```

## Endpoints

| Route | Purpose |
|---|---|
| `/` | live dashboard (auto-refresh) |
| `/health` | health probe — set as Render health check |
| `/wake` | **cron keep-alive target** (same payload) |
| `/status` | full bot state JSON |
| `/edge` | live RNG battery per symbol |
| `/trades` | ledger: totals, per-strategy, recent trades |
| `POST /control` | `{"action":"pause"|"resume"}` with `X-Control-Key` header |

## Deploy on Render (free tier)

1. Push this repo to GitHub (already done if you're reading this there).
2. Render → New → Blueprint → point at the repo. `render.yaml` lives in
   `DERIV-BOTS/omnibot/` — or create a Web Service manually:
   - Root directory: `DERIV-BOTS/omnibot`
   - Build: `pip install -r requirements.txt`
   - Start: `uvicorn app:app --host 0.0.0.0 --port $PORT`
   - Health check path: `/health`
3. Environment variables (dashboard → Environment):
   - `DERIV_TOKEN` — your **demo** API token (never commit it)
   - `MODE` — `ACTIVE` (all strategies) | `SENTINEL` (watch only, trade only
     on detected edge) | `PAPER` (no real orders)
   - sizing/caps: `BASE_STAKE=1`, `MAX_STAKE=5`, `DAILY_LOSS_LIMIT=50`,
     `DAILY_PROFIT_LOCK=100`, `MAX_CONCURRENT=3`
   - pacing: `DIGITS_INTERVAL=90`, `ACCUM_INTERVAL=300`, `RF_INTERVAL=120`
   - symbols: `DIGIT_SYMBOLS=R_100,1HZ100V`, `ACCUM_SYMBOLS=R_75,R_100`,
     `RF_SYMBOLS=R_100,1HZ100V`

## Keep-alive (Render free sleeps after ~15 min idle)

Use any free cron service to ping `/wake` every 5–10 minutes:

- **cron-job.org**: new cronjob → URL `https://YOUR-APP.onrender.com/wake`
  → every 5 minutes.
- **UptimeRobot**: HTTP(s) monitor, 5-minute interval, same URL — you get
  uptime alerting for free as a bonus.
- **GitHub Actions** (if you prefer keeping it in-repo):

```yaml
# .github/workflows/keepalive.yml
on:
  schedule: [{cron: "*/10 * * * *"}]
jobs:
  ping:
    runs-on: ubuntu-latest
    steps:
      - run: curl -fsS https://YOUR-APP.onrender.com/wake
```

Note Render free tier also has a monthly hour budget; a single always-awake
free service fits inside it. The WS client reconnects automatically after any
cold start, so even a missed ping only costs a ~60s gap.

## Run locally

```bash
cd DERIV-BOTS/omnibot
pip install -r requirements.txt
DERIV_TOKEN=your_demo_token uvicorn app:app --port 8400
# open http://127.0.0.1:8400/
```

## Strategy notes

- **digits_flow** — paced flat stakes on the lowest-house-edge contract
  (DIGITOVER 1 ≈ 1.6% edge measured live). Exists for execution readiness and
  live calibration; expected long-run ROI ≈ −1.6% of turnover, capped hard.
- **sentinel** — the reason this bot exists. Continuously prices 40+ digit
  contracts against live payouts; fires only on a Wilson-99.9% violation.
- **accumulators** — enters ACCU (3% growth default), sells at a profit
  target = stake×((1.03)^18−1) ≈ +70%; daily cap; busts are logged honestly.
- **rise_fall** — regime detector (lag-1 autocorr z-score) decides momentum
  vs fade vs heartbeat; on a fair walk it reports "fair" and the dashboard
  shows exactly that.

If you want a "100% uptime watcher that never stakes a cent", run
`MODE=SENTINEL` — it only ever trades when the math says the edge is real.
