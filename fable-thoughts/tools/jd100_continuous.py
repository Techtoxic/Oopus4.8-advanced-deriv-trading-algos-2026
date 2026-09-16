"""Opt-in streamed JD100 decisions with separately polled settlements."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, InvalidOperation
import json
import math
from queue import SimpleQueue
import time

import websocket

import adaptive_sentinel_v4 as model
from crash1000_accu_audit import read_request
from deriv_api import DerivWS


QUOTE_REFRESH_SECONDS = 60
QUOTE_MAX_AGE_SECONDS = 75
PING_INTERVAL_SECONDS = 25


class Book:
    def __init__(self):
        self.pnl = Decimal('0')
        self.pending = {}
        self.count = 0
        self.unknown_buy = False
        self.unknown_stake = Decimal('0')
        self.halt = None

    def reserved(self):
        return sum((r['stake'] for r in self.pending.values()), self.unknown_stake)

    def can_buy(self, stake, max_loss, max_pending):
        return (self.halt is None and not self.unknown_buy and (max_pending == 0 or len(self.pending) < max_pending)
                and self.pnl - self.reserved() - stake >= -max_loss)

    def add(self, buy, signal, epoch, stake):
        cid = buy['contract_id']
        if type(cid) is not int or cid <= 0 or cid in self.pending:
            raise RuntimeError('Invalid or duplicate contract ID in buy response')
        self.pending[cid] = {'stake': stake, 'buy': buy, 'signal': signal, 'epoch': epoch,
                             'deadline': time.monotonic() + 20, 'last_poll': -math.inf, 'read_failed': False}
        self.count += 1
        self.unknown_buy = False
        self.unknown_stake = Decimal('0')
        try:
            price, payout = Decimal(str(buy['buy_price'])), Decimal(str(buy['payout']))
        except (KeyError, InvalidOperation):
            self.halt = 'invalid contracted stake or payout'
            return cid
        if price.is_finite() and price > stake:
            self.pending[cid]['stake'] = price
        if not price.is_finite() or not payout.is_finite() or price != stake or price <= 0:
            self.halt = 'invalid contracted stake or payout'
        elif float(payout / price) + 1e-9 < signal['assumed_payout']:
            self.halt = 'executed payout below decision assumption'
        return cid

    def settle(self, cid, c):
        row = self.pending[cid]
        if c.get('contract_id') != cid:
            raise RuntimeError('Settlement contract ID mismatch')
        if not c.get('is_sold') or c.get('status') not in ('won', 'lost'):
            return None
        profit = Decimal(str(c['profit']))
        if not profit.is_finite():
            raise RuntimeError('Invalid settlement profit')
        price = Decimal(str(row['buy']['buy_price']))
        payout = Decimal(str(row['buy']['payout']))
        if not price.is_finite() or not payout.is_finite() or price <= 0:
            raise RuntimeError('Invalid contracted payment')
        expected = payout - price if c['status'] == 'won' else -price
        end = c.get('exit_spot_time')
        lag = int(end) - row['epoch'] if end is not None else None
        self.pnl += profit
        del self.pending[cid]
        if profit != expected:
            self.halt = 'payment guard failed'
        return {'event': 'settled', 'cid': cid, 'status': c['status'], 'profit': float(profit),
                'pnl': float(self.pnl), 'lag': lag, 'executed_payout': float(payout / price),
                'timing_warning': 'settlement_not_next_tick' if lag != 1 else None,
                'buy_price': float(price), 'payout': float(payout),
                'pending_count': len(self.pending)}


class Reader:
    def __init__(self, client, events, stop):
        self.client, self.events, self.stop = client, events, stop
        self.account_id = client.account.get('account_id')

    def _call(self, payload):
        if self.account_id:
            return read_request(self.client, payload, self.events.put, self.account_id,
                                min(self.stop, time.monotonic() + 15))
        return self.client._call(payload)


def newest_tick(public, initial=None):
    latest = None

    def accept(response):
        nonlocal latest
        if response.get('error'):
            raise RuntimeError('Tick subscription failed')
        tick = response.get('tick')
        if tick:
            if tick.get('symbol') != 'JD100':
                raise RuntimeError('Unexpected feed symbol')
            if latest is None or int(tick['epoch']) >= int(latest['epoch']):
                latest = tick

    try:
        accept(initial if initial is not None else json.loads(public.ws.recv()))
    except websocket.WebSocketTimeoutException:
        return None
    public.ws.settimeout(.001)
    try:
        while True:
            accept(json.loads(public.ws.recv()))
    except websocket.WebSocketTimeoutException:
        pass
    finally:
        public.ws.settimeout(.05)
    return latest


def run_continuous(args, public, trader, emit):
    book = Book()
    events = SimpleQueue()
    control = reader = pool = future = job = None
    payouts = None
    quoted_at = 0
    deadline = time.monotonic() + args.minutes * 60
    reason = 'session complete'
    stake, max_loss = Decimal(str(args.stake)), Decimal(str(args.max_loss))
    max_age = getattr(args, 'max_age', .45)
    skipped = {'not_eligible': 0, 'stale_tick': 0, 'stale_quote': 0, 'pending_cap': 0, 'loss_budget': 0}

    def harvest():
        nonlocal future, job, payouts, quoted_at
        while not events.empty():
            emit(events.get())
        if future is None or not future.done():
            return
        completed, work = future, job
        future = job = None
        try:
            result = completed.result()
            if work[0] == 'prices':
                payouts, quoted_at = result, work[2]
            else:
                c = result.get('proposal_open_contract', {})
                update = book.settle(work[1], c)
                if update:
                    emit(update)
        except Exception as exc:
            book.halt = 'control read failed: ' + type(exc).__name__
            if work[0] == 'settlement' and work[1] in book.pending:
                book.pending[work[1]]['read_failed'] = True

    def schedule(draining=False):
        nonlocal future, job
        if future is not None or pool is None:
            return
        now = time.monotonic()
        if not draining and now - quoted_at >= QUOTE_REFRESH_SECONDS:
            job = ('prices', None, now)
            future = pool.submit(model.prices, reader, args.stake)
            return
        available = [(cid, r) for cid, r in book.pending.items() if not r['read_failed'] and now - r['last_poll'] >= .15]
        if available:
            cid, row = min(available, key=lambda item: item[1]['last_poll'])
            row['last_poll'] = now
            job = ('settlement', cid, now)
            future = pool.submit(reader._call, {'proposal_open_contract': 1, 'contract_id': cid})

    try:
        if args.check_latency:
            emit({'event': 'latency', 'max_ping_rtt': model.measure_latency(trader) if trader else None,
                  'diagnostic_only': True})
            reason = 'latency diagnostic only'
            return
        control = DerivWS(token=trader.token if trader else '', app_id=trader.app_id if trader else None, timeout=3)
        if trader and (trader.account.get('account_type') != 'demo' or not trader.account.get('account_id')
                       or control.account.get('account_type') != 'demo'
                       or control.account.get('account_id') != trader.account['account_id']):
            raise RuntimeError('Buyer and settlement connection must use the same demo account')
        reader = Reader(control, events, deadline + 40)
        payouts = model.prices(reader, args.stake)
        quoted_at = time.monotonic()
        pool = ThreadPoolExecutor(max_workers=1)
        initial = public._call({'ticks': 'JD100', 'subscribe': 1})
        public.ws.settimeout(.05)
        last_epoch = 0
        reported = -math.inf
        last_ping = time.monotonic()
        ping_rtt = None
        while time.monotonic() < deadline and book.count < args.max_trades:
            harvest()
            if book.halt:
                reason = book.halt
                break
            if any(time.monotonic() > r['deadline'] for r in book.pending.values()):
                reason = 'settlement deadline exceeded; draining known contracts'
                break
            schedule()
            if trader and time.monotonic() - last_ping >= PING_INTERVAL_SECONDS:
                started = time.monotonic()
                response = trader._call({'ping': 1})
                ping_rtt = time.monotonic() - started
                last_ping = time.monotonic()
                if 'ping' not in response:
                    reason = 'buyer heartbeat failed'
                    break
            tick = newest_tick(public, initial)
            initial = None
            if tick is None:
                continue
            epoch, spot = int(tick['epoch']), float(tick['quote'])
            signal = model.choose(spot, tick.get('pip_size'), payouts, args.ev_gate)
            if time.monotonic() - reported >= 30:
                emit({'event': 'status', 'continuous': True, 'spot': spot, 'model_only': True,
                      'pnl': float(book.pnl), 'trades': book.count, 'pending_count': len(book.pending),
                      'ping_rtt': ping_rtt,
                      'skipped': dict(skipped),
                      'reserved_stake': float(book.reserved()),
                      'gates': [{'contract': ct, 'barrier': bar, 'assumed_payout': m,
                                'model_spot_gate': model.threshold(m, args.ev_gate)} for (ct, bar), m in payouts.items()]})
                reported = time.monotonic()
            if epoch <= last_epoch:
                continue
            last_epoch = epoch
            age = time.time() - epoch
            if signal is None:
                skipped['not_eligible'] += 1
                continue
            if not 0 <= age <= max_age:
                skipped['stale_tick'] += 1
                continue
            if time.monotonic() - quoted_at > QUOTE_MAX_AGE_SECONDS:
                skipped['stale_quote'] += 1
                continue
            if not trader:
                emit({'event': 'signal', 'epoch': epoch, 'spot': spot, **signal})
                continue
            if not book.can_buy(stake, max_loss, args.max_pending):
                skipped['pending_cap' if args.max_pending and len(book.pending) >= args.max_pending else 'loss_budget'] += 1
                if not book.pending:
                    reason = 'remaining loss budget smaller than stake'
                    break
                continue
            harvest()
            if book.halt or time.monotonic() >= deadline or not book.can_buy(stake, max_loss, args.max_pending):
                continue
            signal = model.choose(spot, tick.get('pip_size'), payouts, args.ev_gate)
            if signal is None:
                skipped['not_eligible'] += 1
                continue
            age = time.time() - epoch
            if not 0 <= age <= max_age:
                skipped['stale_tick'] += 1
                continue
            params = dict(amount=args.stake, basis='stake', currency='USD', underlying_symbol='JD100',
                          contract_type=signal['contract'], barrier=signal['barrier'], duration=1, duration_unit='t')
            book.unknown_buy = True
            book.unknown_stake = stake
            started = time.monotonic()
            response = trader._call({'buy': 1, 'price': args.stake, 'parameters': params})
            rtt = time.monotonic() - started
            if 'buy' not in response:
                reason = 'buy rejected or unconfirmed; inspect demo portfolio'
                break
            cid = book.add(response['buy'], signal, epoch, stake)
            emit({'event': 'buy', 'cid': cid, 'epoch': epoch, 'spot': spot, 'stake': args.stake,
                  'rtt': rtt, 'decision_age': age, 'pending_count': len(book.pending),
                  'reserved_stake': float(book.reserved()), **signal})
    except KeyboardInterrupt:
        reason = 'interrupted; draining known contracts'
    except Exception as exc:
        reason = 'error: ' + type(exc).__name__ + '; inspect any unresolved contracts'
    finally:
        if pool is not None:
            drain_until = time.monotonic() + 20
            reader.stop = drain_until
            try:
                while book.pending and time.monotonic() < drain_until:
                    harvest()
                    schedule(draining=True)
                    if future is None and all(r['read_failed'] for r in book.pending.values()):
                        break
                    time.sleep(.02)
            except KeyboardInterrupt:
                reason = 'drain interrupted; inspect remaining contracts'
            pool.shutdown(wait=True, cancel_futures=True)
            harvest()
        if control is not None:
            try:
                control.ws.close()
            except Exception as exc:
                emit({'event': 'close_error', 'error_type': type(exc).__name__})
        emit({'event': 'summary', 'continuous': True, 'reason': book.halt or reason,
              'skipped': dict(skipped),
              'trades': book.count, 'pnl': float(book.pnl), 'pending_contracts': list(book.pending),
              'unknown_buy_outcome': book.unknown_buy, 'reserved_stake': float(book.reserved())})
