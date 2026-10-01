"""Read-only: compare contract terms quoted publicly vs to one or more logged-in accounts.

Places no orders. Tokens come only from environment variables named on the command line.
Example:  python3 terms_compare.py DERIV_TOKEN DERIV_TOKEN_B
"""
import json
import sys

from deriv_api import DerivWS

REQUESTS = {
    'JD100 UNDER5': {'contract_type': 'DIGITUNDER', 'barrier': '5', 'duration': 1, 'duration_unit': 't', 'underlying_symbol': 'JD100'},
    'JD100 EVEN': {'contract_type': 'DIGITEVEN', 'duration': 1, 'duration_unit': 't', 'underlying_symbol': 'JD100'},
    'R_100 UNDER5': {'contract_type': 'DIGITUNDER', 'barrier': '5', 'duration': 1, 'duration_unit': 't', 'underlying_symbol': 'R_100'},
    'R_50 UNDER5': {'contract_type': 'DIGITUNDER', 'barrier': '5', 'duration': 1, 'duration_unit': 't', 'underlying_symbol': 'R_50'},
    'CRASH1000 ACCU4': {'contract_type': 'ACCU', 'growth_rate': 0.04, 'underlying_symbol': 'CRASH1000'},
    'CRASH500 ACCU4': {'contract_type': 'ACCU', 'growth_rate': 0.04, 'underlying_symbol': 'CRASH500'},
    'BOOM1000 ACCU4': {'contract_type': 'ACCU', 'growth_rate': 0.04, 'underlying_symbol': 'BOOM1000'},
}


def terms(client):
    row = {}
    for name, request in REQUESTS.items():
        reply = client._call({'proposal': 1, 'amount': 10, 'basis': 'stake', 'currency': 'USD', **request})
        quote = reply.get('proposal')
        if not quote:
            row[name] = (reply.get('error') or {}).get('code')
        elif request['contract_type'] == 'ACCU':
            row[name] = quote.get('contract_details', {}).get('tick_size_barrier')
        else:
            row[name] = round(quote['payout'] / quote['ask_price'], 4)
    return row


def main():
    import os
    columns = {'public': DerivWS(token='', timeout=8)}
    for variable in sys.argv[1:]:
        token = os.environ.get(variable)
        if not token:
            raise SystemExit(f'{variable} is not set')
        for kind in ('demo', 'real'):
            try:
                columns[f'{variable}:{kind}'] = DerivWS(token=token, timeout=8, account_type=kind)
            except RuntimeError:
                pass
    try:
        table = {label: terms(client) for label, client in columns.items()}
    finally:
        for client in columns.values():
            client.ws.close()
    width = max(map(len, REQUESTS)) + 2
    print('contract'.ljust(width) + ''.join(label.ljust(22) for label in table))
    for name in REQUESTS:
        print(name.ljust(width) + ''.join(str(table[label][name]).ljust(22) for label in table))
    print(json.dumps(table))


if __name__ == '__main__':
    main()
