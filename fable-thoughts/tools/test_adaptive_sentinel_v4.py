import math
import os
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import adaptive_sentinel_v4 as bot


class Clock:
    def __init__(self):
        self.now = 0

    def monotonic(self):
        return self.now

    def time(self):
        return 1000 + self.now

    def sleep(self, seconds):
        self.now += seconds


class Client:
    def __init__(self, clock, spot=148.02, payout=1.56, lag=1, buy_error=None, settle=True,
                 profit='.56', stale=0):
        self.clock = clock
        self.spot = spot
        self.payout = payout
        self.lag = lag
        self.buy_error = buy_error
        self.settle = settle
        self.profit = profit
        self.stale = stale
        self.calls = []
        self.epoch = 1000

    def _call(self, request):
        self.calls.append(request)
        if 'proposal' in request:
            return {'proposal': {'ask_price': request['amount'], 'payout': request['amount'] * 1.563}}
        if 'ticks_history' in request:
            self.epoch = int(self.clock.time()) - self.stale
            return {'pip_size': 2, 'history': {'times': [self.epoch], 'prices': [self.spot]}}
        if 'buy' in request:
            if self.buy_error:
                raise self.buy_error
            return {'buy': {'contract_id': 12, 'buy_price': 1, 'payout': self.payout}}
        if 'proposal_open_contract' in request:
            if not self.settle:
                return {'proposal_open_contract': {'status': 'open'}}
            return {'proposal_open_contract': {'is_sold': 1, 'status': 'won', 'profit': self.profit,
                                               'exit_spot_time': self.epoch + self.lag}}
        raise AssertionError(request)


class V4Tests(unittest.TestCase):
    def test_threshold_tracks_payout(self):
        self.assertAlmostEqual(bot.threshold(1.56, .01), 148.52633, places=4)
        self.assertLess(bot.threshold(1.45, .01), bot.threshold(1.56, .01))
        self.assertIsNone(bot.threshold(1, .01))

    def test_centered_digits_only(self):
        payouts = dict.fromkeys(bot.CONTRACTS, 1.56)
        for digit in range(10):
            signal = bot.choose(148 + digit / 100, 2, payouts, .01)
            self.assertEqual(signal is not None, digit in (2, 7))
        self.assertEqual(bot.choose(148.07, 2, payouts, .01)['contract'], 'DIGITOVER')
        self.assertIsNone(bot.choose(168.02, 2, payouts, .01))

    def test_invalid_tick_rejected(self):
        for spot, pip in [(148.02, 3), (math.nan, 2), (0, 2), (148.021, 2)]:
            with self.assertRaises(ValueError):
                bot.choose(spot, pip, dict.fromkeys(bot.CONTRACTS, 1.56), .01)

    def test_quotes_cannot_raise_reference_payout(self):
        client = Client(Clock())
        self.assertEqual(set(bot.prices(client, 1).values()), {1.56})

    def test_invalid_quote_halts(self):
        with patch.object(Client, '_call', return_value={'error': {'code': 'RateLimit'}}):
            with self.assertRaises(RuntimeError):
                bot.prices(Client(Clock()), 1)

    def execute(self, trade=True, latency=.1, limits=None, **options):
        clock = Clock()
        client = Client(clock, **options)
        logs = []
        args = SimpleNamespace(minutes=.05, max_trades=2, stake=1, max_loss=2, ev_gate=.01,
                               check_latency=False)
        args.__dict__.update(limits or {})
        with patch.object(bot.time, 'monotonic', clock.monotonic), patch.object(bot.time, 'time', clock.time), \
                patch.object(bot.time, 'sleep', clock.sleep), patch.object(bot, 'measure_latency', return_value=latency):
            bot.run(args, client, client if trade else None, logs.append)
        return client, logs

    def test_watch_never_buys(self):
        client, logs = self.execute(trade=False)
        self.assertFalse(any('buy' in c for c in client.calls))
        self.assertTrue(any(r['event'] == 'signal' for r in logs))

    def test_diagnostic_never_quotes_or_buys(self):
        client, logs = self.execute(limits={'check_latency': True})
        self.assertEqual(client.calls, [])
        self.assertEqual(logs[-1]['reason'], 'latency diagnostic only')

    def test_stale_tick_never_buys(self):
        client, _ = self.execute(stale=1)
        self.assertFalse(any('buy' in c for c in client.calls))

    def test_loss_budget_reserves_full_next_stake(self):
        client, logs = self.execute(profit='-1', limits={'max_loss': 1.5})
        self.assertEqual(sum('buy' in c for c in client.calls), 1)
        self.assertEqual(logs[-1]['pnl'], -1)
        self.assertEqual(logs[-1]['reason'], 'remaining loss budget smaller than stake')

    def test_real_account_refused_before_public_connection(self):
        from unittest.mock import Mock
        client = Mock(account={'account_type': 'real'})
        with tempfile.TemporaryDirectory() as folder, patch.object(bot, 'DerivWS', return_value=client) as constructor, \
                patch.dict(os.environ, {'DERIV_TOKEN': 'test', 'DERIV_APP_ID': 'test'}), \
                patch.object(sys, 'argv', ['v4', '--trade', '--log', folder + '/session.jsonl']), patch('builtins.print'):
            bot.main()
            self.assertEqual(constructor.call_count, 1)
            client._call.assert_not_called()

    def test_closed_regime_never_buys(self):
        client, _ = self.execute(spot=168.02)
        self.assertFalse(any('buy' in c for c in client.calls))

    def test_slow_connection_never_buys(self):
        client, logs = self.execute(latency=.5)
        self.assertFalse(any('buy' in c for c in client.calls))
        self.assertEqual(logs[-1]['reason'], 'connection too slow')

    def test_lower_fill_settles_then_halts(self):
        client, logs = self.execute(payout=1.50)
        self.assertEqual(sum('buy' in c for c in client.calls), 1)
        self.assertEqual(logs[-1]['reason'], 'executed payout below decision assumption')
        self.assertIsNone(logs[-1]['pending_contract'])

    def test_late_settlement_halts(self):
        client, logs = self.execute(lag=2)
        self.assertEqual(sum('buy' in c for c in client.calls), 1)
        self.assertIn('next-tick', logs[-1]['reason'])

    def test_ambiguous_buy_not_retried(self):
        client, logs = self.execute(buy_error=TimeoutError())
        self.assertEqual(sum('buy' in c for c in client.calls), 1)
        self.assertEqual(logs[-1]['pending_contract'], 'buy outcome unknown')

    def test_interrupt_during_buy_preserves_unknown_state(self):
        client, logs = self.execute(buy_error=KeyboardInterrupt())
        self.assertEqual(sum('buy' in c for c in client.calls), 1)
        self.assertEqual(logs[-1]['pending_contract'], 'buy outcome unknown')

    def test_unsettled_contract_blocks_further_buys(self):
        client, logs = self.execute(settle=False)
        self.assertEqual(sum('buy' in c for c in client.calls), 1)
        self.assertEqual(logs[-1]['pending_contract'], 12)

    def test_settles_before_next_buy_and_caps_count(self):
        client, logs = self.execute()
        events = [r['event'] for r in logs if r['event'] in ('buy', 'settled')]
        self.assertEqual(events, ['buy', 'settled', 'buy', 'settled'])
        self.assertEqual(logs[-1]['pnl'], 1.12)


if __name__ == '__main__':
    unittest.main()
