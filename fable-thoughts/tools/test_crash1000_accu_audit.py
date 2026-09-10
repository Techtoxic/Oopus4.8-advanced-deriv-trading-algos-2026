import unittest
from unittest.mock import patch

import crash1000_accu_audit as bot


class Clock:
    def __init__(self):
        self.value = 0

    def monotonic(self):
        return self.value

    def time(self):
        return 1000000 + self.value

    def sleep(self, seconds):
        self.value += seconds


class Client:
    account = {'account_type': 'demo'}

    def __init__(self, clock, spot=6000, profit='1.19', error=None, entry=6000, pending=False):
        self.clock = clock
        self.spot = spot
        self.profit = profit
        self.error = error
        self.entry = entry
        self.pending = pending
        self.calls = []
        self.buys = 0
        self.closed_manually = False

    def _call(self, r):
        self.calls.append(r)
        if 'ticks_history' in r:
            return {'pip_size': 3}
        if 'proposal' in r:
            return {'proposal': self.quote()}
        if 'buy' in r:
            self.buys += 1
            if self.error:
                raise self.error
            self.clock.value += 21
            return {'buy': {'contract_id': self.buys, 'buy_price': 1}}
        if 'sell' in r:
            self.closed_manually = True
            return {'sell': {'sold_for': 1}}
        if 'proposal_open_contract' in r:
            return {'proposal_open_contract': {
                'is_sold': int(not self.pending or self.closed_manually), 'entry_spot': self.entry,
                'growth_rate': .04, 'buy_price': 1,
                'shortcode': 'ACCU_CRASH1000_1.00_0_0.04_1_0.0000023454_1_0',
                'profit': self.profit, 'status': 'won' if float(self.profit) > 0 else 'lost',
                'entry_spot_time': 1000000, 'current_spot_time': 1000022,
                'limit_order': {'take_profit': {'order_amount': 1.19}}}}
        raise AssertionError(r)

    def quote(self):
        return {'ask_price': 1, 'spot': self.spot, 'spot_time': int(self.clock.time()),
                'contract_details': {'tick_size_barrier': bot.BARRIER, 'maximum_ticks': 65},
                'limit_order': {'take_profit': {'order_amount': 1.19}}}


class Tests(unittest.TestCase):
    def run_audit(self, execute=True, check=False, **options):
        clock = Clock()
        client = Client(clock, **options)
        logs = []
        with patch.object(bot.time, 'monotonic', clock.monotonic), patch.object(bot.time, 'time', clock.time), \
                patch.object(bot.time, 'sleep', clock.sleep):
            try:
                bot.run(client, 2000, execute, logs.append, check)
            except SystemExit:
                pass
        return client, logs

    def test_candidate_gate(self):
        c = Client(Clock())
        self.assertTrue(bot.eligible(c.quote(), 3))
        for spot in [5890, 5969.13, 6075.723, 6100]:
            c.spot = spot
            self.assertFalse(bot.eligible(c.quote(), 3))

    def test_changed_barrier_rejected(self):
        q = Client(Clock()).quote()
        q['contract_details']['tick_size_barrier'] *= 1.01
        with self.assertRaises(RuntimeError):
            bot.eligible(q, 3)

    def test_changed_pip_rejected(self):
        with self.assertRaises(RuntimeError):
            bot.eligible(Client(Clock()).quote(), 2)

    def test_wrong_take_profit_rejected(self):
        q = Client(Clock()).quote()
        q['limit_order']['take_profit']['order_amount'] = 2.19
        with self.assertRaises(RuntimeError):
            bot.eligible(q, 3)

    def test_watch_never_buys(self):
        c, _ = self.run_audit(execute=False)
        self.assertEqual(c.buys, 0)

    def test_check_never_buys_even_with_execute(self):
        c, logs = self.run_audit(check=True)
        self.assertEqual(c.buys, 0)
        self.assertEqual(logs[-1]['reason'], 'check only; no purchases')

    def test_outside_state_never_buys(self):
        c, _ = self.run_audit(spot=5890)
        self.assertEqual(c.buys, 0)

    def test_twenty_contract_limit(self):
        c, logs = self.run_audit()
        self.assertEqual(c.buys, 20)
        self.assertEqual(logs[-1]['pnl'], '23.80')

    def test_ten_dollar_loss_limit(self):
        c, logs = self.run_audit(profit='-1')
        self.assertEqual(c.buys, 10)
        self.assertEqual(logs[-1]['pnl'], '-10')

    def test_buy_failure_not_retried(self):
        c, logs = self.run_audit(error=TimeoutError())
        self.assertEqual(c.buys, 1)
        self.assertEqual(logs[-1]['pending'], 'buy outcome unknown')

    def test_interrupt_preserves_pending_state(self):
        c, logs = self.run_audit(error=KeyboardInterrupt())
        self.assertEqual(c.buys, 1)
        self.assertEqual(logs[-1]['pending'], 'buy outcome unknown')

    def test_actual_entry_outside_state_stops(self):
        c, logs = self.run_audit(entry=5890)
        self.assertEqual(c.buys, 1)
        self.assertEqual(logs[-1]['reason'], 'mechanics mismatch; audit halted for review')

    def test_missing_auto_exit_closes_and_halts(self):
        c, logs = self.run_audit(pending=True)
        self.assertEqual(c.buys, 1)
        self.assertTrue(c.closed_manually)
        self.assertEqual(logs[-1]['reason'], 'mechanics mismatch; audit halted for review')

    def test_real_account_refused(self):
        c = Client(Clock())
        c.account = {'account_type': 'real'}
        with self.assertRaises(RuntimeError):
            bot.run(c, 1, True, lambda _: None)
        self.assertEqual(c.calls, [])


if __name__ == '__main__':
    unittest.main()
