from concurrent.futures import Future
from decimal import Decimal
import json
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import websocket

import jd100_continuous as bot


class BookTests(unittest.TestCase):
    def setUp(self):
        self.book = bot.Book()
        self.signal = {'assumed_payout': 1.54}

    def add(self, cid=1, **kw):
        buy = {'contract_id': cid, 'buy_price': 1, 'payout': 1.54, **kw}
        self.book.add(buy, self.signal, 1000, Decimal('1'), .1)

    def settled(self, cid=1, **kw):
        return {'contract_id': cid, 'is_sold': 1, 'status': 'won', 'profit': '.54', 'exit_spot_time': 1001, **kw}

    def test_reserves_every_pending_stake(self):
        self.add(1)
        self.add(2)
        self.assertEqual(self.book.reserved(), Decimal('2'))
        self.assertFalse(self.book.can_buy(Decimal('1'), Decimal('2'), 3))
        self.assertTrue(self.book.can_buy(Decimal('1'), Decimal('3'), 3))

    def test_pending_cap_is_independent_of_profit(self):
        self.add()
        self.book.pnl = Decimal('100')
        self.assertFalse(self.book.can_buy(Decimal('1'), Decimal('200'), 1))

    def test_out_of_order_settlements_and_duplicate(self):
        self.add(1)
        self.add(2)
        self.book.settle(2, self.settled(2))
        self.book.settle(1, self.settled(1, status='lost', profit='-1'))
        self.assertEqual(self.book.pnl, Decimal('-.46'))
        with self.assertRaises(KeyError):
            self.book.settle(2, self.settled(2))
        self.assertEqual(self.book.pnl, Decimal('-.46'))

    def test_mismatched_response_cannot_release_reserve(self):
        self.add()
        with self.assertRaises(RuntimeError):
            self.book.settle(1, self.settled(2))
        self.assertEqual(self.book.reserved(), Decimal('1'))
        self.assertEqual(self.book.pnl, 0)

    def test_duplicate_buy_does_not_replace_pending(self):
        self.add()
        with self.assertRaises(RuntimeError):
            self.add()
        self.assertEqual(self.book.count, 1)

    def test_open_status_retains_reserve(self):
        self.add()
        self.assertIsNone(self.book.settle(1, self.settled(is_sold=0)))
        self.assertEqual(self.book.reserved(), Decimal('1'))

    def test_invalid_profit_retains_reserve(self):
        self.add()
        with self.assertRaises(RuntimeError):
            self.book.settle(1, self.settled(profit='NaN'))
        self.assertEqual(self.book.pnl, 0)
        self.assertEqual(self.book.reserved(), 1)

    def test_late_settlement_and_payment_discrepancy_halt(self):
        for values in ({'exit_spot_time': 1002}, {'profit': '.50'}, {'exit_spot_time': None}):
            with self.subTest(values=values):
                self.book = bot.Book()
                self.add()
                self.book.settle(1, self.settled(**values))
                self.assertIsNotNone(self.book.halt)
                self.assertEqual(self.book.pending, {})

    def test_worse_fill_or_wrong_stake_halts_immediately(self):
        for values in ({'payout': 1.5}, {'buy_price': 2}, {'payout': 'NaN'}, {'payout': None}):
            with self.subTest(values=values):
                self.book = bot.Book()
                self.add(**values)
                self.assertIsNotNone(self.book.halt)
                self.assertIn(1, self.book.pending)

    def test_slow_buy_halts_immediately(self):
        self.book.add({'contract_id': 1, 'buy_price': 1, 'payout': 1.54}, self.signal, 1000, Decimal('1'), .401)
        self.assertIsNotNone(self.book.halt)

    def test_wrong_stake_still_records_actual_payment_when_reconciled(self):
        self.add(buy_price=.9)
        event = self.book.settle(1, self.settled(profit='.64'))
        self.assertEqual(event['buy_price'], .9)
        self.assertAlmostEqual(event['executed_payout'], 1.54 / .9)
        self.assertEqual(self.book.pnl, Decimal('.64'))
        self.assertIsNotNone(self.book.halt)

    def test_invalid_contracted_payout_keeps_unverified_reserve(self):
        self.add(payout='NaN')
        with self.assertRaises(RuntimeError):
            self.book.settle(1, self.settled())
        self.assertEqual(self.book.reserved(), 1)
        self.assertEqual(self.book.pnl, 0)

    def test_unknown_buy_remains_reserved(self):
        self.book.unknown_buy = True
        self.book.unknown_stake = Decimal('1')
        self.assertFalse(self.book.can_buy(Decimal('1'), Decimal('200'), 3))
        self.assertEqual(self.book.reserved(), 1)


class Socket:
    def __init__(self, scenario):
        self.scenario, self.timeout = scenario, .05

    def settimeout(self, value):
        self.timeout = value

    def recv(self):
        if self.timeout == .001:
            raise websocket.WebSocketTimeoutException()
        time.sleep(.003)
        if not self.scenario.duplicate:
            self.scenario.epoch += 1
        return json.dumps(self.scenario.tick())

    def close(self):
        pass


class Scenario:
    def __init__(self):
        self.epoch = 1000
        self.stale = 0
        self.duplicate = False
        self.spot = 145.02
        self.payout = 1.54
        self.lag = 1
        self.profit = '.54'
        self.status = 'won'
        self.error = self.read_error = None
        self.orders = []
        self.owners = {}
        self.second_buy = threading.Event()
        self.second_before_first_settlement = False
        self.account_id = 'demo-id'
        self.account_type = 'demo'
        self.control_account = None
        self.ping_result = {'ping': 'pong'}
        self.ping_delay = 0

    def tick(self):
        return {'tick': {'symbol': 'JD100', 'epoch': self.epoch, 'quote': self.spot, 'pip_size': 2}}

    def client(self, role):
        scenario = self

        class Client:
            token, app_id = 'test-token', 'test-app'

            def __init__(self):
                self.ws = Socket(scenario)
                self.account = {'account_type': scenario.account_type, 'account_id': scenario.account_id}
                if role == 'control' and scenario.control_account:
                    self.account['account_id'] = scenario.control_account

            def _call(self, request):
                scenario.owners.setdefault(role, set()).add(threading.get_ident())
                if 'ticks' in request:
                    return scenario.tick()
                if 'ping' in request:
                    time.sleep(scenario.ping_delay)
                    return scenario.ping_result
                if 'proposal' in request:
                    return {'proposal': {'ask_price': request['amount'], 'payout': request['amount'] * 1.54}}
                if 'buy' in request:
                    scenario.orders.append({'epoch': scenario.epoch, 'time': time.monotonic()})
                    if scenario.error:
                        raise scenario.error
                    cid = len(scenario.orders)
                    if cid == 2:
                        scenario.second_buy.set()
                    return {'buy': {'contract_id': cid, 'buy_price': 1, 'payout': scenario.payout}}
                if 'proposal_open_contract' in request:
                    cid = request['contract_id']
                    if scenario.read_error:
                        raise scenario.read_error
                    if cid == 1:
                        scenario.second_before_first_settlement = scenario.second_buy.wait(.08)
                    return {'proposal_open_contract': {'contract_id': cid, 'is_sold': 1, 'status': scenario.status,
                            'profit': scenario.profit, 'exit_spot_time': scenario.orders[cid - 1]['epoch'] + scenario.lag}}
                raise AssertionError(request)

        return Client()


class StreamingTests(unittest.TestCase):
    def run_case(self, scenario, trade=True, **changes):
        args = SimpleNamespace(minutes=.02, stake=1, max_loss=10, max_trades=2,
                               max_pending=3, ev_gate=.01, check_latency=False)
        for key, value in changes.items():
            setattr(args, key, value)
        events = []
        emitting_threads = set()
        def emit(row):
            emitting_threads.add(threading.get_ident())
            events.append(row)
        public, trader, control = (scenario.client(role) for role in ('public', 'buyer', 'control'))
        if not trade:
            trader = None
            control.account = {}
        with patch.object(bot, 'DerivWS', return_value=control), patch.object(bot.model, 'measure_latency', return_value=.05), \
             patch.object(bot.time, 'time', side_effect=lambda: scenario.epoch + scenario.stale):
            bot.run_continuous(args, public, trader, emit)
        self.assertEqual(events[-1]['event'], 'summary')
        self.assertEqual(emitting_threads, {threading.get_ident()})
        return events

    def test_second_buy_precedes_first_settlement_response(self):
        scenario = Scenario()
        events = self.run_case(scenario)
        self.assertTrue(scenario.second_before_first_settlement)
        self.assertEqual(events[-1]['trades'], 2)
        self.assertEqual(events[-1]['pnl'], 1.08)
        self.assertEqual(events[-1]['pending_contracts'], [])
        self.assertEqual(len(scenario.owners['buyer']), 1)
        self.assertEqual(len(scenario.owners['public']), 1)
        self.assertEqual(len(scenario.owners['control']), 2)
        self.assertEqual([e['event'] for e in events if e['event'] in ('buy', 'settled')][:2], ['buy', 'buy'])

    def test_pending_cap_one_waits_without_duplicate_orders(self):
        scenario = Scenario()
        events = self.run_case(scenario, max_pending=1)
        self.assertFalse(scenario.second_before_first_settlement)
        self.assertEqual(events[-1]['trades'], 2)
        self.assertTrue(all(e['pending_count'] <= 1 for e in events if 'pending_count' in e))

    def test_loss_budget_includes_pending_losses(self):
        scenario = Scenario()
        scenario.status, scenario.profit = 'lost', '-1'
        events = self.run_case(scenario, max_loss=2, max_trades=100)
        self.assertEqual(events[-1]['trades'], 2)
        self.assertEqual(events[-1]['pnl'], -2)

    def test_unknown_buy_is_never_retried(self):
        scenario = Scenario()
        scenario.error = TimeoutError()
        events = self.run_case(scenario)
        self.assertEqual(len(scenario.orders), 1)
        self.assertTrue(events[-1]['unknown_buy_outcome'])
        self.assertEqual(events[-1]['reserved_stake'], 1)

    def test_buy_interruption_preserves_unknown_reservation(self):
        scenario = Scenario()
        scenario.error = KeyboardInterrupt()
        events = self.run_case(scenario)
        self.assertEqual(len(scenario.orders), 1)
        self.assertTrue(events[-1]['unknown_buy_outcome'])
        self.assertIn('interrupted', events[-1]['reason'])

    def test_late_settlement_halts_and_drains(self):
        scenario = Scenario()
        scenario.lag = 2
        events = self.run_case(scenario, max_trades=100)
        self.assertIn('guard failed', events[-1]['reason'])
        self.assertLessEqual(events[-1]['trades'], 3)
        self.assertEqual(events[-1]['pending_contracts'], [])
        self.assertEqual(len([e for e in events if e['event'] == 'settled']), events[-1]['trades'])

    def test_adverse_fill_stops_before_second_buy(self):
        scenario = Scenario()
        scenario.payout, scenario.profit = 1.5, '.50'
        events = self.run_case(scenario)
        self.assertEqual(events[-1]['trades'], 1)
        self.assertEqual(events[-1]['pending_contracts'], [])
        self.assertIn('executed payout', events[-1]['reason'])

    def test_failed_reads_leave_known_contracts_in_summary(self):
        scenario = Scenario()
        scenario.read_error = RuntimeError('read unavailable')
        events = self.run_case(scenario, max_trades=100)
        self.assertGreater(events[-1]['trades'], 0)
        self.assertEqual(len(events[-1]['pending_contracts']), events[-1]['trades'])
        self.assertEqual(events[-1]['pnl'], 0)

    def test_stale_and_future_ticks_do_not_trade(self):
        for stale in (.5, -1):
            with self.subTest(stale=stale):
                scenario = Scenario()
                scenario.stale = stale
                self.assertEqual(self.run_case(scenario, minutes=.001)[-1]['trades'], 0)

    def test_closed_price_gate_still_does_not_trade(self):
        scenario = Scenario()
        scenario.spot = 148.02
        self.assertEqual(self.run_case(scenario, minutes=.001)[-1]['trades'], 0)

    def test_quote_refresh_rechecks_signal_before_sending_buy(self):
        scenario = Scenario()
        calls = []
        def prices(*args):
            calls.append(True)
            return {side: 1.54 if len(calls) == 1 else 1.52 for side in bot.model.CONTRACTS}
        def submit(fn, *args):
            future = Future()
            future.set_result(fn(*args))
            return future
        pool = Mock()
        pool.submit.side_effect = submit
        with patch.object(bot, 'QUOTE_REFRESH_SECONDS', 0), patch.object(bot.model, 'prices', side_effect=prices), \
             patch.object(bot, 'ThreadPoolExecutor', return_value=pool):
            events = self.run_case(scenario, minutes=.001)
        self.assertGreater(len(calls), 1)
        self.assertEqual(events[-1]['trades'], 0)

    def test_expired_quotes_cannot_authorize_entries(self):
        scenario = Scenario()
        with patch.object(bot, 'QUOTE_MAX_AGE_SECONDS', 0):
            events = self.run_case(scenario, minutes=.001)
        self.assertEqual(events[-1]['trades'], 0)

    def test_duplicate_ticks_do_not_trade_twice(self):
        scenario = Scenario()
        scenario.duplicate = True
        self.assertEqual(self.run_case(scenario, minutes=.002)[-1]['trades'], 1)

    def test_real_account_is_rejected(self):
        scenario = Scenario()
        scenario.account_type = 'real'
        events = self.run_case(scenario)
        self.assertEqual(scenario.orders, [])
        self.assertIn('error', events[-1]['reason'])

    def test_different_demo_account_is_rejected(self):
        scenario = Scenario()
        scenario.control_account = 'other-demo'
        self.assertIn('error', self.run_case(scenario)[-1]['reason'])
        self.assertEqual(scenario.orders, [])

    def test_watch_never_buys(self):
        scenario = Scenario()
        events = self.run_case(scenario, trade=False, minutes=.001)
        self.assertTrue(any(e['event'] == 'signal' for e in events))
        self.assertEqual(scenario.orders, [])

    def test_diagnostic_never_subscribes_quotes_or_buys(self):
        scenario = Scenario()
        events = self.run_case(scenario, check_latency=True)
        self.assertEqual(events[-1]['trades'], 0)
        self.assertEqual(scenario.owners, {})

    def test_invalid_buyer_ping_prevents_purchase(self):
        scenario = Scenario()
        scenario.ping_result = {'error': {'code': 'Disconnected'}}
        with patch.object(bot, 'PING_INTERVAL_SECONDS', 0):
            events = self.run_case(scenario)
        self.assertEqual(events[-1]['trades'], 0)
        self.assertIn('heartbeat', events[-1]['reason'])

    def test_slow_buyer_ping_prevents_purchase(self):
        scenario = Scenario()
        scenario.ping_delay = .41
        with patch.object(bot, 'PING_INTERVAL_SECONDS', 0):
            events = self.run_case(scenario)
        self.assertEqual(events[-1]['trades'], 0)
        self.assertIn('heartbeat', events[-1]['reason'])

    def test_expired_settlement_deadline_stops_entries_then_drains(self):
        original = bot.Book.add
        def expired(book, *args):
            cid = original(book, *args)
            book.pending[cid]['deadline'] = time.monotonic() - 1
            return cid
        scenario = Scenario()
        with patch.object(bot.Book, 'add', new=expired):
            events = self.run_case(scenario)
        self.assertEqual(events[-1]['trades'], 1)
        self.assertEqual(events[-1]['pending_contracts'], [])
        self.assertIn('deadline exceeded', events[-1]['reason'])

    def test_duration_limit_stops_entries_and_drains(self):
        scenario = Scenario()
        started = time.monotonic()
        events = self.run_case(scenario, max_trades=1000000, minutes=.001)
        self.assertTrue(scenario.orders)
        self.assertTrue(all(r['time'] < started + .09 for r in scenario.orders))
        self.assertEqual(events[-1]['pending_contracts'], [])


class FeedTests(unittest.TestCase):
    def test_buffer_drains_to_newest_epoch_despite_duplicates_and_reordering(self):
        public = Mock()
        public.ws.recv.side_effect = [json.dumps({'tick': {'symbol': 'JD100', 'epoch': epoch}})
                                     for epoch in (1000, 1002, 1002, 1001)] + [websocket.WebSocketTimeoutException()]
        self.assertEqual(bot.newest_tick(public)['epoch'], 1002)
        public.ws.settimeout.assert_called_with(.05)

    def test_wrong_symbol_rejected(self):
        public = Mock()
        public.ws.recv.return_value = json.dumps({'tick': {'symbol': 'R_100', 'epoch': 1000}})
        with self.assertRaises(RuntimeError):
            bot.newest_tick(public)


if __name__ == '__main__':
    unittest.main()
