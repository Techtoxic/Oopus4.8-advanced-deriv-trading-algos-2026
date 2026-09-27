"""Score the forward test against exposure-matched random timing, per the frozen decision rule."""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from forward import FEE, PREREG, klines

HERE = Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out', default=str(HERE / 'run'))
    a = ap.parse_args()
    out = Path(a.out)
    state = json.loads((out / 'state.json').read_text())
    start = pd.Timestamp(state['start'])
    trades = pd.read_csv(out / 'trades.csv') if (out / 'trades.csv').exists() else pd.DataFrame()
    months = (pd.Timestamp.now(tz='UTC') - start).days / 30.4
    pct, report = [], {'start': str(start), 'months_elapsed': round(months, 1)}
    rng = np.random.default_rng(1)
    pooled_s, pooled_r = [], []
    for symbol in PREREG['symbols']:
        t = trades[trades.symbol == symbol] if len(trades) else trades
        bars = klines(symbol, 1000)
        bars = bars[bars.index >= start]
        cl = bars.Close.values
        strat = float(np.prod(1 + t.net_return) - 1) if len(t) else 0.0
        L = t.bars.astype(int).tolist() if len(t) else []
        rand = []
        if L and len(cl) > sum(L) + 1:
            for _ in range(2000):
                order = rng.permutation(L)
                cuts = np.sort(rng.integers(0, len(cl) - sum(L), len(L)))
                pos = prev = 0
                r = 1.0
                for c, l in zip(cuts, order):
                    s = pos + (c - prev)
                    pos, prev = s + l, c
                    r *= cl[min(pos, len(cl) - 1)] / cl[s] * (1 - FEE) ** 2
                rand.append(r - 1)
        rand = np.array(rand)
        report[symbol] = {'closed_trades': len(L), 'paper_return_%': round(strat * 100, 2),
                          'buy_hold_%': round((cl[-1] / cl[0] - 1) * 100, 2) if len(cl) > 1 else None,
                          'random_median_%': round(float(np.median(rand)) * 100, 2) if len(rand) else None,
                          'timing_percentile': round(float((rand < strat).mean()) * 100, 1) if len(rand) else None}
        if len(rand):
            pooled_s.append(strat)
            pooled_r.append(rand)
    if pooled_s:
        s, r = np.mean(pooled_s), np.mean(pooled_r, axis=0)
        p = float((r < s).mean()) * 100
        report['pooled_timing_percentile'] = round(p, 1)
        rule = PREREG['decision_rule']
        if months < rule['minimum_duration_months']:
            report['verdict'] = 'too early: keep running, change nothing'
        elif p >= 95 and s > np.median(r):
            report['verdict'] = 'PASS (forward evidence consistent with an edge; still small-sample)'
        elif p < 50:
            report['verdict'] = 'FAIL'
        else:
            report['verdict'] = 'inconclusive: extend, never retune'
    else:
        report['verdict'] = 'no closed trades yet'
    print(json.dumps(report, indent=1))


if __name__ == '__main__':
    main()
