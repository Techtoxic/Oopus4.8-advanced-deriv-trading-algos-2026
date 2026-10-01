import json
import time
import unittest
from unittest.mock import Mock, patch

import websocket

import deriv_api
import jd100_continuous as bot
import test_jd100_continuous as fixtures


class ResponseSocket:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.sent = []
        self.timeout = 5

    def send(self, payload):
        self.sent.append(json.loads(payload))

    def recv(self):
        value = next(self.responses)
        if isinstance(value, Exception):
            raise value
        return json.dumps(value)

    def gettimeout(self):
        return self.timeout

    def settimeout(self, timeout):
        self.timeout = timeout


class RequestRecoveryTests(unittest.TestCase):
    def client(self, responses):
        with patch.object(deriv_api.DerivWS, '_connect'):
            client = deriv_api.DerivWS(token='', timeout=5)
        client.ws = ResponseSocket(responses)
        return client

    def test_timeout_recovers_matching_ack_without_sending_again(self):
        client = self.client([websocket.WebSocketTimeoutException(),
                              {'req_id': 999, 'msg_type': 'error', 'error': {'code': 'OldError'}},
                              {'req_id': 1, 'buy': {'contract_id': 123}}])
        with self.assertRaises(websocket.WebSocketTimeoutException):
            client._call({'buy': 1})
        reply = client.receive_pending_buy()
        self.assertEqual(reply['buy']['contract_id'], 123)
        self.assertEqual(len(client.ws.sent), 1)
        self.assertEqual(client.ws.gettimeout(), 5)
        self.assertIsNone(client._pending_request)

    def test_unknown_buy_blocks_all_new_requests(self):
        client = self.client([websocket.WebSocketTimeoutException()])
        with self.assertRaises(websocket.WebSocketTimeoutException):
            client._call({'buy': 1})
        for payload in ({'buy': 1}, {'ping': 1}):
            with self.assertRaises(RuntimeError):
                client._call(payload)
        self.assertEqual(len(client.ws.sent), 1)

    def test_expired_recovery_keeps_request_unresolved(self):
        client = self.client([websocket.WebSocketTimeoutException(), websocket.WebSocketTimeoutException()])
        with self.assertRaises(websocket.WebSocketTimeoutException):
            client._call({'buy': 1})
        with self.assertRaises(websocket.WebSocketTimeoutException):
            client.receive_pending_buy()
        self.assertEqual(client._pending_request, (1, 'buy'))
        self.assertEqual(len(client.ws.sent), 1)

    def test_duplicate_old_ack_cannot_complete_new_buy(self):
        client = self.client([{'req_id': 1, 'buy': {'contract_id': 101}},
                              {'req_id': 1, 'buy': {'contract_id': 101}},
                              {'req_id': 2, 'buy': {'contract_id': 102}}])
        self.assertEqual(client._call({'buy': 1})['buy']['contract_id'], 101)
        self.assertEqual(client._call({'buy': 1})['buy']['contract_id'], 102)

    def test_new_connection_cannot_recover_an_old_request_id(self):
        client = self.client([websocket.WebSocketTimeoutException()])
        with self.assertRaises(websocket.WebSocketTimeoutException):
            client._call({'buy': 1})
        client.ws = ResponseSocket([{'req_id': 1, 'buy': {'contract_id': 999}}])
        with self.assertRaises(RuntimeError):
            client.receive_pending_buy()
        self.assertEqual(client._pending_request, (1, 'buy'))

    def test_response_deadline_is_absolute_despite_unrelated_messages(self):
        client = self.client([])
        client._pending_request = (1, 'buy')
        with patch.object(deriv_api.time, 'monotonic', return_value=10), self.assertRaises(websocket.WebSocketTimeoutException):
            client._receive_response(1, 9)
        self.assertEqual(client.ws.gettimeout(), 5)


class RecoverScenario(fixtures.Scenario):
    def __init__(self):
        super().__init__()
        self.timeout_on = 2
        self.ack_fails = False
        self.receive_calls = 0
        self.fail_ping = False
        self.fail_feed = False
        self.reconnects = []
        self.receive_delay = 0
        self.idle_feed = False

    def client(self, role):
        client = super().client(role)
        original = client._call
        def request(payload):
            if role == 'buyer' and 'ping' in payload and self.fail_ping:
                self.fail_ping = False
                raise websocket.WebSocketTimeoutException()
            reply = original(payload)
            if role == 'buyer' and 'buy' in payload and len(self.orders) == self.timeout_on:
                self.saved = reply
                raise websocket.WebSocketTimeoutException()
            return reply
        def receive(timeout):
            self.receive_calls += 1
            time.sleep(self.receive_delay)
            if self.ack_fails:
                raise websocket.WebSocketTimeoutException()
            return self.saved
        def reconnect():
            self.reconnects.append(role)
            client.ws = fixtures.Socket(self)
        client._call = request
        client.receive_pending_buy = receive
        client._connect = reconnect
        if role == 'public' and self.idle_feed:
            def idle():
                time.sleep(.003)
                raise websocket.WebSocketTimeoutException()
            client.ws.recv = idle
        if role == 'public' and self.fail_feed:
            recv = client.ws.recv
            failed = False
            def receive_tick():
                nonlocal failed
                if not failed:
                    failed = True
                    raise websocket.WebSocketConnectionClosedException()
                return recv()
            client.ws.recv = receive_tick
        return client


class SessionRecoveryTests(unittest.TestCase):
    def test_late_ack_preserves_prior_pnl_and_continues(self):
        scenario = RecoverScenario()
        events = fixtures.StreamingTests.run_case(self, scenario, max_pending=1, max_trades=3)
        self.assertEqual(events[-1]['trades'], 3)
        self.assertEqual(events[-1]['pnl'], 1.62)
        self.assertFalse(events[-1]['unknown_buy_outcome'])
        self.assertEqual(scenario.receive_calls, 1)
        self.assertEqual(len(scenario.orders), 3)
        self.assertTrue(any(e['event'] == 'buy_ack_received' for e in events))

    def test_missing_ack_preserves_profit_and_unknown_stake_without_retry(self):
        scenario = RecoverScenario()
        scenario.ack_fails = True
        events = fixtures.StreamingTests.run_case(self, scenario, max_pending=1, max_trades=3)
        self.assertEqual(events[-1]['trades'], 1)
        self.assertEqual(events[-1]['pnl'], .54)
        self.assertTrue(events[-1]['unknown_buy_outcome'])
        self.assertEqual(events[-1]['reserved_stake'], 1)
        self.assertEqual(events[-1]['stage'], 'buy_ack_recovery')
        self.assertEqual(len(scenario.orders), 2)

    def test_heartbeat_timeout_reconnects_without_resetting_session(self):
        scenario = RecoverScenario()
        scenario.fail_ping = True
        scenario.timeout_on = 100
        with patch.object(bot, 'PING_INTERVAL_SECONDS', 0):
            events = fixtures.StreamingTests.run_case(self, scenario, minutes=.05)
        self.assertEqual(events[-1]['trades'], 2)
        self.assertEqual(events[-1]['pnl'], 1.08)
        self.assertEqual(scenario.reconnects, ['buyer'])

    def test_late_ack_keeps_the_loss_budget(self):
        scenario = RecoverScenario()
        scenario.timeout_on = 1
        scenario.status, scenario.profit = 'lost', '-1'
        events = fixtures.StreamingTests.run_case(self, scenario, max_loss=1, max_trades=10)
        self.assertEqual(events[-1]['trades'], 1)
        self.assertEqual(events[-1]['pnl'], -1)
        self.assertEqual(len(scenario.orders), 1)

    def test_ack_after_session_deadline_does_not_allow_another_buy(self):
        scenario = RecoverScenario()
        scenario.timeout_on = 1
        scenario.receive_delay = .1
        events = fixtures.StreamingTests.run_case(self, scenario, minutes=.001, max_trades=10)
        self.assertEqual(events[-1]['trades'], 1)
        self.assertEqual(events[-1]['pending_contracts'], [])
        self.assertEqual(len(scenario.orders), 1)

    def test_idle_feed_is_resubscribed(self):
        scenario = RecoverScenario()
        scenario.timeout_on = 100
        scenario.idle_feed = True
        with patch.object(bot, 'FEED_IDLE_SECONDS', .01):
            events = fixtures.StreamingTests.run_case(self, scenario, minutes=.05)
        self.assertEqual(events[-1]['trades'], 2)
        self.assertEqual(scenario.reconnects, ['public'])

    def test_feed_disconnect_resubscribes_and_continues(self):
        scenario = RecoverScenario()
        scenario.fail_feed = True
        scenario.timeout_on = 100
        events = fixtures.StreamingTests.run_case(self, scenario, minutes=.05)
        self.assertEqual(events[-1]['trades'], 2)
        self.assertEqual(scenario.reconnects, ['public'])

    def test_reconnect_account_change_is_rejected_before_ping(self):
        client = Mock(account={'account_id': 'a', 'account_type': 'real'})
        def connect():
            client.account = {'account_id': 'b', 'account_type': 'real'}
        client._connect.side_effect = connect
        with patch.object(bot.time, 'sleep'), self.assertRaises(RuntimeError):
            bot.reconnect_channel(client, lambda e: None, time.monotonic()+30, 'buyer', ('a', 'real'))
        client._call.assert_not_called()

    def test_reconnect_attempts_are_bounded(self):
        client = Mock()
        client._connect.side_effect = websocket.WebSocketConnectionClosedException()
        with patch.object(bot.time, 'sleep'), self.assertRaises(RuntimeError):
            bot.reconnect_channel(client, lambda e: None, time.monotonic()+30, 'feed')
        self.assertEqual(client._connect.call_count, 3)

    def test_recovery_never_extends_session_deadline(self):
        client = Mock()
        with patch.object(bot.time, 'monotonic', return_value=20), self.assertRaises(TimeoutError):
            bot.reconnect_channel(client, lambda e: None, 19, 'feed')
        client._connect.assert_not_called()
