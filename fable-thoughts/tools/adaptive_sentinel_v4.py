"""Payout-aware JD100 research runner. Watch-only by default; real trading requires --real --trade."""
import argparse
from datetime import datetime, timezone
from decimal import Decimal
import json
import math
import os
from pathlib import Path
import time

from deriv_api import DerivWS, DEFAULT_ACCOUNT_TYPE
from jd100_demo import measure_latency
from sigma_model import wrapped_normal_pmf


SIGMA_A = .446
SIGMA_B = .0153254
REFERENCE_PAYOUT = 1.56
CONTRACTS = [('DIGITUNDER', '5'), ('DIGITOVER', '4')]


def probability(spot):
    return sum(wrapped_normal_pmf(SIGMA_A + SIGMA_B * spot, 2)[:5])


def threshold(payout, gate):
    if payout <= 1 + gate:
        return None
    low, high = 0.0, 1000.0
    for _ in range(60):
        mid = (low + high) / 2
        if probability(mid) * payout - 1 >= gate:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def choose(spot, pip, payouts, gate):
    value = Decimal(str(spot))
    if pip != 2 or not value.is_finite() or value <= 0:
        raise ValueError('Invalid JD100 price or pip size')
    scaled = value * 100
    if scaled != scaled.to_integral_value():
        raise ValueError('Quote is inconsistent with pip size')
    digit = int(scaled) % 10
    contract = {2: CONTRACTS[0], 7: CONTRACTS[1]}.get(digit)
    if contract is None:
        return None
    p = probability(float(value))
    payout = payouts[contract]
    ev = p * payout - 1
    if ev < gate:
        return None
    return {'contract': contract[0], 'barrier': contract[1], 'digit': digit,
            'model_probability': p, 'model_ev': ev, 'assumed_payout': payout}


def prices(client, stake):
    result = {}
    for ct, bar in CONTRACTS:
        r = client._call({'proposal': 1, 'amount': stake, 'basis': 'stake',
                          'contract_type': ct, 'barrier': bar, 'duration': 1,
                          'duration_unit': 't', 'currency': 'USD', 'underlying_symbol': 'JD100'})
        q = r.get('proposal', {})
        ask, payout = float(q.get('ask_price', 0)), float(q.get('payout', 0))
        if not all(map(math.isfinite, [ask, payout])) or ask <= 0 or payout <= ask:
            raise RuntimeError('Missing or invalid payout quote; no trades allowed')
        if abs(ask - stake) > .000001:
            raise RuntimeError('Proposal stake mismatch')
        result[(ct, bar)] = min(REFERENCE_PAYOUT, payout / ask)
    return result


def run(args, public, trader, emit):
    if getattr(args, 'continuous', False):
        from jd100_continuous import run_continuous
        return run_continuous(args, public, trader, emit)
    pnl = Decimal('0')
    count = 0
    pending = None
    reason = 'session complete'
    deadline = time.monotonic() + args.minutes * 60
    try:
        if args.check_latency:
            emit({'event': 'latency', 'max_ping_rtt': measure_latency(trader) if trader else None,
                  'diagnostic_only': True})
            reason = 'latency diagnostic only'
            return
        checked = time.monotonic()
        payouts = prices(trader or public, args.stake)
        quoted = time.monotonic()
        last_epoch = 0
        reported = -math.inf
        while time.monotonic() < deadline and count < args.max_trades:
            if trader and pnl - Decimal(str(args.stake)) < -Decimal(str(args.max_loss)):
                reason = 'remaining loss budget smaller than stake'
                break
            if trader and time.monotonic() - checked >= 30:
                response = trader._call({'ping': 1})
                checked = time.monotonic()
                if 'ping' not in response:
                    reason = 'buyer heartbeat failed'
                    break
            if time.monotonic() - quoted >= 60:
                payouts = prices(trader or public, args.stake)
                quoted = time.monotonic()
            response = public._call({'ticks_history': 'JD100', 'count': 1,
                                     'end': 'latest', 'style': 'ticks'})
            h = response.get('history', {})
            if not h.get('times') or not h.get('prices'):
                emit({'event': 'skip', 'reason': 'public history unavailable'})
                time.sleep(2)
                continue
            epoch, spot = int(h['times'][-1]), float(h['prices'][-1])
            signal = choose(spot, response.get('pip_size'), payouts, args.ev_gate)
            if time.monotonic() - reported >= 30:
                emit({'event': 'status', 'spot': spot, 'sigma': SIGMA_A + SIGMA_B * spot,
                      'model_only': True, 'gates': [{'contract': ct, 'barrier': bar,
                      'assumed_payout': payout, 'model_spot_gate': threshold(payout, args.ev_gate)}
                      for (ct, bar), payout in payouts.items()], 'pnl': float(pnl), 'trades': count})
                reported = time.monotonic()
            if epoch <= last_epoch:
                time.sleep(.25)
                continue
            last_epoch = epoch
            age = time.time() - epoch
            if signal is None or not 0 <= age <= getattr(args, 'max_age', .45):
                time.sleep(.25)
                continue
            if not trader:
                emit({'event': 'signal', 'epoch': epoch, 'spot': spot, **signal})
                time.sleep(.25)
                continue
            params = dict(amount=args.stake, basis='stake', currency='USD', underlying_symbol='JD100',
                          contract_type=signal['contract'], barrier=signal['barrier'],
                          duration=1, duration_unit='t')
            pending = 'buy outcome unknown'
            started = time.monotonic()
            response = trader._call({'buy': 1, 'price': args.stake, 'parameters': params})
            rtt = time.monotonic() - started
            if 'buy' not in response:
                reason = 'buy rejected or response unconfirmed; inspect the selected account portfolio'
                break
            buy = response['buy']
            pending = buy['contract_id']
            count += 1
            emit({'event': 'buy', 'cid': pending, 'epoch': epoch, 'spot': spot,
                  'stake': args.stake, 'rtt': rtt, 'decision_age': age, **signal})
            until = time.monotonic() + 20
            settled = None
            while time.monotonic() < until:
                time.sleep(.3)
                response = trader._call({'proposal_open_contract': 1, 'contract_id': pending})
                c = response.get('proposal_open_contract', {})
                if c.get('is_sold') and c.get('status') in ('won', 'lost'):
                    settled = c
                    break
            if settled is None:
                reason = 'settlement unconfirmed; inspect the selected account portfolio'
                break
            profit = Decimal(str(settled['profit']))
            if not profit.is_finite():
                raise ValueError('Invalid settlement profit')
            pnl += profit
            cid, pending = pending, None
            buy_price = float(buy['buy_price'])
            actual = float(buy['payout']) / buy_price
            exit_time = settled.get('exit_spot_time')
            lag = int(exit_time) - epoch if exit_time is not None else None
            emit({'event': 'settled', 'cid': cid, 'status': settled['status'], 'profit': float(profit),
                  'pnl': float(pnl), 'lag': lag, 'executed_payout': actual,
                  'timing_warning': 'settlement_not_next_tick' if lag != 1 else None})
            if not math.isfinite(actual) or actual + 1e-9 < signal['assumed_payout']:
                reason = 'executed payout below decision assumption'
                break
            if abs(buy_price - args.stake) > .000001:
                reason = 'stake guard failed'
                break
            time.sleep(.25)
    except KeyboardInterrupt:
        reason = 'interrupted; inspect the selected account portfolio if a purchase is pending'
    except Exception as exc:
        reason = 'error: ' + type(exc).__name__ + '; inspect the selected account portfolio if a purchase is pending'
    finally:
        emit({'event': 'summary', 'reason': reason, 'trades': count, 'pnl': float(pnl),
              'pending_contract': pending})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trade', action='store_true')
    parser.add_argument('--real', action='store_true', help='Force the real account; otherwise TRADE_DEMO in deriv_api.py decides')
    parser.add_argument('--check-latency', action='store_true')
    parser.add_argument('--continuous', action='store_true', help='Stream ticks and reconcile settlements on a separate connection')
    parser.add_argument('--max-pending', type=int, default=0, help='Continuous-mode pending-contract cap, 0 disables the count cap')
    parser.add_argument('--max-age', type=float, default=.45, help='Maximum tick age in seconds, matching v2/v3 by default')
    parser.add_argument('--stake', type=float, default=1)
    parser.add_argument('--minutes', type=float, default=10)
    parser.add_argument('--max-loss', type=float, default=10)
    parser.add_argument('--max-trades', type=int, default=100)
    parser.add_argument('--ev-gate', type=float, default=.01)
    parser.add_argument('--log', default=None)
    args = parser.parse_args()
    if not (math.isfinite(args.stake) and .35 <= args.stake <= 10 and round(args.stake, 2) == args.stake
            and math.isfinite(args.minutes) and 0 < args.minutes <= 1440
            and math.isfinite(args.max_loss) and args.max_loss > 0 and args.max_trades > 0
            and math.isfinite(args.ev_gate) and .01 <= args.ev_gate <= .1 and 0 <= args.max_pending <= 10
            and math.isfinite(args.max_age) and 0 < args.max_age <= 1):
        parser.error('Use a $0.35–$10 cent-rounded stake, positive limits, EV gate .01–.10, max-pending 0–10, and max-age in (0, 1]')
    if args.real and not (args.trade or args.check_latency):
        parser.error('--real requires --trade or --check-latency')
    account_type = 'real' if args.real else DEFAULT_ACCOUNT_TYPE
    args.real = account_type == 'real'
    token, app = os.environ.get('DERIV_TOKEN'), os.environ.get('DERIV_APP_ID')
    if (args.trade or args.check_latency) and (not token or not app):
        parser.error('Configure DERIV_TOKEN and DERIV_APP_ID securely in the environment')
    filename = args.log or 'jd100_v4_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.jsonl'
    public = trader = None
    stage = 'account_connection'
    with Path(filename).open('x', encoding='utf-8', buffering=1) as log:
        def emit(row):
            row = {'utc': datetime.now(timezone.utc).isoformat(), **row}
            line = json.dumps(row, allow_nan=False)
            print(line, flush=True)
            log.write(line + '\n')
        try:
            if args.trade or args.check_latency:
                trader = DerivWS(token=token, app_id=app, timeout=5, account_type=account_type)
                if trader.account.get('account_type') != account_type or trader.account.get('currency') != 'USD':
                    emit({'event': 'halt', 'reason': 'requested account type and USD currency are required'})
                    return
                balance = trader.account.get('balance')
                if args.trade and not args.check_latency and balance is not None:
                    balance = Decimal(str(balance))
                    if not balance.is_finite() or balance < Decimal(str(args.stake)):
                        emit({'event': 'halt', 'reason': 'insufficient or invalid account balance for the requested stake',
                              'account_type': account_type, 'stake': args.stake})
                        return
            stage = 'public_connection'
            public = DerivWS(token='', timeout=5)
            emit({'event': 'start', 'demo_trade': bool(trader) and not args.check_latency and not args.real,
                  'real_trade': bool(trader) and not args.check_latency and args.real,
                  'account_type': trader.account.get('account_type') if trader else None,
                  'continuous': args.continuous, 'max_pending': args.max_pending if args.continuous else 1,
                  'max_age': args.max_age,
                  'stake': args.stake, 'ev_gate': args.ev_gate, 'log': filename,
                  'warning': 'Model extrapolation, not a verified profitable regime; proposals can overstate fills'})
            stage = 'runner_import_or_run'
            run(args, public, trader, emit)
        except Exception as exc:
            emit({'event': 'halt', 'reason': 'initialization failed', 'error_type': type(exc).__name__,
                  'stage': stage, 'error_file': Path(exc.filename).name if isinstance(exc, SyntaxError) and exc.filename else None,
                  'error_line': exc.lineno if isinstance(exc, SyntaxError) else None})
        finally:
            for client in (public, trader):
                if client is not None:
                    client.ws.close()


if __name__ == '__main__':
    main()
