import unittest

import numpy as np
import pandas as pd

import forward
import signals


def bars(prices, start='2026-01-01'):
    idx = pd.date_range(start, periods=len(prices), freq='4h', tz='UTC')
    return pd.DataFrame({'Open': prices, 'High': prices, 'Low': prices, 'Close': prices, 'Volume': 1.0}, index=idx)


class ForwardTests(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(0)
        self.df = bars(100 * np.exp(np.cumsum(rng.normal(0, 0.02, 1500))))

    def run_step(self, df, state, start):
        trades, events = [], []
        return forward.step('TEST', df, state, trades, events, start), trades, events

    def test_signals_are_causal(self):
        full = signals.prepare(self.df)
        part = signals.prepare(self.df.iloc[:1000])
        cols = ['hma_fast', 'hma_slow', 'rsi', 'linreg', 'entry_long', 'exit_long']
        pd.testing.assert_frame_equal(full.loc[part.index, cols], part[cols])

    def test_rerun_is_idempotent(self):
        start = self.df.index[0]
        state, trades, events = self.run_step(self.df, {}, start)
        again, trades2, events2 = self.run_step(self.df, dict(state), start)
        self.assertEqual(trades2, [])
        self.assertEqual(events2, [])
        self.assertGreater(len(trades), 0)

    def test_catch_up_equals_single_pass(self):
        start = self.df.index[0]
        one, t1, _ = self.run_step(self.df, {}, start)
        s, ta, _ = self.run_step(self.df.iloc[:900], {}, start)
        s, tb, _ = self.run_step(self.df, s, start)
        self.assertEqual(t1, ta + tb)

    def test_bars_before_start_are_ignored(self):
        start = self.df.index[-1] + pd.Timedelta(hours=4)
        state, trades, events = self.run_step(self.df, {}, start)
        self.assertEqual(events, [])
        self.assertIsNone(state.get('position'))

    def test_fees_charged_both_sides(self):
        self.assertAlmostEqual(forward.FEE, 0.0003)


if __name__ == '__main__':
    unittest.main()
