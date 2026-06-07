"""
data.py — load and normalise daily OHLC for FX + metals.

All loaders return a tidy DataFrame indexed by a tz-naive daily DatetimeIndex
with float columns: open, high, low, close, volume — sorted, de-duplicated,
NaN-free. Nothing here looks into the future; it is pure I/O + cleaning.

Sources currently bundled in data/raw/ (committed for reproducibility):
  * FX majors/crosses  — daily, 2010..2020 (GBPUSD back to 1993)
  * XAUUSD (gold)       — daily, 2004..2025
See data/raw/SOURCES.md for provenance.
"""

from __future__ import annotations
import os
import glob
import pandas as pd

RAW_DIR = os.path.join(os.path.dirname(__file__), "data", "raw")

# Pip size per symbol (price units of one pip). Used for spread/cost modelling
# and for reporting. JPY pairs and gold quote with 2-3 decimals.
PIP = {
    "EURUSD": 0.0001, "GBPUSD": 0.0001, "AUDUSD": 0.0001, "NZDUSD": 0.0001,
    "USDCAD": 0.0001, "USDCHF": 0.0001, "EURGBP": 0.0001,
    "USDJPY": 0.01, "EURJPY": 0.01, "GBPJPY": 0.01, "AUDJPY": 0.01,
    "XAUUSD": 0.10, "XAGUSD": 0.01,
}

# A realistic *typical* dealing spread in price units (used by the backtester
# when a per-bar spread column is absent). Deliberately conservative.
TYPICAL_SPREAD = {
    "EURUSD": 0.00008, "GBPUSD": 0.00012, "AUDUSD": 0.00010, "NZDUSD": 0.00015,
    "USDCAD": 0.00013, "USDCHF": 0.00013, "EURGBP": 0.00012,
    "USDJPY": 0.010, "EURJPY": 0.013, "GBPJPY": 0.018, "AUDJPY": 0.012,
    "XAUUSD": 0.25, "XAGUSD": 0.02,
}


def available_symbols() -> list[str]:
    return sorted(os.path.basename(f)[:-4] for f in glob.glob(os.path.join(RAW_DIR, "*.csv")))


def _read_any(path: str) -> pd.DataFrame:
    """Read a CSV whether it is comma- or semicolon-delimited."""
    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
        head = fh.readline()
    sep = ";" if head.count(";") > head.count(",") else ","
    return pd.read_csv(path, sep=sep)


def load_ohlc(symbol: str, start: str | None = None, end: str | None = None) -> pd.DataFrame:
    """Load one symbol's daily OHLC, cleaned and sorted ascending by date."""
    path = os.path.join(RAW_DIR, f"{symbol}.csv")
    if not os.path.exists(path):
        raise FileNotFoundError(f"No data file for {symbol} at {path}")
    df = _read_any(path)

    # Normalise column names.
    lower = {c: c.strip().lower() for c in df.columns}
    df = df.rename(columns=lower)

    # Identify the timestamp column.
    date_col = next((c for c in df.columns if "time" in c or "date" in c), df.columns[0])
    df["dt"] = pd.to_datetime(df[date_col], errors="coerce", utc=True)
    df = df.dropna(subset=["dt"])
    df["dt"] = df["dt"].dt.tz_convert(None)  # drop tz -> naive

    rename = {}
    for want in ("open", "high", "low", "close"):
        match = next((c for c in df.columns if c == want), None)
        if match is None:
            raise ValueError(f"{symbol}: missing '{want}' column in {list(df.columns)}")
        rename[match] = want
    vol_col = next((c for c in df.columns if "vol" in c), None)
    df = df.rename(columns=rename)

    out = pd.DataFrame({
        "open": pd.to_numeric(df["open"], errors="coerce"),
        "high": pd.to_numeric(df["high"], errors="coerce"),
        "low": pd.to_numeric(df["low"], errors="coerce"),
        "close": pd.to_numeric(df["close"], errors="coerce"),
        "volume": pd.to_numeric(df[vol_col], errors="coerce") if vol_col else 0.0,
    })
    out.index = pd.DatetimeIndex(df["dt"].values, name="date")

    out = (
        out.dropna(subset=["open", "high", "low", "close"])
           .sort_index()
    )
    # Collapse accidental duplicate timestamps, keep last.
    out = out[~out.index.duplicated(keep="last")]
    # Sanity: high>=low, prices positive.
    out = out[(out[["open", "high", "low", "close"]] > 0).all(axis=1)]
    out = out[out["high"] >= out["low"]]
    # Normalise to date granularity (these are daily bars).
    out.index = out.index.normalize()
    out = out[~out.index.duplicated(keep="last")]

    if start:
        out = out[out.index >= pd.Timestamp(start)]
    if end:
        out = out[out.index <= pd.Timestamp(end)]
    return out


def pip(symbol: str) -> float:
    return PIP.get(symbol.upper(), 0.0001)


def typical_spread(symbol: str) -> float:
    return TYPICAL_SPREAD.get(symbol.upper(), 2 * pip(symbol))


if __name__ == "__main__":
    for s in available_symbols():
        try:
            d = load_ohlc(s)
            print(f"{s:8s} rows={len(d):>6}  {d.index.min().date()} -> {d.index.max().date()}  "
                  f"last_close={d['close'].iloc[-1]:.5f}")
        except Exception as e:  # noqa
            print(f"{s:8s} ERROR {e}")
