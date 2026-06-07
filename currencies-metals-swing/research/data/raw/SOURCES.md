# Data sources & provenance

All series are **daily (D1) OHLC**, bundled for reproducibility. None of this is
synthetic — it is real market history. Swing logic only needs daily bars.

| File | Instrument | Range | Origin |
|---|---|---|---|
| `EURUSD.csv`…`EURGBP.csv` | 8 FX majors/crosses | 2010–2020 (GBPUSD to 1993) | public MT5-export dataset (`ExBlueDream/ForexDataCsv`, `1440/` = D1) |
| `XAUUSD.csv` | Gold spot | 2004–2025 | public daily gold dataset (`FeziweMelvin/XAUUSD-Gold-Price`) |
| `cot/*.csv` | CFTC COT (per asset) | 1986–present | fetched once from CFTC Socrata `6dca-aqww` (Legacy, Futures-Only), cached by `cot.py` |

Live COT bias for the EA / tooling: **`https://cotapi.onrender.com/api/bias`**
(your own OpenCOT API). The historical CFTC pull reproduces your API's rule
exactly (verified: USDJPY=BUY, XAUUSD=SELL on 2026-06-02).

### Caveats you should know
- FX price ends **2020**; gold runs to **2025**. The FX/COT overlap is fine for
  backtesting (COT goes back decades), but FX results don't include 2021–2025.
- COT reports are weekly (Tuesday), released ~Friday. `cot.py` applies a **+3-day
  publish lag** and `merge_asof` so no backtest bar ever sees an unreleased report.
- To extend with fresher or higher-quality data (e.g. Dukascopy, your broker's
  exported history), drop a CSV with an `open/high/low/close` schema into
  `data/raw/` — `data.py` auto-detects comma/semicolon and column names.
