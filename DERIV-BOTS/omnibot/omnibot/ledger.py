"""Trade ledger — JSONL on disk + in-memory aggregates for the dashboard."""
from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from datetime import datetime, timezone


class Ledger:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self.recent: deque = deque(maxlen=200)
        self.totals = {"trades": 0, "wins": 0, "losses": 0, "staked": 0.0, "pnl": 0.0}
        self.by_strategy: dict[str, dict] = {}
        self._load()

    def _load(self):
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path) as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    if rec.get("event") == "close":
                        self._aggregate(rec)
                        self.recent.append(rec)
        except Exception:
            pass

    def _aggregate(self, rec: dict):
        t = self.totals
        t["trades"] += 1
        t["staked"] += rec.get("stake", 0.0)
        t["pnl"] += rec.get("profit", 0.0)
        if rec.get("profit", 0.0) >= 0:
            t["wins"] += 1
        else:
            t["losses"] += 1
        s = self.by_strategy.setdefault(
            rec.get("strategy", "?"), {"trades": 0, "wins": 0, "pnl": 0.0, "staked": 0.0}
        )
        s["trades"] += 1
        s["staked"] += rec.get("stake", 0.0)
        s["pnl"] += rec.get("profit", 0.0)
        if rec.get("profit", 0.0) >= 0:
            s["wins"] += 1

    def write(self, event: str, **fields):
        rec = {"event": event, "ts": time.time(), "utc": datetime.now(timezone.utc).isoformat(), **fields}
        with self._lock:
            try:
                with open(self.path, "a") as f:
                    f.write(json.dumps(rec) + "\n")
            except Exception:
                pass
            if event == "close":
                self._aggregate(rec)
                self.recent.append(rec)
        return rec

    def snapshot(self) -> dict:
        t = dict(self.totals)
        t["win_rate"] = round(t["wins"] / t["trades"], 4) if t["trades"] else None
        t["roi_on_turnover"] = round(t["pnl"] / t["staked"], 4) if t["staked"] else None
        return {
            "totals": t,
            "by_strategy": self.by_strategy,
            "recent": list(self.recent)[-30:],
        }
