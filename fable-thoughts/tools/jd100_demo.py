"""Demo-only, flat-stake measurement of the JD100 centered-digit rule."""
import argparse
import json
import os
import time
from decimal import Decimal

from deriv_api import DerivWS


def select(quote, pip, maximum=185):
    if pip != 2 or not Decimal('171.28') <= Decimal(str(quote)) <= Decimal(str(maximum)):
        return None
    digit = int(Decimal(str(quote)) * 100) % 10
    return {2: ('DIGITUNDER', '5'), 7: ('DIGITOVER', '4')}.get(digit)


def measure_latency(trader):
    samples = []
    for _ in range(5):
        started = time.monotonic()
        response = trader._call({'ping': 1})
        if response.get('error') or 'ping' not in response:
            raise RuntimeError('Authenticated latency check failed')
        samples.append(time.monotonic() - started)
        time.sleep(0.1)
    return max(samples)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--trade', action='store_true')
    ap.add_argument('--check-latency', action='store_true', help='Check demo connection without buying')
    ap.add_argument('--minutes', type=float, default=10)
    ap.add_argument('--stake', type=float, default=1)
    ap.add_argument('--max-loss', type=float, default=10)
    ap.add_argument('--max-trades', type=int, default=100)
    ap.add_argument('--log', default='jd100_demo.jsonl')
    a = ap.parse_args()
    if not (0 < a.minutes <= 1440 and 0.35 <= a.stake <= 10 and a.max_loss > 0 and a.max_trades > 0):
        ap.error('Invalid duration, stake ($0.35–$10), loss limit, or trade limit')
    if round(a.stake, 2) != a.stake:
        ap.error('Stake must have at most two decimal places')
    trader = None
    if a.trade or a.check_latency:
        token = os.environ.get('DERIV_TOKEN')
        app = os.environ.get('DERIV_APP_ID')
        if not token or not app:
            ap.error('Set DERIV_TOKEN and DERIV_APP_ID securely in your environment')
        trader = DerivWS(token=token, app_id=app, timeout=5)
        if trader.account.get('account_type') != 'demo':
            raise RuntimeError('Refusing non-demo account; there is no real-account override')
    public = DerivWS(token='', timeout=5)
    deadline = time.monotonic() + a.minutes * 60
    pnl = 0.0
    count = 0
    last_epoch = 0
    with open(a.log, 'a', buffering=1) as log:
        def emit(row):
            line = json.dumps(row)
            print(line, flush=True)
            log.write(line + '\n')
        emit({'event': 'start', 'demo_trade': bool(trader) and not a.check_latency, 'stake': a.stake})
        latency = None
        checked = 0
        if trader:
            latency = measure_latency(trader)
            checked = time.monotonic()
            emit({'event': 'latency', 'max_ping_rtt': latency, 'limit': 0.4,
                  'passed': latency <= 0.4, 'note': 'Ping is a screen, not a guarantee of buy latency'})
            if a.check_latency or latency > 0.4:
                emit({'event': 'summary', 'trades': 0, 'pnl': 0,
                      'reason': 'diagnostic only' if a.check_latency else 'connection too slow'})
                return
        while time.monotonic() < deadline and count < a.max_trades:
            time.sleep(0.25)
            if trader and time.monotonic() - checked > 30:
                latency = measure_latency(trader)
                checked = time.monotonic()
                if latency > 0.4:
                    emit({'event': 'halt', 'reason': 'connection too slow', 'max_ping_rtt': latency})
                    break
            if pnl - a.stake < -a.max_loss:
                emit({'event': 'halt', 'reason': 'remaining loss budget smaller than stake'})
                break
            response = public._call({'ticks_history': 'JD100', 'count': 1, 'end': 'latest', 'style': 'ticks'})
            h = response.get('history', {})
            if not h.get('times'):
                emit({'event': 'skip', 'reason': 'public history unavailable'})
                time.sleep(2)
                continue
            epoch, quote = int(h['times'][-1]), h['prices'][-1]
            pip = response.get('pip_size')
            if pip != 2:
                raise RuntimeError('Unexpected JD100 pip size')
            if epoch <= last_epoch:
                time.sleep(0.1)
                continue
            last_epoch = epoch
            contract = select(quote, pip)
            age = time.time() - epoch
            if not contract or not 0 <= age <= 0.35:
                continue
            if trader and age + latency + 0.25 >= 1:
                continue
            ct, barrier = contract
            if not trader:
                emit({'event': 'signal', 'epoch': epoch, 'quote': quote, 'contract': ct})
                continue
            params = dict(amount=a.stake, basis='stake', currency='USD', underlying_symbol='JD100',
                          contract_type=ct, barrier=barrier, duration=1, duration_unit='t')
            started = time.monotonic()
            try:
                bought = trader._call({'buy': 1, 'price': a.stake, 'parameters': params})
            except Exception:
                emit({'event': 'halt', 'reason': 'ambiguous buy; inspect demo portfolio before restarting'})
                raise
            if 'buy' not in bought:
                emit({'event': 'halt', 'reason': 'buy rejected', 'code': bought.get('error', {}).get('code')})
                break
            buy = bought['buy']
            cid = buy['contract_id']
            count += 1
            multiplier = float(buy['payout']) / float(buy['buy_price'])
            buy_rtt = time.monotonic() - started
            emit({'event': 'buy', 'cid': cid, 'epoch': epoch, 'quote': quote, 'contract': ct,
                  'multiplier': multiplier, 'rtt': buy_rtt, 'decision_age': age})
            settled = None
            until = time.monotonic() + 20
            while time.monotonic() < until:
                time.sleep(0.3)
                r = trader._call({'proposal_open_contract': 1, 'contract_id': cid})
                c = r.get('proposal_open_contract', {})
                if c.get('is_sold') and c.get('status') in ('won', 'lost'):
                    settled = c
                    break
            if settled is None:
                raise RuntimeError('Settlement unconfirmed; inspect demo portfolio before restarting')
            profit = float(settled['profit'])
            pnl += profit
            exit_time = settled.get('exit_spot_time')
            lag = int(exit_time) - epoch if exit_time is not None else None
            emit({'event': 'settled', 'cid': cid, 'profit': profit, 'pnl': pnl, 'lag': lag})
            if multiplier < 1.79 or lag != 1 or buy_rtt > 0.4:
                emit({'event': 'halt', 'reason': 'payout, settlement or buy latency guard failed',
                      'payout_ok': multiplier >= 1.79, 'lag': lag, 'buy_rtt': buy_rtt})
                break
        emit({'event': 'summary', 'trades': count, 'pnl': pnl})


if __name__ == '__main__':
    main()
