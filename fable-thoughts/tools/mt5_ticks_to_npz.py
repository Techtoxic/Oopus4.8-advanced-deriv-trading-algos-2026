"""mt5_ticks_to_npz.py: turn an MT5 tick export into the npz the probes read (t, p, pip).

HOW TO EXPORT IN MT5
  View > Symbols > Ticks tab > pick the symbol and dates > Request > Export.
  The file is tab-separated with a header like
      <DATE>  <TIME>  <BID>  <ASK>  <LAST>  <VOLUME>  <FLAGS>
      2026.09.30  00:00:01.123  6212.760  6213.140  ...
  MT5 leaves BID or ASK blank when that side did not change, and may write UTF-16. Both are
  handled: blanks are forward-filled and the encoding is detected.

WHAT IS WRITTEN
  t    integer epoch seconds (MT5 server time; Deriv's MT5 server runs on UTC, check yours)
  p    the BID by default. On Deriv synthetics the spread sits around the index, so bid and
       ask move together and the bid stays on the price grid; a mid can land half a pip off
       it, which the probes' integrity check flags. Use --price mid or ask to change it.
  pip  decimals of the price, read from the file
  Also saved: spread (ask - bid) per tick, because on MT5 the cost of a direction trade is
  the spread, not a payout.

ONE TICK PER SECOND
  The probes assume one tick per interval. Several MT5 rows inside the same second (a bid-only
  then an ask-only update) are collapsed to the last row in that second, and the number of
  seconds that held more than one distinct price is reported. On a 1-second synthetic that
  number should be ~0. On forex it will be large: ticks are irregular there and
  generator_probe_v3 is the wrong tool for real markets.

Run:
  python3 mt5_ticks_to_npz.py "C:/Users/HP/Downloads/DSI30_202509300000_202609300000.csv" --out ../results/DSI30_mt5.npz
  python3 generator_probe_v3.py --symbol DSI30 --cache ../results/DSI30_mt5.npz
  python3 mt5_ticks_to_npz.py --selftest
"""
import argparse
import os
import sys

import numpy as np


def sniff_encoding(path):
    with open(path, 'rb') as f:
        head = f.read(4)
    if head.startswith(b'\xff\xfe') or head.startswith(b'\xfe\xff'):
        return 'utf-16'
    if head.startswith(b'\xef\xbb\xbf'):
        return 'utf-8-sig'
    if len(head) >= 2 and head[1:2] == b'\x00':
        return 'utf-16-le'
    return 'utf-8'


def decimals(series):
    """Largest number of decimals among non-blank price strings."""
    best = 0
    for s in series:
        s = str(s).strip()
        if s and s.lower() != 'nan' and '.' in s:
            best = max(best, len(s.split('.')[1]))
    return best


def read_mt5(path, chunk=2_000_000):
    """Yield (date, time, bid, ask) string columns chunk by chunk."""
    import pandas as pd
    enc = sniff_encoding(path)
    with open(path, 'r', encoding=enc, newline='') as f:
        first = f.readline()
    sep = '\t' if '\t' in first else (';' if ';' in first else ',')
    cols = [c.strip().strip('<>').upper() for c in first.strip().split(sep)]
    need = {'DATE', 'TIME', 'BID', 'ASK'}
    if not need <= set(cols):
        sys.exit(f"header {cols} does not have {sorted(need)}; is this an MT5 *tick* export?")
    it = pd.read_csv(path, sep=sep, encoding=enc, header=0, names=cols, dtype=str,
                     usecols=['DATE', 'TIME', 'BID', 'ASK'], chunksize=chunk,
                     keep_default_na=False)
    for df in it:
        yield df


def convert(path, price='bid'):
    import pandas as pd
    parts, pip = [], 0
    last_bid = last_ask = np.nan
    for df in read_mt5(path):
        if pip == 0:
            pip = max(decimals(df['BID'].head(5000)), decimals(df['ASK'].head(5000)))
        ts = pd.to_datetime(df['DATE'] + ' ' + df['TIME'], format='%Y.%m.%d %H:%M:%S.%f',
                            errors='coerce')
        miss = ts.isna()
        if miss.any():   # some exports have no milliseconds
            ts[miss] = pd.to_datetime(df['DATE'][miss] + ' ' + df['TIME'][miss],
                                      format='%Y.%m.%d %H:%M:%S', errors='coerce')
        bid = pd.to_numeric(df['BID'].replace('', np.nan), errors='coerce')
        ask = pd.to_numeric(df['ASK'].replace('', np.nan), errors='coerce')
        # forward-fill across chunk boundaries
        if bid.isna().iloc[0] and not np.isnan(last_bid):
            bid.iloc[0] = last_bid
        if ask.isna().iloc[0] and not np.isnan(last_ask):
            ask.iloc[0] = last_ask
        bid, ask = bid.ffill(), ask.ffill()
        last_bid, last_ask = bid.iloc[-1], ask.iloc[-1]
        # explicit unit: pandas 2 may hold datetimes in s/ms/us, and astype('int64') then
        # returns that unit, which once shrank a year of ticks into 201 'seconds'
        ms = ts.to_numpy(dtype='datetime64[ms]').astype('int64')
        ok = ts.notna().values                      # unparseable times are dropped
        parts.append(pd.DataFrame({'ms': ms[ok], 'bid': bid.values[ok], 'ask': ask.values[ok]})
                     .dropna())
    d = pd.concat(parts, ignore_index=True)
    d = d[d['ms'] > 0].sort_values('ms', kind='stable')
    d['sec'] = d['ms'] // 1000
    px = {'bid': d['bid'], 'ask': d['ask'], 'mid': (d['bid'] + d['ask']) / 2}[price]
    d['px'] = px.round(pip + (1 if price == 'mid' else 0))
    multi = int((d.groupby('sec')['px'].nunique() > 1).sum())
    last = d.groupby('sec', sort=True).last()
    return dict(t=last.index.values.astype(np.int64), p=last['px'].values.astype(float),
                pip=int(pip), spread=(last['ask'] - last['bid']).values.astype(float),
                rows=len(d), multi_price_seconds=multi)


def summarize(r):
    t, sp = r['t'], r['spread']
    dt = np.diff(t)
    iv = int(np.median(dt)) if len(dt) else 0
    print(f"rows read            {r['rows']:,}")
    print(f"ticks written        {len(t):,} (one per second)")
    print(f"span                 {(t[-1] - t[0]) / 86400:.1f} days")
    print(f"median interval      {iv}s; off-interval steps {int((dt != iv).sum()):,}; "
          f"largest gap {int(dt.max()) if len(dt) else 0}s")
    print(f"seconds with >1 price {r['multi_price_seconds']:,}"
          + ("  <- irregular ticks: not a fixed-interval generator; v3 does not apply"
             if r['multi_price_seconds'] > 0.01 * len(t) else ""))
    print(f"pip decimals         {r['pip']}")
    q = np.percentile(sp, [5, 50, 95])
    print(f"spread (ask-bid)     p5 {q[0]:.{r['pip']}f}  median {q[1]:.{r['pip']}f}  "
          f"p95 {q[2]:.{r['pip']}f}  <- the round-trip cost of a direction trade on MT5")


def selftest():
    import tempfile
    txt = ("<DATE>\t<TIME>\t<BID>\t<ASK>\t<LAST>\t<VOLUME>\t<FLAGS>\n"
           "2026.09.30\t00:00:00.100\t100.10\t100.50\t\t\t6\n"
           "2026.09.30\t00:00:01.050\t100.20\t\t\t\t2\n"
           "2026.09.30\t00:00:01.900\t\t100.60\t\t\t4\n"
           "2026.09.30\t00:00:02.000\t100.00\t100.40\t\t\t6\n")
    for enc in ('utf-8', 'utf-16'):
        with tempfile.NamedTemporaryFile('wb', suffix='.csv', delete=False) as f:
            f.write(txt.encode(enc))
            path = f.name
        r = convert(path)
        os.unlink(path)
        assert list(r['t']) == [1790726400, 1790726401, 1790726402], r['t']
        assert list(r['p']) == [100.10, 100.20, 100.00], r['p']
        assert np.allclose(r['spread'], [0.40, 0.40, 0.40]), r['spread']
        assert r['pip'] == 2 and r['multi_price_seconds'] == 0
    print('selftest ok')


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('csv', nargs='?')
    ap.add_argument('--out', default=None, help='npz path (default: next to the csv)')
    ap.add_argument('--price', choices=('bid', 'ask', 'mid'), default='bid')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.csv:
        ap.error('give the exported csv')
    r = convert(a.csv, a.price)
    out = a.out or os.path.splitext(a.csv)[0] + '.npz'
    np.savez_compressed(out, t=r['t'], p=r['p'], pip=r['pip'], spread=r['spread'])
    summarize(r)
    print(f"saved {out}")


if __name__ == '__main__':
    main()
