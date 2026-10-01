"""exotic_sweep.py — authenticated proposal sweep of every contract family the
repo never priced. Read-only. Proposals only, no orders. Quoting MUST be
authenticated: the public tier serves a different book (JD100 digits cut,
Crash ACCU barriers 1.3-5.9% tighter).

Run: python3 exotic_sweep.py --symbol 1HZ100V --out ../results/exotic_1HZ100V.jsonl
     python3 exotic_sweep.py --symbol RDBULL --out ../results/exotic_RDBULL.jsonl
"""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from deriv_api import DerivWS

BASE_MATRIX = {
    'CALL': [{}],
    'PUT': [{}],
    'CALLE': [{'barrier': 'ABS_UP1'}, {'barrier': 'ABS_UP2'}],
    'PUTE': [{'barrier': 'ABS_DN1'}, {'barrier': 'ABS_DN2'}],
    'HIGHER': [{'barrier': 'ABS_UP1'}],
    'LOWER': [{'barrier': 'ABS_DN1'}],
    'ONETOUCH': [{'barrier': 'ABS_UP2'}, {'barrier': 'ABS_DN2'}],
    'NOTOUCH': [{'barrier': 'ABS_UP2'}, {'barrier': 'ABS_DN2'}],
    'EXPIRYRANGE': [{'low': 'ABS_DN1', 'high': 'ABS_UP1'}],
    'EXPIRYMISS': [{'low': 'ABS_DN1', 'high': 'ABS_UP1'}],
    'RANGE': [{'low': 'ABS_DN1', 'high': 'ABS_UP1'}],
    'UPORDOWN': [{'low': 'ABS_DN1', 'high': 'ABS_UP1'}],
    'TICKHIGH': [{'barrier': 'ABS_UP05'}],
    'TICKLOW': [{'barrier': 'ABS_DN05'}],
    'RUNHIGH': [{'barrier': 'ABS_UP05'}],
    'RUNLOW': [{'barrier': 'ABS_DN05'}],
    'RESETCALL': [{'barrier': 'ABS_UP1'}],
    'RESETPUT': [{'barrier': 'ABS_DN1'}],
    'ASIAND': [{}],
    'ASIANU': [{}],
    'TURBOSLONG': [{'barrier': 'ABS_DN2'}],
    'TURBOSSHORT': [{'barrier': 'ABS_UP2'}],
    'VANILLALONGCALL': [{'barrier': 'ABS_UP1'}],
    'VANILLALONGPUT': [{'barrier': 'ABS_DN1'}],
}

DURATIONS = [
    {'duration': 5, 'duration_unit': 't'},
    {'duration': 10, 'duration_unit': 't'},
    {'duration': 1, 'duration_unit': 'm'},
    {'duration': 5, 'duration_unit': 'm'},
    {'duration': 1, 'duration_unit': 'h'},
]


def spot_of(ws, symbol):
    r = ws.call({'ticks_history': symbol, 'count': 1, 'end': 'latest',
                 'style': 'ticks'})
    h = r.get('history', {}) or {}
    if not h.get('prices'):
        return None, None
    return float(h['prices'][-1]), h['times'][-1]


def fill(params, spot, dec):
    out = dict(params)
    rel = {'ABS_UP05': 0.005, 'ABS_UP1': 0.01, 'ABS_UP2': 0.02,
           'ABS_DN05': -0.005, 'ABS_DN1': -0.01, 'ABS_DN2': -0.02}
    for k, v in list(out.items()):
        if isinstance(v, str) and v in rel:
            out[k] = f"{spot * (1 + rel[v]):.{dec}f}"
    if 'high' in out:
        out['high_barrier'] = out.pop('high')
    if 'low' in out:
        out['low_barrier'] = out.pop('low')
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--symbol', default='1HZ100V')
    ap.add_argument('--families', nargs='*', default=sorted(BASE_MATRIX))
    ap.add_argument('--stake', type=float, default=1.0)
    ap.add_argument('--out', default='../results/exotic_sweep.jsonl')
    ap.add_argument('--dec', type=int, default=2)
    a = ap.parse_args()

    ws = DerivWS()
    acct = getattr(ws, 'account', {}) or {}
    print(f"[*] session {acct.get('account_id')} type {acct.get('account_type')}")

    spot, epoch = spot_of(ws, a.symbol)
    if spot is None:
        print('[ERROR] no spot')
        return
    print(f"[*] {a.symbol} spot {spot} epoch {epoch}")

    n_ok = n_err = 0
    with open(a.out, 'w') as f:
        for fam in a.families:
            for bp in BASE_MATRIX.get(fam, [{}]):
                for dur in DURATIONS:
                    params = dict(amount=a.stake, basis='stake', currency='USD',
                                  underlying_symbol=a.symbol,
                                  contract_type=fam,
                                  **fill(bp, spot, a.dec), **dur)
                    r = ws.call({'proposal': 1, **params})
                    p = r.get('proposal')
                    if p:
                        n_ok += 1
                        rec = {'ok': True, 'family': fam, 'symbol': a.symbol,
                               'params': params, 'payout': p.get('payout'),
                               'ask': p.get('ask_price'), 'spot': p.get('spot'),
                               'spot_time': p.get('spot_time'),
                               'details': p.get('contract_details', {})}
                        print(f"  OK   {fam:<16} dur={dur['duration']}"
                              f"{dur['duration_unit']:<2}"
                              f" payout={p.get('payout')} ask={p.get('ask_price')}")
                    else:
                        n_err += 1
                        rec = {'ok': False, 'family': fam, 'symbol': a.symbol,
                               'params': params,
                               'error': (r.get('error', {}) or {}).get('message')}
                    f.write(json.dumps(rec) + '\n')
                    time.sleep(0.15)
    ws.close()
    print(f"\n[*] accepted {n_ok}, rejected {n_err} -> {a.out}")


if __name__ == '__main__':
    main()
