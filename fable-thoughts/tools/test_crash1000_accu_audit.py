import argparse
from decimal import Decimal
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

    def __init__(self, clock, spot=6000, profit=None, error=None, entry=6000, pending=False):
        self.clock = clock
        self.spot = spot
        self.profit = profit
        self.error = error
        self.entry = entry
        self.pending = pending
        self.calls = []
        self.buys = 0
        self.closed_manually = False
        self.stake = Decimal('1')
        self.take_profit = Decimal('1.19')

    def _call(self, r):
        self.calls.append(r)
        if 'ticks_history' in r:
            return {'pip_size': 3}
        if 'proposal' in r:
            self.stake = Decimal(str(r['amount']))
            self.take_profit = Decimal(str(r['limit_order']['take_profit']))
            return {'proposal': self.quote()}
        if 'buy' in r:
            self.buys += 1
            if self.error:
                raise self.error
            self.clock.value += 21
            assert Decimal(str(r['price'])) == self.stake
            assert Decimal(str(r['parameters']['amount'])) == self.stake
            return {'buy': {'contract_id': self.buys, 'buy_price': float(self.stake)}}
        if 'sell' in r:
            self.closed_manually = True
            return {'sell': {'sold_for': 1}}
        if 'proposal_open_contract' in r:
            _, sale = bot.payout_terms(self.stake)
            profit = self.profit if self.profit is not None else str(sale - self.stake)
            won = float(profit) > 0
            prices = [self.entry + i * .001 for i in range(21)]
            if not won:
                prices[-1] = prices[-2] + .02
            return {'proposal_open_contract': {
                'is_sold': int(not self.pending or self.closed_manually), 'entry_spot': self.entry,
                'growth_rate': .04, 'buy_price': str(self.stake),
                'shortcode': f'ACCU_CRASH1000_{self.stake:.2f}_0_0.04_1_0.0000023454_1_0',
                'profit': profit, 'status': 'won' if won else 'lost',
                'entry_spot_time': 1000000, 'current_spot_time': 1000022,
                'exit_spot_time': 1000020, 'sell_price': str(sale) if won else '0.00',
                'audit_details': {'all_ticks': [{'epoch': 1000000 + i, 'tick': round(p, 3)}
                                               for i, p in enumerate(prices)]},
                'limit_order': {'take_profit': {'order_amount': str(self.take_profit)}}}}
        raise AssertionError(r)

    def quote(self):
        return {'ask_price': str(self.stake), 'spot': self.spot, 'spot_time': int(self.clock.time()),
                'contract_details': {'tick_size_barrier': bot.BARRIER, 'maximum_ticks': 65},
                'limit_order': {'take_profit': {'order_amount': str(self.take_profit)}}}


class Tests(unittest.TestCase):
    def test_path_verifies_twentieth_tick_take_profit(self):
        c = Client(Clock())._call({'proposal_open_contract': 1})['proposal_open_contract']
        self.assertTrue(bot.reconcile_path(c)['matched'])

    def test_path_verifies_breach_on_twentieth_tick_as_loss(self):
        c = Client(Clock(), profit='-1')._call({'proposal_open_contract': 1})['proposal_open_contract']
        r = bot.reconcile_path(c)
        self.assertTrue(r['matched'])
        self.assertEqual(r['first_model_breach']['epoch'], c['exit_spot_time'])

    def test_loss_without_modeled_breach_rejected(self):
        c = Client(Clock())._call({'proposal_open_contract': 1})['proposal_open_contract']
        c.update(status='lost', profit='-1', sell_price='0')
        self.assertFalse(bot.reconcile_path(c)['matched'])

    def test_missing_audit_not_claimed_as_matching(self):
        c = Client(Clock())._call({'proposal_open_contract': 1})['proposal_open_contract']
        c['audit_details']['all_ticks'].pop(5)
        self.assertIsNone(bot.reconcile_path(c)['matched'])

    def test_ticks_after_exit_are_excluded(self):
        c = Client(Clock())._call({'proposal_open_contract': 1})['proposal_open_contract']
        c['audit_details']['all_ticks'].append({'epoch': 1000021, 'tick': 1})
        self.assertTrue(bot.reconcile_path(c)['matched'])

    def test_wrong_take_profit_payment_rejected(self):
        c = Client(Clock())._call({'proposal_open_contract': 1})['proposal_open_contract']
        c['sell_price'] = '2.28'
        self.assertFalse(bot.reconcile_path(c)['matched'])

    def run_audit(self, execute=True, check=False, stake=Decimal('1'), max_loss=Decimal('10'), seconds=2000, **options):
        clock = Clock()
        client = Client(clock, **options)
        logs = []
        with patch.object(bot.time, 'monotonic', clock.monotonic), patch.object(bot.time, 'time', clock.time), \
                patch.object(bot.time, 'sleep', clock.sleep):
            try:
                bot.run(client, seconds, execute, logs.append, check, stake, max_loss)
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

    def test_duration_allows_more_than_twenty_contracts(self):
        c, logs = self.run_audit()
        self.assertGreater(c.buys, 20)
        self.assertEqual(Decimal(logs[-1]['pnl']), c.buys * Decimal('1.19'))
        self.assertEqual(logs[-1]['reason'], 'time limit reached')

    def test_two_hundred_dollar_loss_budget(self):
        c, logs = self.run_audit(profit='-1', max_loss=Decimal('200'), seconds=10000)
        self.assertEqual(c.buys, 200)
        self.assertEqual(logs[-1]['pnl'], '-200')
        self.assertEqual(logs[-1]['reason'], 'remaining session loss budget smaller than stake')

    def test_fractional_stake_reserves_full_loss(self):
        c, logs = self.run_audit(stake=Decimal('2.03'), profit='-2.03', max_loss=Decimal('5'))
        self.assertEqual(c.buys, 2)
        self.assertEqual(logs[-1]['pnl'], '-4.06')

    def test_scaled_stake_and_payment_reconciliation(self):
        c, logs = self.run_audit(stake=Decimal('2.03'), max_loss=Decimal('200'), seconds=30)
        self.assertEqual(c.take_profit, Decimal('2.41'))
        settled = [r for r in logs if r['event'] == 'settled']
        self.assertTrue(settled)
        self.assertTrue(all(r['path_check']['matched'] for r in settled))
        self.assertTrue(all(r['profit'] == '2.42' for r in settled))

    def test_target_does_not_require_a_twenty_first_tick(self):
        for value in ['1', '2', '2.03', '10', '100', '300']:
            stake = Decimal(value)
            target, sale = bot.payout_terms(stake)
            self.assertLess(stake * (Decimal('1.04') ** 19 - 1), target)
            self.assertLessEqual(target, stake * (Decimal('1.04') ** 20 - 1))
            self.assertEqual(sale - stake, (sale - stake).quantize(Decimal('.01')))

    def test_invalid_money_arguments(self):
        for value in ['0', '-1', 'nan', 'inf', '1.001', '1e999', 'garbage']:
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                bot.money(value)
        self.assertEqual(bot.money('200'), Decimal('200'))

    def test_deadline_rechecked_after_slow_quote(self):
        original = Client._call

        def slow(client, request):
            if 'proposal' in request:
                client.clock.value += 40
            return original(client, request)

        with patch.object(Client, '_call', slow):
            c, logs = self.run_audit(seconds=30)
        self.assertEqual(c.buys, 0)
        self.assertEqual(logs[-1]['reason'], 'time limit reached')

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

    def test_path_disagreement_stops_further_buys(self):
        original = Client._call

        def inconsistent(client, request):
            response = original(client, request)
            if 'proposal_open_contract' in response:
                response['proposal_open_contract']['audit_details']['all_ticks'][-1]['tick'] = client.entry + .02
            return response

        with patch.object(Client, '_call', inconsistent):
            client, logs = self.run_audit(profit='-1')
        self.assertEqual(client.buys, 1)
        self.assertEqual(logs[-1]['reason'], 'path model mismatch or unavailable audit; halted for review')

    def test_unavailable_path_stops_further_buys(self):
        original = Client._call

        def incomplete(client, request):
            response = original(client, request)
            if 'proposal_open_contract' in response:
                response['proposal_open_contract'].pop('audit_details')
            return response

        with patch.object(Client, '_call', incomplete):
            client, logs = self.run_audit()
        self.assertEqual(client.buys, 1)
        self.assertEqual(logs[-1]['reason'], 'path model mismatch or unavailable audit; halted for review')


if __name__ == '__main__':
    unittest.main()
