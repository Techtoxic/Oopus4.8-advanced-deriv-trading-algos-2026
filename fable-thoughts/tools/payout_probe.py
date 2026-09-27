"""payout_probe.py — paired proposal vs EXECUTED payout at matched tick state.

WHY
results/payout_audit.md records DIGITOVER4 on JD100 quoting 1.8857 from the
proposal endpoint and filling 1.3429. That was read as "PROPOSAL LIES". But
`payout_audit.py` runs proposal-then-buy in a loop and those are TWO DIFFERENT
TICKS. JD100 digit payouts are state-dependent: the next spot is a few pips from
the current one, so the conditional digit distribution — and therefore the fair
payout — depends on the CURRENT last digit. A proposal quoted while the last
digit is 4 is not the price of a buy that lands while the last digit is 6.

So the audit's conclusion needs a control it never ran: fetch the proposal and
the buy on the SAME tick state, and record enough to prove it.

METHOD
For each probe, in one atomic block:
  1. read the latest tick (epoch, spot, digit)
  2. pull a proposal for the target contract, recording spot_time
  3. immediately buy at min stake, recording purchase_time and the contracted
     payout (the shortcode embeds it: DIGITOVER_JD100_0.47_<epoch>_1T_4_0)
Then classify:
  - same epoch  -> SAME-TICK pair, the only comparison that tests the endpoint
  - epoch+1     -> SHIFTED pair, a tick elapsed and the prices are for
                   different states

Run: python3 payout_probe.py --symbol JD100 --barrier 4 --n 8
     python3 payout_probe.py --symbol 1HZ100V --barrier 4 --n 5
"""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from deriv_api import DerivWS

PIP = {'JD100': 2, 'JD10': 2, 'JD25': 2, 'JD50': 2, 'JD75': 2,
       '1HZ100V': 2, '1HZ10V': 2, '1HZ25V': 2, '1HZ50V': 2, '1HZ75V': 2,
       'R_10': 3, 'R_25': 3, 'R_50': 4, 'R_75': 4, 'R_100': 2}


def last_tick(ws, symbol):
    res = ws.call({'ticks_history': symbol, 'count': 1, 'end': 'latest',
                   'style': 'ticks'})
    h = res.get('history', {})
    if not h.get('times'):
        return None, None
    return h['times'][-1], float(h['prices'][-1])



def probe(ws, symbol, ctype, barrier, stake):
    tick_epoch, tick_spot = last_tick(ws, symbol)
    params = dict(amount=stake, basis='stake', currency='USD',
                  underlying_symbol=symbol, contract_type=ctype,
                  duration=1, duration_unit='t')
    if barrier is not None:
        params['barrier'] = str(barrier)

    prop = ws.call({'proposal': 1, **params})
    p = prop.get('proposal')
    if not p:
        return {'probe': 'error',
                'why': prop.get('error', {}).get('message', 'no proposal')}
    prop_payout = float(p['payout'])
    prop_spot_time = p.get('spot_time')
    prop_spot = float(p.get('spot') or 0.0)

    buy = ws.call({'buy': 1, 'price': stake, 'parameters': params})
    b = buy.get('buy')
    if not b:
        return {'probe': 'error',
                'why': buy.get('error', {}).get('message', 'no buy')}
    ex_payout = float(b['payout'])
    purchase = b.get('purchase_time')

    pip = PIP.get(symbol, 2)
    return {
        'probe': 'ok',
        'tick_epoch': tick_epoch,
        'tick_spot': tick_spot,
        'tick_digit': (int(round(tick_spot * 10 ** pip)) % 10
                       if tick_spot else None),
        'prop_spot_time': prop_spot_time,
        'prop_spot': prop_spot,
        'prop_digit': (int(round(prop_spot * 10 ** pip)) % 10
                       if prop_spot else None),
        'prop_payout': prop_payout,
        'prop_ratio': prop_payout / stake,
        'exec_payout': ex_payout,
        'exec_ratio': ex_payout / stake,
        'purchase_time': purchase,
        'same_epoch': prop_spot_time == purchase,
        'tick_delta': ((purchase - prop_spot_time)
                       if (prop_spot_time and purchase) else None),
        'shortcode': b.get('shortcode'),
        'contract_id': b.get('contract_id'),
    }



def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--symbol', default='JD100')
    ap.add_argument('--contract', default='DIGITOVER')
    ap.add_argument('--barrier', default='4')
    ap.add_argument('--n', type=int, default=8)
    ap.add_argument('--stake', type=float, default=0.35)
    ap.add_argument('--json-out', default='')
    a = ap.parse_args()

    barrier = None if a.barrier.lower() in ('none', '') else a.barrier
    ws = DerivWS()
    acct = getattr(ws, 'account', {})
    print(f"[*] account {acct.get('account_id')} type {acct.get('account_type')}"
          f" balance {acct.get('balance')}")
    print(f"[*] probing {a.contract} {barrier} on {a.symbol}, n={a.n},"
          f" stake ${a.stake}")
    print()
    print(f"{'#':>2} {'proposal':>9} {'executed':>9} {'ratio':>6} {'tick d':>6}"
          f" {'prop dig':>8} {'exec dig':>8}  verdict")
    print('-' * 76)

    rows = []
    for i in range(1, a.n + 1):
        r = probe(ws, a.symbol, a.contract, barrier, a.stake)
        rows.append(r)
        if r['probe'] != 'ok':
            print(f"{i:>2} buy/proposal failed: {r['why']}")
            time.sleep(1.0)
            continue
        ratio = r['exec_payout'] / r['prop_payout']
        if r['same_epoch']:
            verdict = ('SAME-TICK: MATCH' if ratio > 0.999
                       else f'SAME-TICK: ENDPOINT LIES {ratio:.4f}')
        else:
            verdict = f'SHIFTED by {r["tick_delta"]}t'
        print(f"{i:>2} {r['prop_payout']:>9.4f} {r['exec_payout']:>9.4f}"
              f" {ratio:>6.3f} {str(r['tick_delta']):>6} {str(r['prop_digit']):>8}"
              f" {str(r['tick_digit']):>8}  {verdict}")
        time.sleep(0.4)

    ok = [r for r in rows if r['probe'] == 'ok']
    same = [r for r in ok if r['same_epoch']]
    print()
    print(f"probes ok: {len(ok)}/{len(rows)}   same-tick pairs: {len(same)}")
    if same:
        ratios = [r['exec_payout'] / r['prop_payout'] for r in same]
        print(f"same-tick executed/proposal ratio: min {min(ratios):.4f}"
              f" max {max(ratios):.4f}")
        if min(ratios) > 0.999:
            print("  => on a matched tick the endpoint does NOT lie. The audit's")
            print("     'PROPOSAL LIES' rows are tick-state artifacts: JD100 digit")
            print("     payouts are state-dependent and the loop compared two")
            print("     different ticks.")
        else:
            print("  => the endpoint understates even at a matched tick.")
    else:
        print("  => no same-tick pair captured; increase --n. Cannot separate")
        print("     an endpoint lie from a tick-state shift yet.")

    ws.close()
    if a.json_out:
        with open(a.json_out, 'w') as f:
            json.dump(rows, f, indent=2)
        print(f"[*] wrote {a.json_out}")


if __name__ == '__main__':
    main()
