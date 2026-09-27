"""Paper forward test for the frozen HMA crypto rule. Public market data only; places no orders."""
import argparse
import csv
import hashlib
import json
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from signals import prepare

HERE = Path(__file__).resolve().parent
PREREG = json.loads((HERE / 'prereg.json').read_text())
PREREG_HASH = hashlib.sha256((HERE / 'prereg.json').read_bytes()).hexdigest()
P = PREREG['params']
FEE = PREREG['fee_bps_per_side'] / 1e4
BAR = pd.Timedelta(hours=4)


def klines(symbol, limit=600):
    url = f'https://data-api.binance.vision/api/v3/klines?symbol={symbol}&interval=4h&limit={limit}'
    rows = json.load(urllib.request.urlopen(url, timeout=30))
    df = pd.DataFrame(rows).iloc[:, :6]
    df.columns = ['t', 'Open', 'High', 'Low', 'Close', 'Volume']
    df['t'] = pd.to_datetime(df.t, unit='ms', utc=True)
    df = df.set_index('t').astype(float)
    now = pd.Timestamp.now(tz='UTC')
    return df[df.index + BAR <= now]                       # closed bars only


def load_state(path):
    if path.exists():
        return json.loads(path.read_text())
    return {'prereg_sha256': PREREG_HASH, 'symbols': {}}


def step(symbol, bars, sym_state, trades, events, start):
    """Advance one symbol through every closed bar not yet processed. Idempotent."""
    sig = prepare(bars, P['hma_fast'], P['hma_slow'], P['rsi_len'], P['rsi_min'], P['linreg_len'])
    last = pd.Timestamp(sym_state['last_bar']) if sym_state.get('last_bar') else None
    for t, row in sig.iterrows():
        if t < start or (last is not None and t <= last):
            continue
        close = float(row.Close)
        if sym_state.get('position') is None and row.entry_long == 1:
            sym_state['position'] = {'entry_time': t.isoformat(), 'entry_price': close}
            events.append({'event': 'entry', 'symbol': symbol, 'bar': t.isoformat(), 'price': close})
        elif sym_state.get('position') is not None and row.exit_long == 1:
            pos = sym_state.pop('position')
            ret = close / pos['entry_price'] * (1 - FEE) ** 2 - 1
            trades.append({'symbol': symbol, **pos, 'exit_time': t.isoformat(), 'exit_price': close,
                           'bars': int((t - pd.Timestamp(pos['entry_time'])) / BAR), 'net_return': round(ret, 6)})
            sym_state['position'] = None
            events.append({'event': 'exit', 'symbol': symbol, 'bar': t.isoformat(), 'price': close, 'net_return': ret})
        sym_state['last_bar'] = t.isoformat()
    return sym_state


def run_once(out):
    out.mkdir(parents=True, exist_ok=True)
    state_path = out / 'state.json'
    state = load_state(state_path)
    if state['prereg_sha256'] != PREREG_HASH:
        raise SystemExit('prereg.json changed after this test started. Start a new test in a new --out folder.')
    start = pd.Timestamp(state.setdefault('start', datetime.now(timezone.utc).isoformat()))
    trades, events = [], []
    for symbol in PREREG['symbols']:
        bars = klines(symbol)
        bars.to_csv(out / f'{symbol}_4h_recent.csv')
        state['symbols'][symbol] = step(symbol, bars, state['symbols'].get(symbol, {}), trades, events, start)
    if trades:
        path = out / 'trades.csv'
        new = not path.exists()
        with path.open('a', newline='') as f:
            w = csv.DictWriter(f, fieldnames=list(trades[0]))
            if new:
                w.writeheader()
            w.writerows(trades)
    with (out / 'events.jsonl').open('a') as f:
        for e in events:
            f.write(json.dumps({'utc': datetime.now(timezone.utc).isoformat(), **e}) + '\n')
    state_path.write_text(json.dumps(state, indent=1))
    open_pos = {s: v.get('position') for s, v in state['symbols'].items()}
    print(json.dumps({'utc': datetime.now(timezone.utc).isoformat(), 'prereg_sha256': PREREG_HASH[:16],
                      'new_events': events, 'open_positions': open_pos}, default=str))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out', default=str(HERE / 'run'), help='Folder holding this test\'s state and logs')
    ap.add_argument('--loop', action='store_true', help='Keep running; checks every 15 minutes')
    a = ap.parse_args()
    while True:
        try:
            run_once(Path(a.out))
        except SystemExit:
            raise
        except Exception as exc:
            print(json.dumps({'utc': datetime.now(timezone.utc).isoformat(), 'error': type(exc).__name__, 'detail': str(exc)[:200]}))
        if not a.loop:
            break
        time.sleep(900)


if __name__ == '__main__':
    main()
