"""Bounded CRASH1000 candidate-state demo audit. No real-account override."""
import argparse
from datetime import datetime, timezone
from decimal import Decimal
import json
import math
import os
import time

from deriv_api import DerivWS


BARRIER = 2.3454e-6


def eligible(q, pip):
    details = q.get('contract_details', {})
    b = float(details.get('tick_size_barrier', 0))
    if pip != 3 or not math.isfinite(b) or abs(b - BARRIER) > 1e-16 or int(details.get('maximum_ticks', 0)) < 20:
        raise RuntimeError('Contract specification changed')
    if float(q.get('ask_price', 0)) != 1:
        raise RuntimeError('Unexpected quoted stake')
    tp = q.get('limit_order', {}).get('take_profit', {}).get('order_amount')
    if tp is None or not math.isfinite(float(tp)) or abs(float(tp) - 1.19) > 1e-8:
        raise RuntimeError('Take-profit specification missing')
    phase = float(q['spot']) * b * 1000
    if not math.isfinite(phase) or phase <= 0:
        raise RuntimeError('Invalid quote spot')
    return 14 <= phase < 14.25


def run(client, seconds, execute, emit, check_only=False):
    if client.account.get('account_type') != 'demo':
        raise RuntimeError('Refusing non-demo account')
    deadline = time.monotonic() + seconds
    pnl = Decimal('0')
    count = 0
    pending = None
    last_report = -float('inf')
    reason = 'time limit reached'
    params = dict(amount=1, basis='stake', contract_type='ACCU', currency='USD',
                  underlying_symbol='CRASH1000', growth_rate=.04, limit_order={'take_profit': 1.19})
    try:
        while time.monotonic() < deadline:
            if count >= 20 or pnl - 1 < -10:
                reason = 'contract count or loss budget reached'
                break
            h = client._call({'ticks_history': 'CRASH1000', 'count': 1, 'end': 'latest', 'style': 'ticks'})
            r = client._call({'proposal': 1, **params})
            q = r.get('proposal', {})
            if not q:
                raise RuntimeError('Quote unavailable')
            ready = eligible(q, h.get('pip_size'))
            age = time.time() - int(q['spot_time'])
            if time.monotonic() - last_report >= 60 or ready:
                emit({'event': 'state', 'spot': q['spot'], 'spot_time': q['spot_time'],
                      'barrier': BARRIER, 'candidate': ready, 'quote_age': age,
                      'fresh_quote': 0 <= age <= 2,
                      'minimum_spot': 14 / (BARRIER * 1000),
                      'maximum_spot_exclusive': 14.25 / (BARRIER * 1000)})
                last_report = time.monotonic()
            if check_only:
                reason = 'check only; no purchases'
                break
            if not execute or not ready or not 0 <= age <= 2:
                time.sleep(min(10, max(0, deadline - time.monotonic())))
                continue
            pending = 'buy outcome unknown'
            bought = client._call({'buy': 1, 'price': 1, 'parameters': params})
            if 'buy' not in bought:
                reason = 'buy rejected or unconfirmed; inspect demo portfolio'
                break
            pending = bought['buy']['contract_id']
            count += 1
            emit({'event': 'buy', 'cid': pending, 'quoted_spot': q['spot'], 'buy_price': bought['buy'].get('buy_price')})
            until = time.monotonic() + 90
            mismatch = False
            c = {}
            while time.monotonic() < until:
                time.sleep(.4)
                response = client._call({'proposal_open_contract': 1, 'contract_id': pending})
                c = response.get('proposal_open_contract', {})
                if not c:
                    raise RuntimeError('Pending contract cannot be read')
                safe = {k: v for k, v in c.items() if k not in ('account_id', 'transaction_ids')}
                emit({'event': 'contract', 'cid': pending, 'contract': safe})
                entry = c.get('entry_spot')
                if entry is not None:
                    actual_state = float(entry) * BARRIER * 1000
                    tp = c.get('limit_order', {}).get('take_profit', {}).get('order_amount')
                    parts = c.get('shortcode', '').split('_')
                    contract_ok = (len(parts) >= 8 and parts[0] == 'ACCU' and parts[1] == 'CRASH1000'
                                   and abs(float(parts[6]) - BARRIER) <= 1e-16
                                   and float(c.get('growth_rate', 0)) == .04 and float(c.get('buy_price', 0)) == 1)
                    mismatch = mismatch or not (14 <= actual_state < 14.25) or not contract_ok
                    if not c.get('is_sold'):
                        mismatch = mismatch or tp is None or abs(float(tp) - 1.19) > 1e-8
                if c.get('is_sold'):
                    if entry is None:
                        mismatch = True
                    break
                elapsed = int(c.get('current_spot_time', 0)) - int(c.get('entry_spot_time') or c.get('current_spot_time', 0))
                if mismatch or elapsed >= 22:
                    sold = client._call({'sell': pending, 'price': 0})
                    emit({'event': 'manual_close', 'cid': pending, 'spec_mismatch': mismatch,
                          'accepted': 'sell' in sold, 'reason': 'specification mismatch' if mismatch else 'take profit not confirmed by tick 22'})
                    mismatch = True
                    time.sleep(.5)
                    c = client._call({'proposal_open_contract': 1, 'contract_id': pending}).get('proposal_open_contract', {})
                    break
            if not c.get('is_sold'):
                reason = 'closure unconfirmed; inspect demo portfolio'
                break
            profit = Decimal(str(c['profit']))
            if not profit.is_finite():
                raise RuntimeError('Invalid settlement profit')
            pnl += profit
            emit({'event': 'settled', 'cid': pending, 'profit': str(profit), 'pnl': str(pnl),
                  'status': c.get('status'), 'entry_time': c.get('entry_spot_time'),
                  'exit_time': c.get('exit_spot_time'), 'spec_mismatch': mismatch,
                  'contract': {k: v for k, v in c.items() if k not in ('account_id', 'transaction_ids')}})
            pending = None
            if mismatch:
                reason = 'mechanics mismatch; audit halted for review'
                break
        emit({'event': 'summary', 'reason': reason, 'contracts': count, 'pnl': str(pnl), 'pending': pending})
    except BaseException as exc:
        emit({'event': 'halt', 'error_type': type(exc).__name__, 'contracts': count,
              'pnl': str(pnl), 'pending': pending, 'reason': 'No retry; inspect demo portfolio if pending'})
        raise SystemExit(1) from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--minutes', type=float, default=60)
    parser.add_argument('--execute', action='store_true', help='Permit bounded demo purchases inside the tested state')
    parser.add_argument('--check', action='store_true', help='Check the current state once, without purchasing')
    parser.add_argument('--out', default=None, help='New JSONL file; existing files are never overwritten')
    args = parser.parse_args()
    if not math.isfinite(args.minutes) or not 0 < args.minutes <= 360:
        parser.error('Duration must be greater than zero and no more than 360 minutes')
    token, app = os.environ.get('DERIV_TOKEN'), os.environ.get('DERIV_APP_ID')
    if not token or not app:
        parser.error('Set DERIV_TOKEN and DERIV_APP_ID securely in your environment')
    filename = args.out or 'crash1000_accu_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.jsonl'
    try:
        client = DerivWS(token=token, app_id=app, timeout=10)
    except Exception as exc:
        print(json.dumps({'event': 'initialization_failed', 'error_type': type(exc).__name__}), flush=True)
        raise SystemExit(1) from None
    with open(filename, 'x', encoding='utf-8', buffering=1) as out:
        def emit(row):
            row['local_epoch'] = time.time()
            line = json.dumps(row, allow_nan=False)
            out.write(line + '\n')
            if row['event'] != 'contract':
                print(line, flush=True)
        try:
            if client.account.get('account_type') != 'demo':
                emit({'event': 'halt', 'reason': 'Refusing non-demo account; no purchases'})
                return
            emit({'event': 'start', 'demo_execute': args.execute and not args.check,
                  'minutes': args.minutes, 'stake': 1, 'growth': .04, 'take_profit': 1.19,
                  'maximum_contracts': 20, 'maximum_realized_loss': 10, 'log': filename,
                  'warning': 'Historical candidate, not proven live profitability. This uses the Options API, not MT5.'})
            run(client, args.minutes * 60, args.execute, emit, args.check)
        finally:
            client.ws.close()


if __name__ == '__main__':
    main()
