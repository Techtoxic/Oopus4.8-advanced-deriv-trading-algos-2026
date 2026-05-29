# Deriv Trading Algorithms — Research, Backtests & Honest Findings (2026)

A first‑principles, evidence‑driven investigation into whether a **statistically
profitable / positive‑expected‑value** trading bot can be built for **Deriv**,
with a target of **$7–10 net per week on a $10 account**.

This repo contains real data, real payouts, runnable bots, full backtests, and a
Monte‑Carlo risk study — not opinions. It also contains the conclusion those
artifacts force.

---

## ⭐ Bottom line (read this first)

> **On Deriv synthetic indices, no algorithm has positive expected value — and
> "guaranteed daily profit" is mathematically impossible.** The indices are
> memoryless random processes with zero drift and an explicit house edge baked
> into every payout. I verified this on **~700,000 fresh live ticks across 9
> instruments**, with **live‑quoted payouts**, honest backtests, and a
> 200k‑path Monte Carlo. Every popular strategy converges to **ROI ≈ −(house
> edge)**; every "win‑every‑day" martingale ends, with probability →1, in a total
> wipe‑out.

This is the same conclusion the prior repo reached. I did not take it on faith —
I re‑derived it from scratch, then went **much further**: all instrument
families (incl. Crash/Boom, Step, Jump), real payouts, a reusable backtest engine,
and the risk‑of‑ruin math behind the "$1/day" dream. The full reasoning, with
tables and figures, is in **[`FINDINGS.md`](FINDINGS.md)**.

I'm not selling you a fantasy. I'm giving you the proof, the tools, and an honest
map of where real (hard, non‑guaranteed) edges actually live.

---

## What's in here

```
FINDINGS.md                  ← the full writeup: the math, the evidence, the verdict
README.md                    ← you are here
requirements.txt
data/                        ← ~700k gzipped live ticks (9 instruments) + live payouts
src/
  collect.py                 ← multi‑instrument tick collector (Deriv public WS)
  common.py                  ← data loading / digit extraction helpers
  analysis/
    verify_prior.py          ← independent re‑derivation of the prior repo's stats
    payouts.py               ← live house‑edge table via the `proposal` endpoint
    battery.py               ← i.i.d.‑uniform + random‑walk test battery (all instruments)
    crashboom.py             ← Crash/Boom spike dynamics + memorylessness tests
  backtest/
    engine.py                ← event‑driven backtester (real payouts, correct lag)
    strategies.py            ← the popular strategies, honestly coded
    run_backtests.py         ← runs every strategy × stake scheme → results/
  montecarlo/
    risk_of_ruin.py          ← the "guaranteed $1/day" martingale, simulated
    plots.py                 ← figures → results/*.png
  live/
    paper_trader.py          ← live paper/demo runner (no money by default)
results/                     ← pre‑computed outputs + figures
```

## Quickstart

```bash
pip install -r requirements.txt

# 1) Reproduce the evidence (each writes to results/)
python src/analysis/payouts.py          # measured house edge per contract
python src/analysis/battery.py          # no memory, no drift, no edge
python src/analysis/crashboom.py        # Crash/Boom myth, dismantled
python src/backtest/run_backtests.py    # every strategy loses
python src/montecarlo/risk_of_ruin.py   # martingale ruin probabilities

# 2) Watch it live with NO money (settles against the real tick feed)
python src/live/paper_trader.py --symbol 1HZ100V --strategy differs --max-trades 300
python src/live/paper_trader.py --symbol 1HZ100V --strategy even --martingale --max-trades 300
```

## How to "run it on demo" honestly

`src/live/paper_trader.py` connects to the **live Deriv feed** and settles each
decision exactly as a 1‑tick contract would — with **no money at risk**. That is
the safest possible "demo backtest," and it's the one I recommend.

⚠️ **A warning you will see for yourself:** over a *short* run (a few dozen
trades) the paper‑trader can show a profit — my own 40‑trade test ended **+$0.50
at a 95% win rate.** That is *variance*, not edge. It is precisely why people
believe these systems work. Run it for thousands of trades (or read
`results/backtests.txt`) and the result converges to **ROI ≈ −(house edge)**. The
short‑run green is the bait; the long‑run red is the truth.

If you still want to place actual orders, do it **only on a Deriv demo (virtual)
account** and treat any profit as a coin‑flip streak, not a strategy.

## The one‑paragraph version of the math

A binary pays `payout × stake` on a win. Deriv sets `payout = (1−edge)/q` from the
true probability `q`. Your EV per $1 is `p_win·payout − 1`. On a memoryless RNG no
past information moves the next outcome, so `p_win = q` and `EV = −edge < 0` for
**every** strategy, on **every** contract, **always**. Bet sizing can't fix a
negative edge — Kelly literally prescribes a stake of **zero**. Full derivation,
data and figures: **[`FINDINGS.md`](FINDINGS.md)**.

---

*Built as an honest research deliverable. If you can falsify any claim here, the
code is all runnable — change it and show the passing test. That's the scientific
way to get rich, if it were possible here.*
