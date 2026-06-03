"""
lattice.engine — the full 6-layer pipeline, one tick at a time.

    tick -> [L3] regression predicts next price, maps to cluster, reg_conf gate
         -> [L1] mini-cluster engine: non-overlap zone revisit? cluster_conf gate
         -> [L2] entropy gate + distribution signal strengths
         -> [L4] convergence table -> contract set clearing 0.99 (+ dominance)
         -> [L5] adaptive Kelly sizing (EV<=0 -> 0)  -> orders
         (settlement at lag-2 by the caller) -> [L6] log + drift + calibration

Two belief modes make the honesty explicit:
  * belief="honest"    : p_win = true uniform prob -> EV<=0 -> stake 0 -> no trades
  * belief="framework" : p_win = the system's own convergence score (i.e. it
                         *trusts* its signal). This lets the backtest actually
                         place the friend's trades and measure realized win rate
                         vs what the system believed.

Settlement always uses the REAL future digit, so realized outcomes are ground
truth regardless of belief.
"""
from collections import deque

from . import common
from .layer1_clusters import MiniClusterEngine
from .layer2_distribution import distribution, entropy_gate_ok, signal_strengths
from .layer3_regression import AdaptiveRegressor
from . import layer4_gametheory as L4
from . import layer5_risk as L5
from .layer6_feedback import Feedback


class LatticeEngine:
    def __init__(self, symbol, places, payouts, belief="framework",
                 bankroll=10.0, reg_conf_gate=0.60, min_stake=0.35,
                 require_entropy_gate=True, conv_threshold=0.99,
                 execution_mode="rolling", roll_window=10, framework_pbelief=0.97,
                 disable_drift=False):
        self.symbol = symbol
        self.places = places
        self.payouts = payouts
        self.belief = belief
        self.framework_pbelief = framework_pbelief
        self.disable_drift = disable_drift
        self.bankroll = bankroll
        self.peak = bankroll
        self.reg_conf_gate = reg_conf_gate
        self.min_stake = min_stake
        self.require_entropy_gate = require_entropy_gate
        self.conv_threshold = conv_threshold
        self.execution_mode = execution_mode      # "rolling" | "revisit"
        self.roll_window = roll_window
        self.scale = 10 ** places
        self.recent = deque(maxlen=roll_window)
        self.L1 = MiniClusterEngine(places)
        self.L3 = AdaptiveRegressor()
        self.L6 = Feedback()
        self.gated = {  # diagnostics: why ticks were skipped
            "reg_conf": 0, "no_strike": 0, "entropy": 0, "no_converge": 0,
            "ev_or_size": 0, "drift_halt": 0, "executed": 0,
        }

    def _digit(self, price):
        return int(round(price * self.scale)) % 10

    def on_tick(self, price, epoch):
        # ---- Layer 3 -----------------------------------------------------
        pred = self.L3.predict()
        reg_conf = self.L3.confidence()
        self.L3.update(price, pred)        # walk-forward shift happens here

        # ---- Layer 1 -----------------------------------------------------
        strike = self.L1.feed(price, epoch,
                              check_revisit=(self.execution_mode == "revisit"))
        if self.execution_mode == "rolling":
            self.recent.append(self._digit(price))

        if reg_conf < self.reg_conf_gate:
            self.gated["reg_conf"] += 1
            return None

        if self.execution_mode == "rolling":
            if len(self.recent) < self.roll_window:
                self.gated["no_strike"] += 1
                return None
            counts = [0] * 10
            for dd in self.recent:
                counts[dd] += 1
            untapped = tuple(d for d in range(10) if counts[d] == 0)
            strike = {"counts": tuple(counts), "untapped": untapped,
                      "confidence": (sum(1 for c in counts if c == 0) / 10.0) * 100.0,
                      "zone": None}
        if strike is None:
            self.gated["no_strike"] += 1
            return None

        # ---- Layer 2 -----------------------------------------------------
        dist = distribution(strike["counts"])
        if self.require_entropy_gate and not entropy_gate_ok(dist):
            self.gated["entropy"] += 1
            return None
        sigs, match_target = signal_strengths(dist)
        if not sigs:
            self.gated["no_converge"] += 1
            return None

        # ---- Layer 4 -----------------------------------------------------
        cluster_conf01 = strike["confidence"] / 100.0
        chosen, scores = L4.select(sigs, cluster_conf01, reg_conf,
                                   self.payouts, self.symbol,
                                   threshold=self.conv_threshold)
        if not chosen:
            self.gated["no_converge"] += 1
            return None

        # ---- Layer 5 -----------------------------------------------------
        dd = max(0.0, (self.peak - self.bankroll) / self.peak) if self.peak > 0 else 0.0
        drift, drift_mult = self.L6.drift_state()
        if self.disable_drift:
            drift, drift_mult = "ok", 1.0
        if drift == "halt":
            self.gated["drift_halt"] += 1
            return None
        orders = []
        for (kind, barrier, conv) in chosen:
            payout = common.payout_lookup(self.payouts, self.symbol, kind, barrier)
            if payout is None:
                payout = (1.0 - 0.0) / max(common.true_prob(kind, barrier), 1e-9)
            # framework TRUSTS its signal (claims ~99% exactness); honest mode
            # uses the uniform truth, which makes Kelly size exactly 0.
            p_belief = self.framework_pbelief if self.belief == "framework" else common.true_prob(kind, barrier)
            p_belief = min(p_belief, 0.999)
            s, reason = L5.stake(self.bankroll, p_belief, payout, conv,
                                 cluster_conf01, dd, kind, barrier, self.min_stake,
                                 conv_floor=self.conv_threshold)
            s *= drift_mult
            if s <= 0:
                continue
            orders.append({
                "kind": kind, "barrier": barrier, "stake": round(s, 2),
                "payout": payout, "convergence": conv, "believed_p": p_belief,
                "true_p": common.true_prob(kind, barrier),
                "zone": strike["zone"], "untapped": strike["untapped"],
                "reason": reason,
            })
        if not orders:
            self.gated["ev_or_size"] += 1
            return None
        if strike.get("zone") is not None:
            self.L1.mark_executed(strike["zone"])     # first-strike rule (revisit mode)
        self.gated["executed"] += 1
        return {"orders": orders, "strike": strike}

    def settle(self, order, settle_digit):
        won = common.resolves_win(order["kind"], order["barrier"], settle_digit)
        pnl = order["stake"] * (order["payout"] - 1.0) if won else -order["stake"]
        self.bankroll += pnl
        self.peak = max(self.peak, self.bankroll)
        fill = (settle_digit in order["untapped"]) if order["untapped"] else None
        self.L6.record({
            "symbol": self.symbol, "kind": order["kind"], "barrier": order["barrier"],
            "stake": order["stake"], "payout": order["payout"],
            "convergence": order["convergence"], "believed_p": order["believed_p"],
            "true_p": order["true_p"], "settle_digit": settle_digit,
            "outcome": "win" if won else "loss", "pnl": pnl, "fill": fill,
            "bankroll": self.bankroll,
        })
        return won, pnl
