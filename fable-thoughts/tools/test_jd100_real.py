import ast
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import websocket

import adaptive_sentinel_v4 as entry
import deriv_api
from crash1000_accu_audit import read_request
import test_jd100_continuous as fixtures


class SmallStakeScenario(fixtures.Scenario):
    def __init__(self):
        super().__init__()
        self.account_type = 'real'
        self.account_id = 'real-test-id'
        self.buy_requests = []
        self.reject = False
        self.actual_payout = .54

    def client(self, role):
        client = super().client(role)
        call = client._call
        def request(payload):
            if 'buy' in payload:
                self.buy_requests.append(payload)
                if self.reject:
                    return {'error': {'code': 'InsufficientBalance'}}
            response = call(payload)
            if 'proposal' in response:
                response['proposal']['payout'] = .54
            if 'buy' in response:
                response['buy'].update(buy_price=.35, payout=self.actual_payout)
            if 'proposal_open_contract' in response:
                response['proposal_open_contract']['profit'] = f'{self.actual_payout - .35:.2f}'
            return response
        client._call = request
        return client


class RealSupportTests(unittest.TestCase):
    def test_explicit_real_selects_real_even_when_demo_is_first(self):
        accounts = [{'account_type': 'demo', 'account_id': 'd'}, {'account_type': 'real', 'account_id': 'r'}]
        with patch.object(deriv_api.DerivWS, '_rest', side_effect=[{'data': accounts}, {'data': {'url': 'wss://unit.invalid'}}]) as rest, \
             patch.object(deriv_api.websocket, 'create_connection'):
            client = deriv_api.DerivWS(token='unit-token', account_type='real')
        self.assertEqual(client.account['account_id'], 'r')
        self.assertEqual(rest.call_args.args[1], '/trading/v1/options/accounts/r/otp')

    def test_requested_account_type_never_falls_back(self):
        for requested, available in [('real', 'demo'), ('demo', 'real')]:
            with self.subTest(requested=requested), \
                 patch.object(deriv_api.DerivWS, '_rest', return_value={'data': [{'account_type': available, 'account_id': 'x'}]}), \
                 patch.object(deriv_api.websocket, 'create_connection') as connect:
                with self.assertRaises(RuntimeError):
                    deriv_api.DerivWS(token='unit-token', account_type=requested)
                connect.assert_not_called()

    def test_explicit_account_requires_token(self):
        with patch.object(deriv_api.DerivWS, '_connect') as connect, self.assertRaises(ValueError):
            deriv_api.DerivWS(token='', account_type='real')
        connect.assert_not_called()

    def test_real_small_stake_executes_and_reconciles_with_mocks(self):
        scenario = SmallStakeScenario()
        events = fixtures.StreamingTests.run_case(self, scenario, real=True, stake=.35)
        self.assertEqual(events[-1]['trades'], 2)
        self.assertEqual(events[-1]['pnl'], .38)
        self.assertEqual(events[-1]['pending_contracts'], [])
        self.assertTrue(all(r['price'] == .35 and r['parameters']['amount'] == .35 for r in scenario.buy_requests))

    def test_real_mode_refuses_demo_connections(self):
        scenario = fixtures.Scenario()
        events = fixtures.StreamingTests.run_case(self, scenario, real=True)
        self.assertEqual(scenario.orders, [])
        self.assertEqual(events[-1]['stage'], 'account_validation')

    def test_small_stake_adverse_fill_is_reconciled_then_stops(self):
        scenario = SmallStakeScenario()
        scenario.actual_payout = .53
        events = fixtures.StreamingTests.run_case(self, scenario, real=True, stake=.35)
        self.assertEqual(events[-1]['trades'], 1)
        self.assertEqual(events[-1]['pnl'], .18)
        self.assertEqual(events[-1]['pending_contracts'], [])
        self.assertEqual(events[-1]['reason'], 'executed payout below decision assumption')

    def test_invalid_small_stakes_and_real_without_trade_do_not_connect(self):
        arguments = [['--stake', value] for value in ('0.34', '0.351', 'nan')]
        arguments.append(['--real'])
        for args in arguments:
            with self.subTest(args=args), patch.object(sys, 'argv', ['v4']+args), \
                 patch.object(entry, 'DerivWS') as constructor, patch('sys.stderr'), self.assertRaises(SystemExit):
                entry.main()
            constructor.assert_not_called()

    def test_real_mode_refuses_different_settlement_account(self):
        scenario = SmallStakeScenario()
        scenario.control_account = 'another-real-id'
        events = fixtures.StreamingTests.run_case(self, scenario, real=True, stake=.35)
        self.assertEqual(scenario.orders, [])
        self.assertEqual(events[-1]['stage'], 'account_validation')

    def test_broker_rejection_code_is_logged_without_retry(self):
        scenario = SmallStakeScenario()
        scenario.reject = True
        events = fixtures.StreamingTests.run_case(self, scenario, real=True, stake=.35)
        error = next(e for e in events if e['event'] == 'buy_error')
        self.assertEqual(error['error_code'], 'InsufficientBalance')
        self.assertEqual(len(scenario.buy_requests), 1)
        self.assertTrue(events[-1]['unknown_buy_outcome'])
        self.assertEqual(events[-1]['reserved_stake'], .35)

    def test_read_recovery_rejects_account_switch(self):
        client = Mock(account={'account_type': 'real', 'account_id': 'a'})
        client._call.side_effect = websocket.WebSocketConnectionClosedException()
        def reconnect():
            client.account = {'account_type': 'real', 'account_id': 'b'}
        client._connect.side_effect = reconnect
        with patch('crash1000_accu_audit.time.sleep'), self.assertRaises(RuntimeError):
            read_request(client, {'proposal_open_contract': 1, 'contract_id': 1}, lambda row: None,
                         'a', time.monotonic()+15, account_type='real')
        self.assertEqual(client._call.call_count, 1)

    def test_default_read_recovery_remains_demo_only(self):
        client = Mock(account={'account_type': 'real', 'account_id': 'a'})
        with self.assertRaises(RuntimeError):
            read_request(client, {'proposal_open_contract': 1, 'contract_id': 1}, lambda row: None,
                         'a', time.monotonic()+15)
        client._call.assert_not_called()

    def test_cli_explicit_real_and_small_stake_routes_correctly(self):
        trader = Mock(account={'account_type': 'real', 'account_id': 'r', 'currency': 'USD'})
        public = Mock()
        with tempfile.TemporaryDirectory() as folder, \
             patch.dict(os.environ, {'DERIV_TOKEN': 'unit-token', 'DERIV_APP_ID': 'unit-app'}), \
             patch.object(sys, 'argv', ['v4', '--real', '--trade', '--stake', '.35', '--log', folder+'/log.jsonl']), \
             patch.object(entry, 'DerivWS', side_effect=[trader, public]) as construct, \
             patch.object(entry, 'run') as run, patch('builtins.print'):
            entry.main()
            self.assertEqual(construct.call_args_list[0].kwargs['account_type'], 'real')
            self.assertEqual(run.call_args.args[0].stake, .35)
            start = json.loads(Path(folder+'/log.jsonl').read_text().splitlines()[0])
            self.assertTrue(start['real_trade'])
            self.assertFalse(start['demo_trade'])

    def test_non_usd_account_is_rejected_before_run(self):
        trader = Mock(account={'account_type': 'real', 'account_id': 'r', 'currency': 'EUR'})
        with tempfile.TemporaryDirectory() as folder, \
             patch.dict(os.environ, {'DERIV_TOKEN': 'unit-token', 'DERIV_APP_ID': 'unit-app'}), \
             patch.object(sys, 'argv', ['v4', '--real', '--trade', '--log', folder+'/log.jsonl']), \
             patch.object(entry, 'DerivWS', return_value=trader), \
             patch.object(entry, 'run') as run, patch('builtins.print'):
            entry.main()
            run.assert_not_called()

    def test_zero_balance_is_reported_before_any_buy(self):
        trader = Mock(account={'account_type': 'real', 'account_id': 'r', 'currency': 'USD', 'balance': '0.00'})
        with tempfile.TemporaryDirectory() as folder, \
             patch.dict(os.environ, {'DERIV_TOKEN': 'unit-token', 'DERIV_APP_ID': 'unit-app'}), \
             patch.object(sys, 'argv', ['v4', '--real', '--trade', '--stake', '.35', '--log', folder+'/log.jsonl']), \
             patch.object(entry, 'DerivWS', return_value=trader), \
             patch.object(entry, 'run') as run, patch('builtins.print'):
            entry.main()
            run.assert_not_called()
            self.assertIn('balance', json.loads(Path(folder+'/log.jsonl').read_text())['reason'])

    def test_syntax_error_reports_filename_and_line_without_source_text(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(sys, 'argv', ['v4', '--log', folder+'/log.jsonl']), \
             patch.object(entry, 'DerivWS', return_value=Mock()), \
             patch.object(entry, 'run', side_effect=SyntaxError('invalid syntax', ('jd100_continuous.py', 279, 1, 'private source'))), \
             patch('builtins.print'):
            entry.main()
            records = Path(folder+'/log.jsonl').read_text()
            last = json.loads(records.splitlines()[-1])
            self.assertEqual(last['error_file'], 'jd100_continuous.py')
            self.assertEqual(last['error_line'], 279)
            self.assertNotIn('private source', records)

    def test_token_default_has_no_embedded_credential(self):
        source = ast.parse(Path(deriv_api.__file__).read_text())
        token = next(n for n in source.body if isinstance(n, ast.Assign)
                     and any(isinstance(t, ast.Name) and t.id == 'TOKEN' for t in n.targets))
        self.assertEqual([n.value for n in ast.walk(token) if isinstance(n, ast.Constant)], ['DERIV_TOKEN', ''])


if __name__ == '__main__':
    unittest.main()


class SharedSwitchTests(unittest.TestCase):
    def select(self, default, account_type=None):
        accounts = [{'account_type': 'real', 'account_id': 'r'}, {'account_type': 'demo', 'account_id': 'd'}]
        with patch.object(deriv_api, 'DEFAULT_ACCOUNT_TYPE', default), \
             patch.object(deriv_api.DerivWS, '_rest', side_effect=[{'data': accounts}, {'data': {'url': 'wss://unit.invalid'}}]), \
             patch.object(deriv_api.websocket, 'create_connection'):
            return deriv_api.DerivWS(token='unit-token', account_type=account_type).account['account_id']

    def test_switch_selects_demo_or_real_by_default(self):
        self.assertEqual(self.select('demo'), 'd')
        self.assertEqual(self.select('real'), 'r')

    def test_explicit_argument_overrides_switch(self):
        self.assertEqual(self.select('real', 'demo'), 'd')
        self.assertEqual(self.select('demo', 'real'), 'r')

    def test_jd100_without_real_flag_follows_switch(self):
        for default, expected in (('demo', 'demo'), ('real', 'real')):
            trader = Mock(account={'account_type': expected, 'account_id': 'x', 'currency': 'USD', 'balance': '100'})
            with tempfile.TemporaryDirectory() as folder, patch.object(entry, 'DEFAULT_ACCOUNT_TYPE', default), \
                 patch.dict(os.environ, {'DERIV_TOKEN': 'unit-token', 'DERIV_APP_ID': 'unit-app'}), \
                 patch.object(sys, 'argv', ['v4', '--trade', '--log', folder + '/log.jsonl']), \
                 patch.object(entry, 'DerivWS', side_effect=[trader, Mock()]) as construct, \
                 patch.object(entry, 'run') as run, patch('builtins.print'):
                entry.main()
                self.assertEqual(construct.call_args_list[0].kwargs['account_type'], expected)
                self.assertEqual(run.call_args.args[0].real, expected == 'real')
