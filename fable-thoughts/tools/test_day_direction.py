"""test_day_direction.py -- day_direction.py must stay silent on a memoryless series and catch each
planted signature: a fixed drift (H1), a per-day direction (H2/H3) and an hour-of-day volatility (H4).
Run: python3 -m unittest test_day_direction -v"""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import day_direction as dd

T0 = 1_750_000_000 - 1_750_000_000 % 86400


def candles(r):
    """Hourly log returns -> [[epoch, open, close]] with open = previous close."""
    px = 1000 * np.exp(np.cumsum(r))
    op = np.concatenate([[1000.0], px[:-1]])
    return [[T0 + 3600 * i, float(o), float(c)] for i, (o, c) in enumerate(zip(op, px))]


def crashlike(rng, n):
    """Drift up between Poisson spikes down, zero-mean in price: the memoryless null."""
    k = rng.poisson(3.6, n)                      # spikes per hour on N=1000
    spike = np.array([rng.exponential(1e-3, j).sum() for j in k])
    return 3.6e-3 - spike + rng.normal(0, 2e-5, n)


class TestDayDirection(unittest.TestCase):
    N = 24 * 200

    def run_one(self, r):
        return dd.analyse('X', candles(r), n_perm=400)

    def test_memoryless_is_silent(self):
        rng = np.random.default_rng(1)
        res = self.run_one(crashlike(rng, self.N))
        self.assertEqual(res['complete_days'], 200)          # the first hour runs from its own open
        for k in dd.TESTS[1:]:
            self.assertGreater(res[k]['p'], 0.001, k)
        self.assertGreater(res['H1_drift']['p'], 0.001)

    def test_decided_days_are_caught(self):
        rng = np.random.default_rng(2)
        r = crashlike(rng, self.N)
        tilt = np.repeat(rng.choice([-1, 1], self.N // 24), 24) * 0.25 * r.std()   # each day leans one way
        res = self.run_one(r + tilt)
        self.assertLess(res['H2_VR']['p'], 0.01)
        self.assertGreater(res['H2_VR']['obs'], 1.5)
        self.assertLess(res['H3_half']['p'], 0.01)
        self.assertLess(res['H3_early']['p'], 0.01)
        self.assertGreater(res['P_same_sign_halves'], 0.6)

    def test_fixed_drift_and_hour_vol(self):
        rng = np.random.default_rng(3)
        z = rng.normal(0, 0.01, self.N)
        res = self.run_one(z + 0.003)
        self.assertLess(res['H1_drift']['p'], 1e-6)
        hod = np.arange(self.N) % 24
        res = self.run_one(z * np.where(hod == 14, 3.0, 1.0))
        self.assertLess(res['H4_hour_vol']['p'], 0.01)
        self.assertGreater(res['H4_hour_mean']['p'], 0.001)


    def test_reset_index_keeps_its_daily_drift(self):
        """RDBULL-like: every day restarts at 1000 and drifts up; the midnight reset must not cancel it."""
        rng = np.random.default_rng(4)
        c = []
        for d in range(120):
            r = rng.normal(0.004, 0.02, 24)
            px = 1000 * np.exp(np.cumsum(r))
            op = np.concatenate([[1000.0], px[:-1]])
            c += [[T0 + 86400 * d + 3600 * h, float(o), float(x)] for h, (o, x) in enumerate(zip(op, px))]
        res = dd.analyse('RD', c, n_perm=200)
        self.assertGreater(res['P_day_up'], 0.75)
        self.assertLess(res['H1_drift']['p'], 1e-6)
        self.assertGreater(res['H4_hour_mean']['p'], 0.001)          # no fake hour-0 effect from the reset


if __name__ == '__main__':
    unittest.main()
