# DIAGNOSIS — what's wrong with the legacy bots, measured not guessed

Scope examined: **97 custom Expert Advisors**, ~57 custom indicators, the COT API
(backend + frontend), and the Deriv research repo. Counts below come from
scanning the actual `.mq5` source in `MQL5/Experts/`, not impressions.

---

## 1. The headline problem: the bots are *static*, the market is not

| Symptom | Evidence | Why it fails |
|---|---|---|
| **Hardcoded stops/targets** | Only **14 / 97** EAs reference ATR at all. The rest hardcode pips/points: `StopLoss=50 / TakeProfit=100`, `StopLoss=800 / TakeProfit=3000`, `InpStopLossPoints=1000`, `StopLoss_Pips=150`, … | A 50-pip stop is huge on EURUSD in a quiet session and tiny on XAUUSD in a news spike. A fixed distance is a different *trade* in every regime. Stops, targets, trailing and **size** must scale with live volatility (ATR) — or the strategy silently changes character as volatility changes. |
| **Martingale / grid / recovery** | Present in **≥7** EAs: `MartingalePulse`, `HFT+BIASNEW`, `HFT+SEMA`, `quantum refined`, `scalping robot`, `hft scalper`, `hft profit per position`. | This is the exact negative-EV trap your own Deriv `FINDINGS.md` already proved: win 90%+ of days, then hand the account back in one streak. Multiplying size after losses turns a small negative edge into a guaranteed blow-up. **Removed entirely** in the new EA. |
| **Scalping orientation** | A large share are `HFT*`, `*scalper*`, M1/M5 logic. | You asked for **swing**. Scalping fights spread/commission on every trade and needs infrastructure (latency, fills) retail can't win on. Swing amortises costs over multi-day moves. |
| **COT never actually wired in** | `WebRequest` appears in **0 / 97** EAs. | Your edge thesis is "trend + COT bias", but no bot ever fetched your COT API. The "bias" inputs in `HFT WITH BIAS` etc. were manual constants. The new EA calls `cotapi.onrender.com/api/bias` directly. |
| **Dead stubs** | `KISSEA.mq5`, `SD_EA_v1.00.mq5`, `keen.mq5`, `Advisors/gpt.mq5` are **0 lines**. | Incomplete experiments left in the tree — noise that makes the project look bigger than the working surface. |

---

## 2. Your *best* work is good — it's just not automated

The four files you uploaded most recently are **indicators**, and they're
genuinely well-built and **non-repainting** (they redraw only on a closed bar):

- **`ZULU_Advanced_SMC` V4** — BOS / CHoCH / order blocks / FVG / EQH-EQL /
  premium-discount with a multi-timeframe dashboard. A serious SMC analysis tool.
- **`SelfAwareTrendSystem_HTF`** — an *adaptive SuperTrend* with a trend-quality
  dashboard. This is exactly the adaptive-trend idea the new EA's regime engine
  is built on.
- **`MSnR-GAPS`** — multi-TF A-top/V-bottom swing levels + hidden-engulfing gap
  S&R. Clean swing structure, redraws only on closed bars.
- **`LVRB`** — low-volatility range → breakout, confirmed on bar close.

You trade the *confluence* of these by eye. Nothing in the 97 EAs automates that
confluence with disciplined risk. **That is the gap the new system fills.**

> Repaint note: the new indicators commit signals only on `barstate.isconfirmed`
> (Pine) / closed bars (MQL5). Several legacy EAs evaluate on the *forming* bar,
> which can repaint signals intrabar — audit any you keep for `shift 0` decisions.

---

## 3. What "adaptive" actually means here

Every hardcoded number becomes a function of state:

| Legacy (static) | New (adaptive) |
|---|---|
| `StopLoss = 50 pips` | `stop = entry ± ATR × InpSL_ATR_Mult` |
| `TakeProfit = 100 pips` | `target = R-multiple of the live stop distance` |
| `Lots = 0.10` (fixed) | `lots = equity × risk% / stop_distance_in_money` |
| trade always | trade **only** when ADX ≥ min and ATR-percentile is in a sane band |
| direction = indicator | direction = SuperTrend **and** EMA **and** (optional) HTF **and** COT bias |
| martingale after loss | flat risk %, daily-loss circuit breaker, one position/symbol |

---

## 4. The honest part (read `research/results/REPORT.md`)

I didn't just assert the new design is better — I walk-forward tested it on real
FX (2010–2020) and gold (2004–2025) with CFTC COT back to the 1990s:

- FX-majors daily trend-following shows **no robust edge** after costs.
- The **COT-index of speculator positioning** consistently helps; your raw
  *commercial-net* rule actually hurts metals (commercials are perma-short gold).
- **Gold + COT-index** is the one configuration that survives out-of-sample.
- An ML trade filter **failed** purged walk-forward (AUC ≈ 0.49) — reported as a
  null result, not buried.

The new EA is the disciplined, adaptive vehicle to trade that thin edge and
forward-test it on demo. It is not, and I will not call it, a guaranteed winner.
