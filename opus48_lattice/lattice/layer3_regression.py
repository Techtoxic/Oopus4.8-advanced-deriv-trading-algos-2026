"""
lattice.layer3_regression — Adaptive rolling-window regression.

Predicts the *price location* of the next tick (not the digit), maps it to a
mini-cluster, and reports a regression_confidence used to gate trading. Implements
the friend's walk-forward shift exactly:

    fit -> predict P_next -> actual arrives -> drop oldest, append actual ->
    re-analyze curve shape -> resize window by residual trend -> repeat.

Curve-shape adaptation picks, every step, the model family with the lowest
leave-last-out error among {linear, quadratic, cubic, mean-reversion(OU)}.

Reality check baked in for validation: a zero-drift random walk's optimal 1-step
predictor is "next = last" (persistence). test_regression_location.py compares
this engine's cluster-hit-rate against that persistence baseline; if it can't
beat persistence, the regression adds nothing. It can't, on a random walk — and
the code lets you see that rather than asserting it.
"""
from collections import deque
import numpy as np


class AdaptiveRegressor:
    def __init__(self, n0=30, n_min=20, n_max=60, rmse_span=20, reselect_every=10):
        self.n = n0
        self.n_min = n_min
        self.n_max = n_max
        self.win = deque(maxlen=self.n)
        self._ema_sq = None
        self.rmse_span = rmse_span
        self._rmse_hist = deque(maxlen=10)
        self.shape = "warmup"
        self.reselect_every = reselect_every
        self._since_select = 10 ** 9
        self._family = None

    # ---- model families -------------------------------------------------- #
    @staticmethod
    def _poly_pred(y, deg):
        x = np.arange(len(y))
        try:
            c = np.polyfit(x, y, deg)
        except Exception:
            return y[-1]
        return float(np.polyval(c, len(y)))

    @staticmethod
    def _ou_pred(y):
        # mean-reversion: next ~ y + theta*(mean - y); theta from lag-1 fit
        y = np.asarray(y)
        mu = y.mean()
        dy = np.diff(y)
        denom = np.sum((y[:-1] - mu) ** 2)
        theta = -np.sum((y[:-1] - mu) * dy) / denom if denom > 1e-12 else 0.0
        theta = float(np.clip(theta, 0.0, 1.0))
        return float(y[-1] + theta * (mu - y[-1]))

    def _select_and_predict(self, y):
        """Re-select the model family every `reselect_every` ticks (spec: 'check
        curve shape every 10 ticks'); reuse it in between for speed."""
        if len(y) < 6:
            self.shape = "linear"
            return self._poly_pred(y, 1)
        cands = {
            "linear": lambda yy: self._poly_pred(yy, 1),
            "quadratic": lambda yy: self._poly_pred(yy, 2),
            "cubic": lambda yy: self._poly_pred(yy, 3),
            "mean_revert": lambda yy: self._ou_pred(yy),
            "persistence": lambda yy: yy[-1],
        }
        if self._family is None or self._since_select >= self.reselect_every:
            errs = {name: abs(fn(y[:-1]) - y[-1]) for name, fn in cands.items()}
            self._family = min(errs, key=errs.get)
            self._since_select = 0
        self.shape = self._family
        self._since_select += 1
        return cands[self._family](y)

    # ---- main step ------------------------------------------------------- #
    def predict(self):
        if len(self.win) < 5:
            return None
        y = np.asarray(self.win, dtype=float)
        return self._select_and_predict(y)

    def update(self, actual, predicted):
        if predicted is not None:
            r2 = (predicted - actual) ** 2
            self._ema_sq = r2 if self._ema_sq is None else \
                (self._ema_sq + (r2 - self._ema_sq) * 2.0 / (self.rmse_span + 1))
        self.win.append(actual)
        self._resize()

    def rmse(self):
        return float(np.sqrt(self._ema_sq)) if self._ema_sq is not None else None

    def confidence(self):
        if len(self.win) < 5 or self._ema_sq is None:
            return 0.0
        y = np.asarray(self.win, dtype=float)
        rng = max(y.max() - y.min(), 1e-9)
        return float(max(0.0, 1.0 - self.rmse() / rng))

    def _resize(self):
        r = self.rmse()
        if r is None:
            return
        self._rmse_hist.append(r)
        if len(self._rmse_hist) >= 10:
            old = np.mean(list(self._rmse_hist)[:5])
            new = np.mean(list(self._rmse_hist)[5:])
            if old > 0 and new > old * 1.15 and self.n < self.n_max:
                self.n = min(self.n + 5, self.n_max)
            elif old > 0 and new < old * 0.85 and self.n > self.n_min:
                self.n = max(self.n - 3, self.n_min)
            if self.win.maxlen != self.n:
                self.win = deque(list(self.win)[-self.n:], maxlen=self.n)
