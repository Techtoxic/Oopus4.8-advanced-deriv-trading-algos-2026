"""Central configuration — everything overridable via environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except Exception:
        return default


def _i(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except Exception:
        return default


def _b(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _s(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass
class Config:
    # --- connection ---
    app_id: int = _i("DERIV_APP_ID", 1089)
    token: str = _s("DERIV_TOKEN", "")
    ws_url: str = _s("DERIV_WS_URL", "wss://ws.binaryws.com/websockets/v3?app_id={app_id}")

    # --- safety ---
    allow_real_account: bool = _b("ALLOW_REAL", False)  # refuse non-virtual unless explicitly enabled
    mode: str = _s("MODE", "ACTIVE")  # ACTIVE | SENTINEL | PAPER

    # --- bankroll / risk (all in account currency) ---
    base_stake: float = _f("BASE_STAKE", 1.0)
    max_stake: float = _f("MAX_STAKE", 5.0)
    daily_loss_limit: float = _f("DAILY_LOSS_LIMIT", 50.0)      # hard stop for the day
    daily_profit_lock: float = _f("DAILY_PROFIT_LOCK", 100.0)   # stop after this much profit
    max_concurrent: int = _i("MAX_CONCURRENT", 3)
    loss_streak_pause: int = _i("LOSS_STREAK_PAUSE", 5)          # pause strategy after N straight losses
    pause_minutes: float = _f("PAUSE_MINUTES", 30.0)

    # --- symbols ---
    digit_symbols: list = field(default_factory=lambda: _s("DIGIT_SYMBOLS", "R_100,1HZ100V").split(","))
    accum_symbols: list = field(default_factory=lambda: _s("ACCUM_SYMBOLS", "R_75,R_100").split(","))
    rf_symbols: list = field(default_factory=lambda: _s("RF_SYMBOLS", "R_100,1HZ100V").split(","))

    # --- strategy toggles ---
    enable_digits: bool = _b("ENABLE_DIGITS", True)
    enable_accum: bool = _b("ENABLE_ACCUM", True)
    enable_risefall: bool = _b("ENABLE_RISEFALL", True)
    enable_sentinel: bool = _b("ENABLE_SENTINEL", True)

    # --- digits strategy ---
    digits_trade_interval_s: float = _f("DIGITS_INTERVAL", 90.0)   # pacing between flow trades
    digits_contract: str = _s("DIGITS_CONTRACT", "DIGITOVER")      # cheapest-edge family
    digits_barrier: int = _i("DIGITS_BARRIER", 1)                  # Over 1 → p=0.8, lowest measured edge
    # sentinel promotion thresholds
    sentinel_window: int = _i("SENTINEL_WINDOW", 4000)             # rolling ticks per symbol
    sentinel_min_ticks: int = _i("SENTINEL_MIN_TICKS", 1500)
    sentinel_z: float = _f("SENTINEL_Z", 3.29)                     # ~99.9% one-sided

    # --- accumulator strategy ---
    accum_growth_rate: float = _f("ACCUM_GROWTH", 0.03)
    accum_take_profit_ticks: int = _i("ACCUM_TP_TICKS", 18)
    accum_stake: float = _f("ACCUM_STAKE", 1.0)
    accum_interval_s: float = _f("ACCUM_INTERVAL", 300.0)
    accum_max_per_day: int = _i("ACCUM_MAX_PER_DAY", 40)

    # --- rise/fall strategy ---
    rf_stake: float = _f("RF_STAKE", 1.0)
    rf_duration_ticks: int = _i("RF_DURATION_TICKS", 5)
    rf_interval_s: float = _f("RF_INTERVAL", 120.0)
    rf_regime_window: int = _i("RF_REGIME_WINDOW", 600)

    # --- ops ---
    control_key: str = _s("CONTROL_KEY", "")  # required for /control if set
    ledger_path: str = _s("LEDGER_PATH", "/tmp/omnibot_ledger.jsonl")
    log_level: str = _s("LOG_LEVEL", "INFO")


CFG = Config()
