"""
cot.py — Commitments of Traders client.

Two data paths:
  * HISTORICAL  : CFTC Socrata 'Legacy - Futures Only' (6dca-aqww), weekly,
                  back to 1986. Used to align a COT bias to historical price for
                  honest backtesting. Cached under data/cot/.
  * LIVE        : the user's own API (https://cotapi.onrender.com/api/bias),
                  used by the EA / live tooling for the current week's bias.

Two bias rules, both faithful to the user's definition and testable:
  * 'user'   — exactly the rule in the user's API:
               BUY  = base spec/comm net > 0 AND quote net < 0
               SELL = base net < 0 AND quote net > 0   else UNCERTAIN
               (currencies -> noncommercial net; metals base -> commercial net)
  * 'index'  — the classic COT index (Williams): where each leg's *speculator*
               net sits within its trailing N-week range; >hi = bullish,
               <lo = bearish. Often more predictive than raw level.

Look-ahead guard: a weekly report dated Tuesday T is not public until ~Friday.
We attach a publish lag (default +3 days) and align with merge_asof so a daily
bar only ever sees a report that was already released.
"""

from __future__ import annotations
import os
import time
import requests
import numpy as np
import pandas as pd

CACHE = os.path.join(os.path.dirname(__file__), "data", "cot")
os.makedirs(CACHE, exist_ok=True)

SOCRATA = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"
LIVE_API = "https://cotapi.onrender.com"
UA = {"User-Agent": "capy-cot-research"}

# asset -> CFTC contract_market_code  (from the user's symbols.py)
CODES = {
    "GOLD": "088691", "SILVER": "084691", "AUD": "232741", "GBP": "096742",
    "CAD": "090741", "EUR": "099741", "JPY": "097741", "CHF": "092741",
    "DXY": "098662", "NZD": "112741", "MXN": "095741", "BRL": "102741",
}

# pair -> (base_asset, base_field), (quote_asset, quote_field)  [user's BIAS_PAIRS]
PAIR_DEF = {
    "EURUSD": (("EUR", "noncomm_net"), ("DXY", "noncomm_net")),
    "GBPUSD": (("GBP", "noncomm_net"), ("DXY", "noncomm_net")),
    "AUDUSD": (("AUD", "noncomm_net"), ("DXY", "noncomm_net")),
    "NZDUSD": (("NZD", "noncomm_net"), ("DXY", "noncomm_net")),
    "USDCAD": (("DXY", "noncomm_net"), ("CAD", "noncomm_net")),
    "USDCHF": (("DXY", "noncomm_net"), ("CHF", "noncomm_net")),
    "USDJPY": (("DXY", "noncomm_net"), ("JPY", "noncomm_net")),
    "EURJPY": (("EUR", "noncomm_net"), ("JPY", "noncomm_net")),
    "GBPJPY": (("GBP", "noncomm_net"), ("JPY", "noncomm_net")),
    "EURGBP": (("EUR", "noncomm_net"), ("GBP", "noncomm_net")),
    "XAUUSD": (("GOLD", "comm_net"), ("DXY", "noncomm_net")),
    "XAGUSD": (("SILVER", "comm_net"), ("DXY", "noncomm_net")),
}

PUBLISH_LAG_DAYS = 3   # Tuesday report -> Friday release


def fetch_cftc(asset: str, refresh: bool = False) -> pd.DataFrame:
    """Weekly COT history for one asset, cached. Columns: date, comm_net,
    noncomm_net, open_interest, comm_long/short, noncomm_long/short."""
    code = CODES[asset]
    path = os.path.join(CACHE, f"{asset}.csv")
    if os.path.exists(path) and not refresh:
        df = pd.read_csv(path, parse_dates=["date"])
        return df
    sel = ("report_date_as_yyyy_mm_dd,comm_positions_long_all,comm_positions_short_all,"
           "noncomm_positions_long_all,noncomm_positions_short_all,open_interest_all")
    rows = []
    offset = 0
    while True:
        r = requests.get(SOCRATA, headers=UA, timeout=40, params={
            "cftc_contract_market_code": code, "$select": sel,
            "$order": "report_date_as_yyyy_mm_dd ASC", "$limit": 50000, "$offset": offset,
        })
        chunk = r.json()
        if not chunk:
            break
        rows.extend(chunk)
        if len(chunk) < 50000:
            break
        offset += 50000
        time.sleep(0.2)
    df = pd.DataFrame(rows)
    for c in df.columns:
        if c != "report_date_as_yyyy_mm_dd":
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df["date"] = pd.to_datetime(df["report_date_as_yyyy_mm_dd"]).dt.tz_localize(None)
    df["comm_net"] = df["comm_positions_long_all"] - df["comm_positions_short_all"]
    df["noncomm_net"] = df["noncomm_positions_long_all"] - df["noncomm_positions_short_all"]
    df = df.rename(columns={"open_interest_all": "open_interest"})
    out = df[["date", "comm_net", "noncomm_net", "open_interest",
              "comm_positions_long_all", "comm_positions_short_all",
              "noncomm_positions_long_all", "noncomm_positions_short_all"]].sort_values("date")
    out.to_csv(path, index=False)
    return out


def _net_series(asset: str, field: str) -> pd.Series:
    df = fetch_cftc(asset)
    s = df.set_index("date")[field]
    return s[~s.index.duplicated(keep="last")]


def _sign(x):
    return np.where(x > 0, 1, np.where(x < 0, -1, 0))


def pair_bias_history(pair: str, rule: str = "user", index_window: int = 26,
                      hi: float = 0.65, lo: float = 0.35) -> pd.Series:
    """Weekly bias Series in {+1 buy, -1 sell, 0 uncertain}, indexed by the
    report date (Tuesday). Apply align_daily() before using against price."""
    (bsym, bfield), (qsym, qfield) = PAIR_DEF[pair]
    base = _net_series(bsym, bfield)
    quote = _net_series(qsym, qfield)
    df = pd.concat({"base": base, "quote": quote}, axis=1, sort=True).dropna()

    if rule == "user":
        b, q = _sign(df["base"].to_numpy()), _sign(df["quote"].to_numpy())
        bias = np.where((b > 0) & (q < 0), 1, np.where((b < 0) & (q > 0), -1, 0))
    elif rule == "index":
        def cot_index(s):
            mn = s.rolling(index_window).min()
            mx = s.rolling(index_window).max()
            return (s - mn) / (mx - mn).replace(0, np.nan)
        bi, qi = cot_index(df["base"]), cot_index(df["quote"])
        b = np.where(bi > hi, 1, np.where(bi < lo, -1, 0))
        q = np.where(qi > hi, 1, np.where(qi < lo, -1, 0))
        bias = np.where((b > 0) & (q < 0), 1, np.where((b < 0) & (q > 0), -1, 0))
    else:
        raise ValueError(rule)
    return pd.Series(bias, index=df.index, name=f"{pair}_bias")


def align_daily(weekly_bias: pd.Series, daily_index: pd.DatetimeIndex,
                lag_days: int = PUBLISH_LAG_DAYS) -> pd.Series:
    """Forward-fill a weekly bias onto a daily index, honouring publish lag so
    no future report is ever visible to a given day."""
    wk = weekly_bias.copy()
    pub = pd.DataFrame({"pub": wk.index + pd.Timedelta(days=lag_days), "bias": wk.values}).sort_values("pub")
    daily = pd.DataFrame({"date": pd.DatetimeIndex(daily_index)}).sort_values("date")
    merged = pd.merge_asof(daily, pub, left_on="date", right_on="pub", direction="backward")
    return pd.Series(merged["bias"].fillna(0).to_numpy(), index=daily_index, name="cot_bias")


def _cot_index_series(asset: str, field: str, window: int = 26) -> pd.Series:
    s = _net_series(asset, field)
    mn = s.rolling(window).min(); mx = s.rolling(window).max()
    return ((s - mn) / (mx - mn).replace(0, np.nan)).clip(0, 1)


def pair_index_features(pair: str, daily_index: pd.DatetimeIndex,
                        window: int = 26) -> pd.DataFrame:
    """Daily, look-ahead-safe COT features for a pair: base/quote speculator COT
    index (0..1) and the combined index bias (+1/-1/0)."""
    (bsym, bfield), (qsym, qfield) = PAIR_DEF[pair]
    base_idx = _cot_index_series(bsym, bfield, window)
    quote_idx = _cot_index_series(qsym, qfield, window)
    bias = pair_bias_history(pair, "index", index_window=window)
    out = pd.DataFrame({
        "cot_base_idx": align_daily(base_idx, daily_index),
        "cot_quote_idx": align_daily(quote_idx, daily_index),
        "cot_bias": align_daily(bias, daily_index),
    }, index=daily_index)
    return out


def live_bias() -> dict:
    """Current week's bias per pair from the user's API: {pair: 'BUY'|'SELL'|'UNCERTAIN'}."""
    r = requests.get(f"{LIVE_API}/api/bias", headers=UA, timeout=30)
    d = r.json()
    out = {}
    for grp in ("currencies", "metals"):
        for e in d.get(grp, []):
            out[e["pair"]] = e["bias"]
    out["_as_of"] = d.get("as_of")
    return out


if __name__ == "__main__":
    for pair in ["XAUUSD", "EURUSD", "GBPUSD", "USDJPY"]:
        wb = pair_bias_history(pair, "user")
        print(f"{pair}: weekly {wb.index.min().date()}->{wb.index.max().date()} "
              f"n={len(wb)} dist={pd.Series(wb).value_counts().to_dict()}")
    print("LIVE:", {k: v for k, v in live_bias().items() if k in ('XAUUSD', 'EURUSD', 'USDJPY', '_as_of')})
