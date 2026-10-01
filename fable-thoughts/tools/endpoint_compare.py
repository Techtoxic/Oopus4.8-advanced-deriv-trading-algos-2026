"""endpoint_compare.py — public WS proposal vs authenticated WS proposal, same contract.

WHY
payout_audit.py does this:

    ws = DerivWS(token="")      # PUBLIC websocket  -> proposal
    tr = DerivWS()              # AUTHENTICATED ws  -> buy

Two DIFFERENT sessions. So every "PROPOSAL LIES (1.886 vs 1.343)" row may simply
be the public tier quoting a different (uncut) book than the authenticated tier.
payout_probe.py already showed the authenticated tier quotes exactly what it
fills (8/8 same-tick pairs, ratio 1.0000). This test closes the loop: quote the
SAME contract on both tiers back to back and diff them.

If the public tier quotes the intact grid while the authenticated tier quotes the
cut grid, then the audit's headline is a tier artifact, not a lying endpoint, and
the real finding is that Deriv cuts the digit book on the authenticated JD100
session only.

Run: python3 endpoint_compare.py --symbol JD100
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from deriv_api import DerivWS


def grid(ws, sym, stake=1.0):
    out = {}
    errs = {}
    for ct, rng in (('DIGITOVER', range(0, 9)), ('DIGITUNDER', range(1, 10))):
        for b in rng:
            r = ws.call({'proposal': 1, 'amount': stake, 'basis': 'stake',
                         'currency': 'USD', 'underlying_symbol': sym,
                         'contract_type': ct, 'barrier': str(b),
                         'duration': 1, 'duration_unit': 't'})
            p = r.get('proposal')
            if p:
                out[f'{ct}{b}'] = round(float(p['payout']) / stake, 4)
            else:
                errs[f'{ct}{b}'] = r.get('error', {}).get('message', 'n/a')
    return out, errs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--symbol', default='JD100')
    a = ap.parse_args()

    pub = DerivWS(token='')
    auth = DerivWS()
    acct = getattr(auth, 'account', {})
    print(f"public WS : {getattr(pub, 'token', '') or 'unauthenticated'}")
    print(f"auth WS   : account {acct.get('account_id')}"
          f" type {acct.get('account_type')}")
    print()

    gpub, epub = grid(pub, a.symbol)
    gauth, eauth = grid(auth, a.symbol)
    pub.close()
    auth.close()

    print(f"{a.symbol}: contract   public   auth   verdict")
    print('-' * 62)
    diffs = 0
    for k in sorted(set(gpub) | set(gauth)):
        vp = gpub.get(k)
        va = gauth.get(k)
        if vp is not None and va is not None:
            if abs(vp - va) > 1e-9:
                verdict = f'DIFFERENT by {(vp-va):+.4f}'
                diffs += 1
            else:
                verdict = 'same'
        elif vp is None and va is not None:
            verdict = 'public DELISTED, auth live'
            diffs += 1
        elif va is None and vp is not None:
            verdict = 'auth DELISTED, public live'
            diffs += 1
        else:
            verdict = 'both delisted'
        print(f"          {k:<11} {str(vp):>7} {str(va):>7}   {verdict}")

    print()
    print(f"public errors: {epub}")
    print(f"auth   errors: {eauth}")
    print()
    print(f"differing rows: {diffs}")
    if diffs:
        print("  => the two tiers do NOT quote the same book. payout_audit.py")
        print("     compared a public proposal against an authenticated fill and")
        print("     called it a lie. The honest statement is: the public tier")
        print("     quotes the old grid, the authenticated tier quotes the")
        print("     restricted one, and the fill matches the authenticated quote.")
    else:
        print("  => both tiers agree; the audit's gap came from the tick-state")
        print("     shift and the in-flight repricing instead.")


if __name__ == '__main__':
    main()
