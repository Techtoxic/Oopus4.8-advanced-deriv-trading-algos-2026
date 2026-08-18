"""Fresh executed digit grid for every digit-enabled symbol (OVER4/UNDER5 + the tail
contracts where the biggest edges would hide). Buys at min stake, reads payout back."""
import sys, time, json; sys.path.insert(0,'.')
from deriv_api import DerivWS
ws = DerivWS()
print("acct", ws.account.get("account_id"), ws.account.get("account_type"))
SYMS = ["JD10","JD25","JD50","JD75","JD100","R_10","R_25","R_50","R_75","R_100",
        "1HZ10V","1HZ25V","1HZ50V","1HZ75V","1HZ100V","RDBULL","RDBEAR"]
OUT = {}
for sym in SYMS:
    row = {}
    for ct,k in [("DIGITOVER","4"),("DIGITUNDER","5"),("DIGITOVER","3"),("DIGITUNDER","6"),
                 ("DIGITMATCH","0"),("DIGITEVEN",None),("DIGITOVER","7"),("DIGITUNDER","2")]:
        pr = dict(amount=1,basis="stake",contract_type=ct,currency="USD",
                  duration=1,duration_unit="t",underlying_symbol=sym)
        if k is not None: pr["barrier"]=k
        r = ws.call({"buy":1,"price":3.5,"parameters":pr})
        if "buy" in r:
            row[ct+(k or "")] = round(float(r["buy"]["payout"])/float(r["buy"]["buy_price"]),4)
        else:
            row[ct+(k or "")] = None
        time.sleep(0.25)
    OUT[sym]=row
    print(sym, json.dumps(row))
    time.sleep(0.5)
json.dump(OUT, open("/tmp/grid_audit.json","w"), indent=1)
