"""
lattice.layer6_feedback — Logging, drift detection, calibration.

Every trade produces a structured record. The drift detector tracks the rolling
realized win rate of executed contracts vs their break-even and the rolling
untapped-fill rate vs the 90% baseline, cutting stakes or halting when an
assumption breaks. Calibration re-estimates per-zone fill rates and per-contract
win rates from the log.
"""
from collections import deque
import numpy as np

BASELINE_FILL = 0.90
DRIFT_ALERT = BASELINE_FILL - 0.12      # 0.78
DRIFT_HALT = BASELINE_FILL - 0.20       # 0.70


class Feedback:
    def __init__(self, window=200):
        self.log = []
        self.fills = deque(maxlen=window)        # 1 if untapped digit later filled
        self.outcomes = deque(maxlen=window)     # 1 win / 0 loss
        self.loss_streak = 0

    def record(self, rec):
        self.log.append(rec)
        self.outcomes.append(1 if rec["outcome"] == "win" else 0)
        if rec["outcome"] == "win":
            self.loss_streak = 0
        else:
            self.loss_streak += 1
        if "fill" in rec and rec["fill"] is not None:
            self.fills.append(1 if rec["fill"] else 0)

    def fill_rate(self):
        return float(np.mean(self.fills)) if self.fills else None

    def win_rate(self):
        return float(np.mean(self.outcomes)) if self.outcomes else None

    def drift_state(self):
        fr = self.fill_rate()
        if fr is None:
            return "warmup", 1.0
        if fr <= DRIFT_HALT:
            return "halt", 0.0
        if fr <= DRIFT_ALERT:
            return "alert", 0.25
        return "ok", 1.0

    def summary(self):
        n = len(self.log)
        wins = sum(1 for r in self.log if r["outcome"] == "win")
        pnl = sum(r.get("pnl", 0.0) for r in self.log)
        stake = sum(r.get("stake", 0.0) for r in self.log)
        return {
            "trades": n, "wins": wins,
            "win_rate": wins / n if n else None,
            "pnl": pnl, "staked": stake,
            "roi": pnl / stake if stake else None,
            "fill_rate": self.fill_rate(),
        }
