from decimal import Decimal
import runpy
from pathlib import Path
import unittest
from unittest.mock import patch

import crash1000_accu_audit as engine
from test_crash1000_accu_audit import Client, Clock


class Client500(Client):
    def __init__(self, clock, **kwargs):
        kwargs.setdefault('spot', 3000)
        kwargs.setdefault('entry', 3000)
        super().__init__(clock, **kwargs)

    def quote(self):
        q = super().quote()
        q['contract_details']['tick_size_barrier'] = engine.BARRIERS['CRASH500']
        return q

    def _call(self, payload):
        if 'ticks_history' in payload:
            assert payload['ticks_history'] == 'CRASH500'
        if 'proposal' in payload:
            assert payload['underlying_symbol'] == 'CRASH500'
        if 'buy' in payload:
            assert payload['parameters']['underlying_symbol'] == 'CRASH500'
        response = super()._call(payload)
        if 'proposal_open_contract' in response:
            response['proposal_open_contract']['shortcode'] = f'ACCU_CRASH500_{self.stake:.2f}_0_0.04_1_0.0000047141_1_0'
        return response


class Crash500Tests(unittest.TestCase):
    def run_audit(self, execute=True, check=False, stake=Decimal('1'), max_loss=Decimal('10'), **kwargs):
        clock = Clock()
        client = Client500(clock, **kwargs)
        events = []
        with patch.object(engine.time, 'monotonic', clock.monotonic), patch.object(engine.time, 'time', clock.time), \
                patch.object(engine.time, 'sleep', clock.sleep):
            engine.run(client, 60, execute, events.append, check, stake, max_loss, 'CRASH500')
        return client, events

    def test_exact_candidate_range(self):
        c = Client500(Clock())
        for spot, expected in [(2969.813, False), (2969.814, True), (3000, True), (3022.846, True), (3022.847, False), (3140, False)]:
            c.spot = spot
            self.assertEqual(engine.eligible(c.quote(), 3, symbol='CRASH500'), expected)

    def test_wrong_symbol_barrier_rejected(self):
        with self.assertRaises(RuntimeError):
            engine.eligible(Client(Clock()).quote(), 3, symbol='CRASH500')

    def test_changed_barrier_rejected(self):
        q = Client500(Clock()).quote()
        q['contract_details']['tick_size_barrier'] *= 1.001
        with self.assertRaises(RuntimeError):
            engine.eligible(q, 3, symbol='CRASH500')

    def test_watch_and_check_never_buy(self):
        for execute, check in [(False, False), (True, True)]:
            c, _ = self.run_audit(execute=execute, check=check)
            self.assertEqual(c.buys, 0)

    def test_outside_state_never_buys(self):
        c, _ = self.run_audit(spot=3140)
        self.assertEqual(c.buys, 0)

    def test_scaled_stake_execution_and_reconciliation(self):
        c, events = self.run_audit(stake=Decimal('3'), max_loss=Decimal('200'))
        self.assertGreater(c.buys, 0)
        self.assertTrue(all(r['path_check']['matched'] for r in events if r['event'] == 'settled'))
        self.assertTrue(all(r['profit'] == '3.57' for r in events if r['event'] == 'settled'))

    def test_loss_budget_preserved(self):
        c, events = self.run_audit(stake=Decimal('3'), profit='-3', max_loss=Decimal('5'))
        self.assertEqual(c.buys, 1)
        self.assertEqual(events[-1]['pnl'], '-3')

    def test_contract_cannot_be_reconciled_as_other_symbol(self):
        c = Client500(Clock())._call({'proposal_open_contract': 1})['proposal_open_contract']
        self.assertTrue(engine.reconcile_path(c, symbol='CRASH500')['matched'])
        self.assertFalse(engine.reconcile_path(c, symbol='CRASH1000')['matched'])

    def test_entry_leaving_range_halts(self):
        c, events = self.run_audit(entry=2900)
        self.assertEqual(c.buys, 1)
        self.assertEqual(events[-1]['reason'], 'mechanics mismatch; audit halted for review')

    def test_entry_point_selects_only_crash500(self):
        with patch.object(engine, 'main') as main:
            runpy.run_path(str(Path(__file__).with_name('crash500_accu_audit.py')), run_name='__main__')
        main.assert_called_once_with('CRASH500')


if __name__ == '__main__':
    unittest.main()
