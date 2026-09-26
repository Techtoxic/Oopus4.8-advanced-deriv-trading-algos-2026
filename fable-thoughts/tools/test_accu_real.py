import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from decimal import Decimal
from unittest.mock import Mock, patch

import crash1000_accu_audit as bot
import test_crash1000_accu_audit as fixtures


class RealModeTests(unittest.TestCase):
    def run_real(self, account_type_connected='real', requested='real', **options):
        clock = fixtures.Clock()
        client = fixtures.Client(clock, **options)
        client.account = {'account_type': account_type_connected, 'account_id': 'acct-1'}
        logs = []
        with patch.object(bot.time, 'monotonic', clock.monotonic), patch.object(bot.time, 'time', clock.time), \
                patch.object(bot.time, 'sleep', clock.sleep):
            bot.run(client, 2000, True, logs.append, False, Decimal('1'), Decimal('10'), 'CRASH1000', requested)
        return client, logs

    def test_default_run_still_refuses_real_account(self):
        c = fixtures.Client(fixtures.Clock())
        c.account = {'account_type': 'real', 'account_id': 'acct-1'}
        with self.assertRaises(RuntimeError):
            bot.run(c, 1, True, lambda _: None)
        self.assertEqual(c.calls, [])

    def test_real_request_refuses_demo_connection(self):
        with self.assertRaises(RuntimeError):
            self.run_real(account_type_connected='demo')

    def test_real_request_trades_on_real_account_with_same_guards(self):
        c, logs = self.run_real()
        self.assertGreater(c.buys, 0)
        self.assertTrue(all(json.dumps(l, default=str) for l in logs))
        self.assertEqual(logs[-1]['event'], 'summary')

    def test_cli_real_flag_selects_real_and_marks_start(self):
        client = Mock(account={'account_type': 'real', 'account_id': 'r', 'currency': 'USD', 'balance': '25.00'})
        with tempfile.TemporaryDirectory() as folder, \
             patch.dict(os.environ, {'DERIV_TOKEN': 'unit', 'DERIV_APP_ID': 'unit'}), \
             patch.object(sys, 'argv', ['accu', '--execute', '--real', '--out', folder + '/x.jsonl']), \
             patch.object(bot, 'DerivWS', return_value=client) as construct, patch.object(bot, 'run') as run, patch('builtins.print'):
            bot.main()
            self.assertEqual(construct.call_args.kwargs['account_type'], 'real')
            self.assertEqual(run.call_args.args[-1], 'real')
            start = json.loads(Path(folder + '/x.jsonl').read_text().splitlines()[0])
            self.assertTrue(start['real_execute'])
            self.assertFalse(start['demo_execute'])

    def test_cli_without_real_flag_requests_demo(self):
        client = Mock(account={'account_type': 'demo', 'account_id': 'd', 'currency': 'USD', 'balance': '25.00'})
        with tempfile.TemporaryDirectory() as folder, \
             patch.dict(os.environ, {'DERIV_TOKEN': 'unit', 'DERIV_APP_ID': 'unit'}), \
             patch.object(sys, 'argv', ['accu', '--execute', '--out', folder + '/x.jsonl']), \
             patch.object(bot, 'DerivWS', return_value=client) as construct, patch.object(bot, 'run') as run, patch('builtins.print'):
            bot.main()
            self.assertEqual(construct.call_args.kwargs['account_type'], 'demo')
            self.assertEqual(run.call_args.args[-1], 'demo')

    def test_real_balance_below_stake_halts_before_run(self):
        client = Mock(account={'account_type': 'real', 'account_id': 'r', 'currency': 'USD', 'balance': '0.50'})
        with tempfile.TemporaryDirectory() as folder, \
             patch.dict(os.environ, {'DERIV_TOKEN': 'unit', 'DERIV_APP_ID': 'unit'}), \
             patch.object(sys, 'argv', ['accu', '--execute', '--real', '--out', folder + '/x.jsonl']), \
             patch.object(bot, 'DerivWS', return_value=client), patch.object(bot, 'run') as run, patch('builtins.print'):
            bot.main()
            run.assert_not_called()
            self.assertIn('balance', json.loads(Path(folder + '/x.jsonl').read_text().splitlines()[-1])['reason'])


if __name__ == '__main__':
    unittest.main()
