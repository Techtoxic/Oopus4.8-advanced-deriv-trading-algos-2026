"""Bounded CRASH1000 candidate-state demo audit. No real-account override."""
import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_UP
import json
import math
import os
import time

import websocket

from deriv_api import DerivWS


BARRIERS = {'CRASH1000': 2.3454e-6, 'CRASH500': 4.7141e-6}
BARRIER = BARRIERS['CRASH1000']
TRANSPORT_ERRORS = (websocket.WebSocketException, ConnectionError, TimeoutError, OSError)


def read_request(client, payload, emit, account_id, deadline, account_type='demo'):
    if account_type not in ('demo', 'real'):
        raise ValueError('Unsupported account type')
    fields = {
        'ticks_history': {'ticks_history', 'count', 'end', 'style'},
        'proposal': {'proposal', 'amount', 'basis', 'contract_type', 'currency',
                     'underlying_symbol', 'growth_rate', 'limit_order', 'barrier', 'duration', 'duration_unit'},
        'proposal_open_contract': {'proposal_open_contract', 'contract_id'},
    }
    kinds = [kind for kind, allowed in fields.items() if kind in payload and set(payload) <= allowed]
    if len(kinds) != 1:
        raise ValueError('Only explicitly supported read requests may be retried')
    reconnect = False
    for attempt in range(4):
        if time.monotonic() >= deadline:
            raise TimeoutError('Read recovery deadline reached')
        if client.account.get('account_type') != account_type or client.account.get('account_id') != account_id:
            raise RuntimeError('Account changed; refusing to continue')
        try:
            if reconnect:
                old = getattr(client, 'ws', None)
                if old is not None:
                    try:
                        old.close()
                    except TRANSPORT_ERRORS:
                        pass
                client._connect()
                if client.account.get('account_type') != account_type or client.account.get('account_id') != account_id:
                    raise RuntimeError('Reconnected to a different account; refusing to continue')
                reconnect = False
            if time.monotonic() >= deadline:
                raise TimeoutError('Read recovery deadline reached')
            response = client._call(payload)
        except TRANSPORT_ERRORS as exc:
            reconnect = True
            error = type(exc).__name__
        else:
            if (response.get('error') or {}).get('code') != 'RateLimit':
                if attempt:
                    emit({'event': 'read_recovered', 'request': kinds[0], 'attempt': attempt + 1,
                          'cid': payload.get('contract_id'), 'same_demo_account': account_type == 'demo',
                          'same_account': True, 'account_type': account_type})
                return response
            error = 'RateLimit'
        if attempt == 3:
            raise RuntimeError('Read recovery attempts exhausted')
        delay = min(2 ** attempt, max(0, deadline - time.monotonic()))
        emit({'event': 'read_retry', 'request': kinds[0], 'attempt': attempt + 1,
              'cid': payload.get('contract_id'), 'error_type': error, 'delay_seconds': delay})
        time.sleep(delay)
    raise RuntimeError('Read recovery exhausted')


def money(value):
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount <= 0 or amount != amount.quantize(Decimal('.01')):
            raise ValueError
        return amount
    except (InvalidOperation, ValueError):
        raise argparse.ArgumentTypeError('Use a finite positive amount with at most two decimal places') from None


def payout_terms(stake):
    raw_value = stake * Decimal('1.04') ** 20
    target = (raw_value - stake).quantize(Decimal('.01'), rounding=ROUND_DOWN)
    sale = raw_value.quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
    return target, sale


def reconcile_path(contract, stake=Decimal('1'), symbol='CRASH1000'):
    try:
        barrier = BARRIERS[symbol]
        parts = contract.get('shortcode', '').split('_')
        if (len(parts) < 8 or parts[0] != 'ACCU' or parts[1] != symbol
                or Decimal(parts[6]) != Decimal(str(barrier))):
            return {'matched': False, 'reason': 'contract symbol or barrier mismatch'}
        entry = int(contract['entry_spot_time'])
        end = int(contract['exit_spot_time'])
        if not entry <= end <= entry + 65:
            raise ValueError('Invalid audit duration')
        ticks = {}
        for tick in contract.get('audit_details', {}).get('all_ticks', []):
            epoch = int(tick['epoch'])
            if not entry <= epoch <= end:
                continue
            value = Decimal(str(tick['tick']))
            if not value.is_finite() or value <= 0 or (epoch in ticks and ticks[epoch] != value):
                raise ValueError('Invalid audit price')
            ticks[epoch] = value
        if sorted(ticks) != list(range(entry, end + 1)):
            raise ValueError('Incomplete audit path')
        first_breach = None
        for epoch in range(entry + 1, end + 1):
            move = abs(ticks[epoch] - ticks[epoch - 1])
            limit = Decimal(str(barrier)) * ticks[epoch - 1]
            if move >= limit:
                first_breach = {'epoch': epoch, 'previous': str(ticks[epoch - 1]),
                                'next': str(ticks[epoch]), 'move': str(move), 'limit': str(limit)}
                break
        sale = Decimal(str(contract['sell_price']))
        profit = Decimal(str(contract['profit']))
        _, expected_sale = payout_terms(stake)
        if contract.get('status') == 'lost':
            matched = bool(first_breach and first_breach['epoch'] == end and end - entry <= 20
                           and sale == 0 and profit == -stake)
        elif contract.get('status') == 'won':
            matched = not first_breach and end - entry == 20 and sale == expected_sale and profit == expected_sale - stake
        else:
            matched = False
        return {'matched': matched, 'protected_ticks': end - entry, 'first_model_breach': first_breach,
                'reason': 'path and payment match' if matched else 'outcome, target timing or payment disagrees with model'}
    except (KeyError, ValueError, TypeError, ArithmeticError):
        return {'matched': None, 'reason': 'audit path or terminal fields unavailable; cannot verify'}


def eligible(q, pip, stake=Decimal('1'), take_profit=Decimal('1.19'), symbol='CRASH1000'):
    details = q.get('contract_details', {})
    b = float(details.get('tick_size_barrier', 0))
    if pip != 3 or not math.isfinite(b) or abs(b - BARRIERS[symbol]) > 1e-16 or int(details.get('maximum_ticks', 0)) < 20:
        raise RuntimeError('Contract specification changed')
    if Decimal(str(q.get('ask_price', 0))) != stake:
        raise RuntimeError('Unexpected quoted stake')
    tp = q.get('limit_order', {}).get('take_profit', {}).get('order_amount')
    if tp is None or Decimal(str(tp)) != take_profit:
        raise RuntimeError('Take-profit specification missing')
    phase = float(q['spot']) * b * 1000
    if not math.isfinite(phase) or phase <= 0:
        raise RuntimeError('Invalid quote spot')
    return 14 <= phase < 14.25


def run(client, seconds, execute, emit, check_only=False, stake=Decimal('1'), max_loss=Decimal('10'), symbol='CRASH1000'):
    if client.account.get('account_type') != 'demo' or not client.account.get('account_id'):
        raise RuntimeError('Refusing non-demo account')
    account_id = client.account['account_id']
    barrier = BARRIERS[symbol]
    deadline = time.monotonic() + seconds
    pnl = Decimal('0')
    count = 0
    pending = None
    last_report = -float('inf')
    reason = 'time limit reached'
    take_profit, _ = payout_terms(stake)
    params = dict(amount=float(stake), basis='stake', contract_type='ACCU', currency='USD',
                  underlying_symbol=symbol, growth_rate=.04, limit_order={'take_profit': float(take_profit)})
    try:
        while time.monotonic() < deadline:
            if execute and not check_only and pnl - stake < -max_loss:
                reason = 'remaining session loss budget smaller than stake'
                break
            h = read_request(client, {'ticks_history': symbol, 'count': 1, 'end': 'latest', 'style': 'ticks'},
                             emit, account_id, deadline)
            if time.monotonic() >= deadline:
                break
            r = read_request(client, {'proposal': 1, **params}, emit, account_id, deadline)
            q = r.get('proposal', {})
            if not q:
                raise RuntimeError('Quote unavailable')
            ready = eligible(q, h.get('pip_size'), stake, take_profit, symbol)
            age = time.time() - int(q['spot_time'])
            if time.monotonic() - last_report >= 60 or ready:
                emit({'event': 'state', 'symbol': symbol, 'spot': q['spot'], 'spot_time': q['spot_time'],
                      'barrier': barrier, 'candidate': ready, 'quote_age': age,
                      'fresh_quote': 0 <= age <= 2,
                      'minimum_spot': 14 / (barrier * 1000),
                      'maximum_spot_exclusive': 14.25 / (barrier * 1000)})
                last_report = time.monotonic()
            if check_only:
                reason = 'check only; no purchases'
                break
            if not execute or not ready or not 0 <= age <= 2:
                time.sleep(min(10, max(0, deadline - time.monotonic())))
                continue
            if time.monotonic() >= deadline:
                break
            pending = 'buy outcome unknown'
            bought = client._call({'buy': 1, 'price': float(stake), 'parameters': params})
            if 'buy' not in bought:
                reason = 'buy rejected or unconfirmed; inspect demo portfolio'
                break
            pending = bought['buy']['contract_id']
            count += 1
            emit({'event': 'buy', 'symbol': symbol, 'cid': pending, 'quoted_spot': q['spot'], 'buy_price': bought['buy'].get('buy_price')})
            until = time.monotonic() + 90
            mismatch = False
            close_attempted = False
            c = {}
            while time.monotonic() < until:
                time.sleep(.4)
                if time.monotonic() >= until:
                    break
                response = read_request(client, {'proposal_open_contract': 1, 'contract_id': pending},
                                        emit, account_id, until)
                c = response.get('proposal_open_contract', {})
                if not c:
                    raise RuntimeError('Pending contract cannot be read')
                safe = {k: v for k, v in c.items() if k not in ('account_id', 'transaction_ids')}
                emit({'event': 'contract', 'cid': pending, 'contract': safe})
                entry = c.get('entry_spot')
                if entry is not None:
                    actual_state = float(entry) * barrier * 1000
                    tp = c.get('limit_order', {}).get('take_profit', {}).get('order_amount')
                    parts = c.get('shortcode', '').split('_')
                    contract_ok = (len(parts) >= 8 and parts[0] == 'ACCU' and parts[1] == symbol
                                   and abs(float(parts[6]) - barrier) <= 1e-16
                                   and float(c.get('growth_rate', 0)) == .04 and Decimal(str(c.get('buy_price', 0))) == stake)
                    mismatch = mismatch or not (14 <= actual_state < 14.25) or not contract_ok
                    if not c.get('is_sold') and not c.get('exit_spot_time'):
                        mismatch = mismatch or tp is None or Decimal(str(tp)) != take_profit
                if c.get('is_sold'):
                    if entry is None:
                        mismatch = True
                    break
                endpoint = c.get('exit_spot_time') or c.get('current_spot_time', 0)
                elapsed = int(endpoint) - int(c.get('entry_spot_time') or endpoint)
                if (mismatch or elapsed >= 22) and not close_attempted:
                    close_attempted = True
                    try:
                        sold = client._call({'sell': pending, 'price': 0})
                    except TRANSPORT_ERRORS as exc:
                        emit({'event': 'close_result_unknown', 'cid': pending, 'error_type': type(exc).__name__,
                              'reason': 'Close will not be retried; reconciling the known contract'})
                        continue
                    emit({'event': 'manual_close', 'cid': pending, 'spec_mismatch': mismatch,
                          'accepted': 'sell' in sold, 'error_code': (sold.get('error') or {}).get('code'),
                          'reason': 'specification mismatch' if mismatch else 'take profit not confirmed by tick 22'})
            if not c.get('is_sold'):
                reason = 'closure unconfirmed; inspect demo portfolio'
                break
            profit = Decimal(str(c['profit']))
            if not profit.is_finite():
                raise RuntimeError('Invalid settlement profit')
            pnl += profit
            path_check = (reconcile_path(c, stake, symbol) if not mismatch else
                          {'matched': None, 'reason': 'not assessed because execution specification mismatched'})
            emit({'event': 'settled', 'symbol': symbol, 'cid': pending, 'profit': str(profit), 'pnl': str(pnl),
                  'status': c.get('status'), 'entry_time': c.get('entry_spot_time'),
                  'exit_time': c.get('exit_spot_time'), 'spec_mismatch': mismatch,
                  'close_attempted': close_attempted,
                  'path_check': path_check,
                  'contract': {k: v for k, v in c.items() if k not in ('account_id', 'transaction_ids')}})
            pending = None
            if mismatch:
                reason = 'mechanics mismatch; audit halted for review'
                break
            if path_check['matched'] is not True:
                reason = 'path model mismatch or unavailable audit; halted for review'
                break
        emit({'event': 'summary', 'symbol': symbol, 'reason': reason, 'contracts': count, 'pnl': str(pnl), 'pending': pending})
    except BaseException as exc:
        emit({'event': 'halt', 'symbol': symbol, 'error_type': type(exc).__name__, 'contracts': count,
              'pnl': str(pnl), 'pending': pending,
              'reason': 'Could not safely continue; buy requests are never retried; inspect demo portfolio if pending'})
        raise SystemExit(1) from None


def main(symbol='CRASH1000'):
    if symbol not in BARRIERS:
        raise ValueError('Unsupported candidate configuration')
    parser = argparse.ArgumentParser(description=f'{symbol} candidate-state accumulator demo audit; no real-account override')
    parser.add_argument('--minutes', type=float, default=60)
    parser.add_argument('--stake', type=money, default=Decimal('1'), help='Fixed stake in USD, at least $1; broker limits still apply')
    parser.add_argument('--max-loss', type=money, default=Decimal('10'), help='Maximum net realized loss for this session in USD')
    parser.add_argument('--execute', action='store_true', help='Permit bounded demo purchases inside the tested state')
    parser.add_argument('--check', action='store_true', help='Check the current state once, without purchasing')
    parser.add_argument('--out', default=None, help='New JSONL file; existing files are never overwritten')
    args = parser.parse_args()
    if not math.isfinite(args.minutes) or not 0 < args.minutes <= 360:
        parser.error('Duration must be greater than zero and no more than 360 minutes')
    if args.stake < 1:
        parser.error('Stake must be at least $1; the broker also validates its limits')
    token, app = os.environ.get('DERIV_TOKEN'), os.environ.get('DERIV_APP_ID')
    if not token or not app:
        parser.error('Set DERIV_TOKEN and DERIV_APP_ID securely in your environment')
    filename = args.out or symbol.lower() + '_accu_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.jsonl'
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
                print(json.dumps({k: v for k, v in row.items() if k != 'contract'}, allow_nan=False), flush=True)
        try:
            if client.account.get('account_type') != 'demo':
                emit({'event': 'halt', 'reason': 'Refusing non-demo account; no purchases'})
                return
            emit({'event': 'start', 'demo_execute': args.execute and not args.check,
                  'symbol': symbol,
                  'minutes': args.minutes, 'stake': str(args.stake), 'growth': .04,
                  'take_profit': str(payout_terms(args.stake)[0]), 'maximum_contracts': None,
                  'maximum_realized_loss': str(args.max_loss), 'log': filename,
                  'warning': 'Historical candidate, not proven live profitability. This uses the Options API, not MT5.'})
            run(client, args.minutes * 60, args.execute, emit, args.check, args.stake, args.max_loss, symbol)
        finally:
            client.ws.close()


if __name__ == '__main__':
    main()
