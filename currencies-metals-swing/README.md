# Currencies & Metals — Adaptive Swing System (2026)

A disciplined pivot from Deriv synthetics (which the parent repo proved are
negative-EV by construction) to **real markets with real structure**: FX majors
and metals, traded as **swings**, with **adaptive** (volatility-scaled) risk and
a **COT positioning** filter.

This is the engineering answer to "make my static, hardcoded EAs adaptive." It is
**not** a promise of profit. The research here is deliberately honest — it shows
where a thin edge plausibly lives (metals + COT) and where it does not (FX-majors
trend-following). Read `research/results/REPORT.md` before risking a cent.

```
currencies-metals-swing/
├── DIAGNOSIS.md              ← what was wrong with the 97 legacy EAs (measured)
├── mql5/
│   ├── AdaptiveSwingTrader.mq5   ← the EA: adaptive, non-repainting, COT-filtered, no martingale
│   ├── AdaptiveSwingSignal.mq5   ← chart indicator (same logic, closed-bar, non-repainting)
│   └── AdaptiveSwingTrader.set   ← sensible swing defaults
├── pine/
│   └── adaptive_swing_signal.pine ← TradingView v6 version (non-repainting)
└── research/                ← Python: honest walk-forward proof of the design
    ├── data.py  indicators.py  strategy.py  backtest.py  cot.py  ml.py  run_research.py
    ├── data/raw/            ← real daily OHLC (FX 2010–2020, gold 2004–2025) + cached COT
    └── results/             ← REPORT.md, ML_REPORT.md, equity PNGs
```

## The strategy in one paragraph

On the **closed** bar of the working timeframe (default H4), establish a trend
regime from an **adaptive SuperTrend(ATR)** confirmed by an EMA filter and an
**ADX** strength gate, and only act when the **ATR volatility percentile** is in a
sane band (skip dead and berserk markets). Enter on either a **pullback** to the
dynamic EMA (continuation) or a **Donchian breakout**, in the trend direction,
with a momentum candle. Gate every entry by the **COT bias** for that pair from
your API. Risk is fully adaptive: **stop = ATR × mult**, **target = R-multiple**,
**chandelier ATR trail**, **breakeven after +1R**, **size = % of equity ÷ stop
distance**. One position per symbol, daily-loss circuit breaker, **no martingale**.

## MQL5 EA — install & run

1. Copy `mql5/AdaptiveSwingTrader.mq5` to `MQL5/Experts/` and
   `mql5/AdaptiveSwingSignal.mq5` to `MQL5/Indicators/`, then **compile** in
   MetaEditor (F7). The EA uses only the standard `<Trade/Trade.mqh>` library.
2. **Enable the COT feed:** MetaTrader → Tools → Options → Expert Advisors →
   *Allow WebRequest for listed URL* → add `https://cotapi.onrender.com`.
   (Without this, the EA falls back per `InpCOTFallback` — default = trade without
   the COT gate.)
3. Attach to an **H4 or D1** chart of a pair in your COT universe (XAUUSD, EURUSD,
   GBPUSD, USDJPY, AUDUSD, USDCAD, NZDUSD, EURJPY, EURGBP, …). The EA auto-maps
   the broker symbol (e.g. `XAUUSD.r` → `XAUUSD`); override with `InpCOTPairOverride`.
4. **Demo first.** Run it on your Deriv MT5 demo (or any FX/metals demo) for weeks
   before considering real money. Load `AdaptiveSwingTrader.set` for the defaults.

Key inputs: `InpRiskPctPerTrade` (default 0.5%), `InpWorkingTF` (H4),
`InpUseCOT` (true), `InpEntryMode` (Either), `InpSL_ATR_Mult` (2.0), `InpTP_R`
(2.5), `InpMaxDailyLossPct` (3%). Nothing in the EA is a fixed pip distance.

## Research harness — reproduce the honesty

```bash
cd research
pip install -r requirements.txt
python run_research.py     # walk-forward backtest -> results/REPORT.md + PNGs
python ml.py               # purged walk-forward ML filter -> results/ML_REPORT.md
python cot.py              # COT client self-test (CFTC history + your live API)
```

Data is bundled (`research/data/raw/`) so it runs offline; see `SOURCES.md`.
The CFTC COT history is fetched once from the public Socrata API and cached.

## Honest expectations (the whole point)

- **No guarantee of positive EV.** Backtest profit ≠ forward profit. Liquid FX is
  close to efficient; the FX-majors portfolio here is flat-to-negative OOS.
- **The durable signal is simple:** trend regime + COT-index positioning,
  concentrated in **gold** (XAUUSD OOS: +16.9%, PF 1.51, Sharpe 0.52 on fixed
  params). One instrument is a fragile basket — treat it as a *candidate*.
- **Your raw COT rule needs upgrading for metals:** commercials are perma-short
  gold, so the commercial-net-level rule blocks the uptrend. Use the **COT-index**
  of speculator net (implemented in `cot.py`).
- **ML added nothing** out-of-sample here (AUC ≈ 0.49). Don't ship it as-is.
- The right next step is **forward demo testing**, not leverage.
