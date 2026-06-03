"""
lattice.layer1_clusters — Non-overlapping mini-cluster architecture & strike zones.

Faithful operationalization of the friend's Layer 1:

  * A 1-minute candle (60 ticks on a 1s feed) is a CLUSTER, split into 6
    MINI-CLUSTERS of `ticks_per_mini` (default 10) consecutive ticks.
  * Each mini-cluster has a price range [low, high] and a set of last-digit
    values its ticks actually printed -> tapped; the rest are UNTAPPED.
  * A mini-cluster is NON-OVERLAPPING if its [low,high] does not intersect the
    previous mini-cluster's [low,high] (clean, isolated statistical zone).
  * Finalized non-overlapping zones with untapped digits are stored. A REVISIT
    occurs when a later tick re-enters such a zone -> the friend's "strike zone".
  * FIRST-STRIKE RULE: at most one execution per zone visit; entropy/contam.
    degrade with each subsequent in-zone tick.
  * cluster_confidence is the friend's weighted composite, normalized to 0..100.

This module is the *production* signal generator. Whether its signals carry any
predictive edge is the job of validation/ — this layer only structures the data.
"""
from collections import deque
from dataclasses import dataclass, field
import numpy as np


@dataclass
class MiniCluster:
    idx: int
    low: float
    high: float
    digits: tuple                  # last-digit values printed (sorted unique)
    counts: tuple                  # length-10 count vector
    start_epoch: int
    end_epoch: int
    non_overlap: bool
    visits: int = 1
    fills_seen: int = 0            # untapped digits later filled (for fill-rate)
    untapped_at_finalize: int = 0
    executed_visit: int = -1       # last visit index on which we executed

    @property
    def untapped(self):
        return tuple(d for d in range(10) if self.counts[d] == 0)

    @property
    def untapped_ratio(self):
        return sum(1 for c in self.counts if c == 0) / 10.0

    def entropy(self):
        tot = sum(self.counts)
        if tot == 0:
            return 0.0
        ps = [c / tot for c in self.counts if c > 0]
        return float(-sum(p * np.log2(p) for p in ps))


class MiniClusterEngine:
    def __init__(self, places, ticks_per_mini=10, minis_per_cluster=6,
                 conf_threshold=65.0, age_decay_per_min=5.0, age_floor=50.0):
        self.places = places
        self.scale = 10 ** places
        self.tpm = ticks_per_mini
        self.mpc = minis_per_cluster
        self.conf_threshold = conf_threshold
        self.age_decay = age_decay_per_min
        self.age_floor = age_floor
        # streaming state
        self._buf_p = []
        self._buf_e = []
        self._buf_d = []
        self._idx = 0
        self.prev = None               # previous finalized mini-cluster
        self.zones = []                # finalized non-overlapping zones (revisitable)
        self._recent_ranges = deque(maxlen=30)  # for non_overlap_clarity scaling

    # ---- digit helper ---------------------------------------------------- #
    def _digit(self, price):
        return int(round(price * self.scale)) % 10

    # ---- main feed ------------------------------------------------------- #
    def feed(self, price, epoch, check_revisit=True):
        """
        Feed one tick. Returns a dict describing any STRIKE opportunity at this
        tick (revisit of a non-overlapping untapped zone) or None. Set
        check_revisit=False to only maintain block/zone bookkeeping (used by the
        engine's 'rolling' execution mode, which strikes on the forming cluster).
        """
        d = self._digit(price)
        self._buf_p.append(price); self._buf_e.append(epoch); self._buf_d.append(d)

        strike = self._check_revisit(price, epoch) if check_revisit else None

        if len(self._buf_p) >= self.tpm:
            self._finalize_block()
        return strike

    def _finalize_block(self):
        lo = min(self._buf_p); hi = max(self._buf_p)
        counts = [0] * 10
        for dd in self._buf_d:
            counts[dd] += 1
        non_overlap = True
        if self.prev is not None:
            non_overlap = (lo > self.prev.high) or (hi < self.prev.low)
        mc = MiniCluster(idx=self._idx, low=lo, high=hi,
                         digits=tuple(sorted(set(self._buf_d))),
                         counts=tuple(counts),
                         start_epoch=self._buf_e[0], end_epoch=self._buf_e[-1],
                         non_overlap=non_overlap)
        mc.untapped_at_finalize = len(mc.untapped)
        self._recent_ranges.append(hi - lo)
        self.prev = mc
        self._idx += 1
        # only non-overlapping zones with untapped digits are revisitable strike zones
        if non_overlap and mc.untapped_ratio > 0.0:
            self.zones.append(mc)
        self._buf_p, self._buf_e, self._buf_d = [], [], []

    def _typical_range(self):
        if not self._recent_ranges:
            return 1.0 / self.scale
        return max(np.median(self._recent_ranges), 1.0 / self.scale)

    def _check_revisit(self, price, epoch):
        """If price re-enters a stored zone that still has untapped digits and
        hasn't been struck this visit, return a strike descriptor."""
        for z in self.zones[-200:]:                      # bound the scan
            if z.low <= price <= z.high and z.untapped_ratio > 0.0:
                # new visit bookkeeping: a visit begins when price enters from outside
                conf = self.cluster_confidence(z, epoch)
                if conf < self.conf_threshold:
                    return None
                if z.executed_visit == z.visits:
                    return None                          # first-strike already used
                return {
                    "zone_idx": z.idx, "confidence": conf,
                    "untapped": z.untapped, "counts": z.counts,
                    "low": z.low, "high": z.high, "entropy": z.entropy(),
                    "zone": z,
                }
        return None

    def mark_executed(self, zone):
        zone.executed_visit = zone.visits

    # ---- confidence ------------------------------------------------------ #
    def cluster_confidence(self, z, now_epoch):
        untapped_ratio = z.untapped_ratio
        clarity = 0.0
        if self.prev is not None:
            gap = max(z.low - self.prev.high, self.prev.low - z.high, 0.0)
            clarity = min(gap / (self._typical_range() + 1e-12), 1.0)
        age_min = max(now_epoch - z.end_epoch, 0) / 60.0
        if age_min <= 5.0:
            age_weight = 1.0
        else:
            age_weight = max(self.age_floor / 100.0,
                             1.0 - self.age_decay / 100.0 * (age_min - 5.0))
        hist_fill = (z.fills_seen / z.untapped_at_finalize) if z.untapped_at_finalize else 0.9
        score = (untapped_ratio * 0.40 + clarity * 0.25 +
                 age_weight * 0.20 + hist_fill * 0.15) * 100.0
        return float(score)
